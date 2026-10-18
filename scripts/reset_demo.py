"""Reset the demo to a clean slate: conversations, runs and reports, the circular text index and the Memory Bank.

    python scripts/reset_demo.py              # dry run: print what would be deleted
    python scripts/reset_demo.py --execute    # delete it

Kept on purpose: the knowledge graph (Spanner), the customer seed data (bank_ops), the audit log and the table of
objects the Cloud Storage watcher has already processed (deleting that would make the watcher re-run old files).
Run it where the app database is reachable: a Cloud Run job with the API image, service account and environment
(Cloud SQL is private-IP only).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import inspect, text  # noqa: E402

from core.app_store import get_app_store  # noqa: E402
from core.config import get_settings  # noqa: E402

# Children before parents. The last two are Google ADK's session tables (model-side history for each conversation).
APP_TABLES = ("chat_messages", "conversation_threads", "conversations", "run_events", "run_meta", "outbox", "worklists",
              "actions", "reports", "runs", "doc_locks", "circular_chunks", "events", "sessions")


def memories(settings):
    if settings.memory_backend != "gcp" or not settings.memory_bank_engine_id:
        return None, []
    import vertexai

    client = vertexai.Client(project=settings.project, location=settings.location)
    engine = f"projects/{settings.project}/locations/{settings.location}/reasoningEngines/{settings.memory_bank_engine_id}"
    return client, [m.name for m in client.agent_engines.memories.list(name=engine)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--execute", action="store_true", help="delete (default is a dry run)")
    args = parser.parse_args()
    settings = get_settings()
    engine = get_app_store().engine
    present = [t for t in APP_TABLES if t in inspect(engine).get_table_names()]
    with engine.connect() as conn:
        before = {t: conn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar() for t in present}
    client, memory_names = memories(settings)
    print("before:", {**before, "memory_bank": len(memory_names)})
    if not args.execute:
        print("dry run: nothing deleted (pass --execute)")
        return 0
    with engine.begin() as conn:
        for table in present:
            conn.execute(text(f'DELETE FROM "{table}"'))
    for name in memory_names:
        client.agent_engines.memories.delete(name=name)
    with engine.connect() as conn:
        after = {t: conn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar() for t in present}
    _, left = memories(settings)
    print("after:", {**after, "memory_bank": len(left)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
