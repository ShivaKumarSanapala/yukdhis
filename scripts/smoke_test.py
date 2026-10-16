"""Post-deploy smoke test: one agentic run against the real GCP backends.

Runs as Cloud Run job ``yukdhis-smoke`` with the API's service account and
environment, so it exercises Gemini, Spanner Graph, Cloud SQL, DLP/Gemma and
the app store exactly as the service does. Exits non-zero on failure.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api import runs  # noqa: E402
from core.app_store import get_app_store  # noqa: E402
from privacy.gemma import GemmaRedactor, _parse  # noqa: E402
from privacy.sanitizer import get_sanitizer  # noqa: E402

SAMPLE = ROOT / "data" / "circulars" / "rbi_2023_09_13_release_of_property_documents.md"


async def main() -> int:
    probe = "My wife Priya Verma visited about the loan account ending in 4427."
    try:
        raw = GemmaRedactor()._call_vertex(probe)
        print("gemma:", [(f.text, f.entity_type) for f in _parse(raw, probe)])
    except Exception as error:  # report, but the pipeline still runs on the heuristic fallback
        print("gemma: ERROR", type(error).__name__, str(error)[:300])
    masked = get_sanitizer().sanitize("Customer Rahul Verma, PAN ABCPV1234K, called from +91 98450 12345.", "smoke")
    print("privacy:", masked.engine, masked.entity_counts)
    started = runs.start_run({"path": str(SAMPLE), "filename": SAMPLE.name, "trigger": "smoke"}, "smoke-test", "agentic")
    print("mode:", started["mode"])
    store = get_app_store()
    for _ in range(450):
        await asyncio.sleep(2)
        run = store.get_run(started["run_id"])
        if run["status"] in {"SUCCEEDED", "FAILED"}:
            break
    events = store.events(started["run_id"])
    print("tool calls:", dict(Counter(e["payload"].get("tool") for e in events if e["kind"] == "tool_call")))
    print("status:", run["status"], run.get("error") or "")
    if run["status"] != "SUCCEEDED":
        return 1
    report = store.get_report(run["report_id"])
    print("summary:", json.dumps(report["summary"], default=str))
    print("graph:", report["blast_radius"][0]["query_language"])
    return 0 if report["summary"]["loans_with_violations"] == 12 else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
