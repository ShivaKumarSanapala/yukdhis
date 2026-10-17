"""Application state: runs, reports, HITL actions, worklists, audit, outbox.

SQLite locally, Cloud SQL for PostgreSQL (``CLOUDSQL_APP_DB``) on GCP.
Worklists hold opaque loan/customer references only; they let the LLM refer
to "the 12 loans" by ``worklist_id`` without ever seeing the rows.
Assistant conversations and messages (with each answer's thinking/tool trace)
are always read and written with the owning tenant + user in the predicate.
New tables are created by ``metadata.create_all`` on existing databases. On Postgres only the creating
identity can use a new table, so the creator grants it to ``APP_DB_GRANTEES`` (the API and action-engine users).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (Column, Index, Integer, MetaData, String, Table, Text, create_engine, delete, func, insert, select,
                        text, update)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from core.config import Settings, get_settings

log = logging.getLogger("yukdhis.app_store")

metadata = MetaData()
runs = Table("runs", metadata,
             Column("run_id", String(64), primary_key=True), Column("tenant_id", String(64)),
             Column("status", String(32)), Column("mode", String(32)), Column("document", Text),
             Column("report_id", String(64)), Column("error", Text), Column("created_at", String(40)),
             Column("updated_at", String(40)))
run_events = Table("run_events", metadata,
                   Column("id", Integer, primary_key=True, autoincrement=True), Column("run_id", String(64), index=True),
                   Column("seq", Integer), Column("ts", String(40)), Column("stage", String(64)),
                   Column("kind", String(32)), Column("payload", Text))
reports = Table("reports", metadata,
                Column("report_id", String(64), primary_key=True), Column("tenant_id", String(64)),
                Column("policy_id", String(256)), Column("created_at", String(40)), Column("body", Text))
actions = Table("actions", metadata,
                Column("action_id", String(64), primary_key=True), Column("report_id", String(64), index=True),
                Column("status", String(32)), Column("body", Text), Column("decided_by", String(256)),
                Column("decided_at", String(40)))
worklists = Table("worklists", metadata,
                  Column("worklist_id", String(64), primary_key=True), Column("rule_id", String(256)),
                  Column("created_at", String(40)), Column("items", Text))
audit_log = Table("audit_log", metadata,
                  Column("id", Integer, primary_key=True, autoincrement=True), Column("ts", String(40)),
                  Column("tenant_id", String(64)), Column("actor", String(128)), Column("tool", String(128)),
                  Column("llm_intent", Text), Column("executed_query", Text), Column("result_summary", Text))
outbox = Table("outbox", metadata,
               Column("id", Integer, primary_key=True, autoincrement=True), Column("action_id", String(64), index=True),
               Column("channel", String(32)), Column("recipient", String(256)), Column("body", Text),
               Column("created_at", String(40)))
processed_objects = Table("processed_objects", metadata,
                          Column("uri", String(1024), primary_key=True), Column("run_id", String(64)),
                          Column("created_at", String(40)))
# Assistant chat memory: one ADK session per conversation (session_id), every row scoped to tenant + user.
conversations = Table("conversations", metadata,
                      Column("conversation_id", String(64), primary_key=True), Column("tenant_id", String(64)),
                      Column("user_id", String(128)), Column("title", String(200)), Column("session_id", String(128)),
                      Column("created_at", String(40)), Column("updated_at", String(40)),
                      Index("ix_conversations_owner", "tenant_id", "user_id", "updated_at"))
chat_messages = Table("chat_messages", metadata,
                      Column("id", Integer, primary_key=True, autoincrement=True),
                      Column("message_id", String(64), unique=True), Column("conversation_id", String(64), index=True),
                      Column("tenant_id", String(64)), Column("user_id", String(128)), Column("role", String(16)),
                      Column("content", Text), Column("trace", Text), Column("status", String(16)),
                      Column("created_at", String(40)))
CHAT_STATUSES = {"complete", "stopped", "error"}
# Run metadata lives beside ``runs`` (not as new columns) so existing databases need no ALTER TABLE; old runs have no row.
# ``source`` and ``checkpoint`` are server-only: the full source (with local paths) and the paused state for review.
run_meta = Table("run_meta", metadata,
                 Column("run_id", String(64), primary_key=True), Column("tenant_id", String(64)),
                 Column("owner_id", String(128)), Column("created_by", String(256)), Column("source_kind", String(16)),
                 Column("filename", String(512)), Column("doc_sha256", String(64)), Column("current_stage", String(64)),
                 Column("started_at", String(40)), Column("finished_at", String(40)), Column("queued_position", Integer),
                 Column("as_of", String(16)), Column("review_rules", Integer), Column("rerun_of", String(64)),
                 Column("heartbeat_at", Integer), Column("rules", Text), Column("source", Text), Column("checkpoint", Text),
                 Index("ix_run_meta_doc", "tenant_id", "doc_sha256"), Index("ix_run_meta_owner", "tenant_id", "owner_id"))
# Cross-instance lease that serializes runs of the same document (tenant + content hash); expires unless refreshed.
doc_locks = Table("doc_locks", metadata,
                  Column("lock_key", String(160), primary_key=True), Column("run_id", String(64)),
                  Column("expires_at", Integer))
RUN_COLUMNS = {"status", "mode", "document", "report_id", "error"}
META_COLUMNS = {c.name for c in run_meta.columns} - {"run_id", "tenant_id"}
PRIVATE_META = {"owner_id", "source", "checkpoint", "heartbeat_at"}
ACTIVE_RUN_STATUSES = ("QUEUED", "RUNNING", "CANCEL_REQUESTED")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_ms() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _json(text: str | None, default: Any) -> Any:
    try:
        value = json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


SOURCE_KINDS = {"upload": "upload", "sample": "sample", "text": "text", "url": "url", "watcher": "watcher"}


def _meta_values(values: dict) -> dict:
    out = {k: v for k, v in values.items() if k in META_COLUMNS}
    for key in ("rules", "source", "checkpoint"):
        if out.get(key) is not None and not isinstance(out[key], str):
            out[key] = json.dumps(out[key], default=str)
    if "review_rules" in out:
        out["review_rules"] = int(bool(out["review_rules"]))
    return out


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def share_owned_objects(engine: Engine, grantees: tuple[str, ...]) -> list[str]:
    """Postgres: grant tables/sequences this identity owns in ``public`` to the other app users. Idempotent.

    Whichever service starts first creates new tables and owns them; without this the other one gets
    "permission denied". Failures are logged, not raised, so a missing role never blocks startup.
    """
    if engine.dialect.name != "postgresql" or not grantees:
        return []
    with engine.connect() as conn:
        me = conn.execute(text("SELECT current_user")).scalar_one()
        owned = conn.execute(text(
            "SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'S') AND pg_get_userbyid(c.relowner) = current_user")).all()
    granted = []
    for grantee in (g for g in grantees if g != me):
        for name, kind in owned:
            statement = f"GRANT ALL ON {'SEQUENCE' if kind == 'S' else 'TABLE'} {_quote(name)} TO {_quote(grantee)}"
            try:
                with engine.begin() as conn:
                    conn.execute(text(statement))
                granted.append(f"{name}->{grantee}")
            except Exception as error:  # noqa: BLE001 - startup must not fail on a missing role
                log.warning("Could not grant %s to %s: %s", name, grantee, type(error).__name__)
    if granted:
        log.info("Shared %d app DB objects with %s", len(granted), ", ".join(g for g in grantees if g != me))
    return granted


class AppStore:
    def __init__(self, settings: Settings | None = None, url: str | None = None) -> None:
        settings = settings or get_settings()
        if url:
            self.engine = create_engine(url)
        elif settings.memory_backend == "gcp" and settings.cloudsql_instance:
            from bank_mcp_server.operational_store import _cloudsql_engine

            self.engine = _cloudsql_engine(settings, settings.cloudsql_app_db)
        else:
            settings.runtime_dir.mkdir(parents=True, exist_ok=True)
            self.engine = create_engine(f"sqlite:///{settings.runtime_dir / 'app.db'}",  # parallel runs write from threads
                                        connect_args={"check_same_thread": False, "timeout": 30})
        metadata.create_all(self.engine)
        share_owned_objects(self.engine, settings.app_db_grantees)
        self.tenant_id = settings.tenant_id
        self._seq_lock = threading.Lock()
        self._seq: dict[str, int] = {}

    # runs ---------------------------------------------------------------
    def create_run(self, mode: str, document: dict, meta: dict | None = None) -> str:
        run_id, ts = new_id("run"), now()
        with self.engine.begin() as conn:
            conn.execute(insert(runs).values(run_id=run_id, tenant_id=self.tenant_id, status="QUEUED", mode=mode,
                                             document=json.dumps(document), created_at=ts, updated_at=ts))
            conn.execute(insert(run_meta).values(run_id=run_id, tenant_id=self.tenant_id, heartbeat_at=int(time.time()),
                                                 **_meta_values(meta or {})))
        return run_id

    def _write_meta(self, conn: Any, run_id: str, values: dict) -> None:
        if not values:
            return
        if conn.execute(update(run_meta).where(run_meta.c.run_id == run_id).values(**values)).rowcount == 0:
            conn.execute(insert(run_meta).values(run_id=run_id, tenant_id=self.tenant_id, **values))  # run predates run_meta

    def update_run(self, run_id: str, **values: Any) -> None:
        unknown = set(values) - RUN_COLUMNS - META_COLUMNS
        if unknown:
            raise ValueError(f"Unknown run fields: {sorted(unknown)}")
        with self.engine.begin() as conn:
            conn.execute(update(runs).where(runs.c.run_id == run_id, runs.c.tenant_id == self.tenant_id)
                         .values(updated_at=now(), **{k: v for k, v in values.items() if k in RUN_COLUMNS}))
            self._write_meta(conn, run_id, _meta_values(values))

    def transition_run(self, run_id: str, expected: tuple[str, ...] | set[str], status: str, **values: Any) -> bool:
        """Compare-and-set on status (shared by every instance); False when the run was not in ``expected``."""
        with self.engine.begin() as conn:
            result = conn.execute(update(runs).where(runs.c.run_id == run_id, runs.c.tenant_id == self.tenant_id,
                                                     runs.c.status.in_(list(expected)))
                                  .values(status=status, updated_at=now(), **{k: v for k, v in values.items() if k in RUN_COLUMNS}))
            if result.rowcount != 1:
                return False
            self._write_meta(conn, run_id, _meta_values(values))
        return True

    def run_status(self, run_id: str) -> str | None:
        with self.engine.connect() as conn:
            return conn.execute(select(runs.c.status).where(runs.c.run_id == run_id, runs.c.tenant_id == self.tenant_id)).scalar()

    def heartbeat(self, run_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(run_meta).where(run_meta.c.run_id == run_id).values(heartbeat_at=int(time.time())))

    def _run_query(self):
        return (select(runs, *[run_meta.c[c] for c in sorted(META_COLUMNS)])
                .select_from(runs.outerjoin(run_meta, run_meta.c.run_id == runs.c.run_id))
                .where(runs.c.tenant_id == self.tenant_id))

    def _previous(self, conn: Any, shas: set[str]) -> dict[str, list[dict]]:
        if not shas:
            return {}
        rows = conn.execute(select(run_meta.c.doc_sha256, runs.c.run_id, runs.c.report_id, runs.c.created_at)
                            .join(runs, runs.c.run_id == run_meta.c.run_id)
                            .where(run_meta.c.tenant_id == self.tenant_id, run_meta.c.doc_sha256.in_(sorted(shas)))
                            .order_by(runs.c.created_at.desc(), runs.c.run_id.desc()).limit(50 * len(shas))).mappings().all()
        out: dict[str, list[dict]] = {}
        for r in rows:
            out.setdefault(r["doc_sha256"], []).append(dict(r))
        return out

    @staticmethod
    def _run(row: Any, previous: dict[str, list[dict]], private: bool = False) -> dict:
        """Public run shape; fields added after the first release are read defensively (old rows have no run_meta)."""
        document = _json(row["document"], {})
        others = [p for p in previous.get(row["doc_sha256"] or "", []) if p["run_id"] != row["run_id"]]
        earlier = [p for p in others if p["report_id"] and (p["created_at"] or "") <= (row["created_at"] or "")]
        out = {k: row[k] for k in ("run_id", "status", "mode", "report_id", "error", "created_at", "updated_at")}
        out.update(
            document={k: v for k, v in document.items() if k != "user_id"},
            created_by=row["created_by"] or document.get("user_id"),
            source_kind=row["source_kind"] or SOURCE_KINDS.get(document.get("trigger") or "", "upload"),
            filename=row["filename"] or document.get("filename"), doc_sha256=row["doc_sha256"],
            current_stage=row["current_stage"], started_at=row["started_at"], finished_at=row["finished_at"],
            queued_position=row["queued_position"], as_of=row["as_of"], review_rules=bool(row["review_rules"]),
            rerun_of=row["rerun_of"], rules=_json(row["rules"], []), previous_run_ids=[p["run_id"] for p in others][:20],
            previous_report_id=earlier[0]["report_id"] if earlier else None,
            owner_id=row["owner_id"] or document.get("user_id"))  # for ownership checks; the API does not return it
        if private:
            out.update(source=_json(row["source"], {}), checkpoint=_json(row["checkpoint"], {}),
                       heartbeat_at=row["heartbeat_at"])
        return out

    def get_run(self, run_id: str, private: bool = False) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(self._run_query().where(runs.c.run_id == run_id)).mappings().first()
            if not row:
                return None
            previous = self._previous(conn, {row["doc_sha256"]} if row["doc_sha256"] else set())
        return self._run(row, previous, private)

    def list_runs(self, limit: int = 20, statuses: list[str] | None = None, owner_id: str | None = None,
                  source_kind: str | None = None) -> list[dict]:
        query = self._run_query()
        if statuses:
            query = query.where(runs.c.status.in_(statuses))
        if owner_id:
            query = query.where(run_meta.c.owner_id == owner_id)
        if source_kind:
            query = query.where(run_meta.c.source_kind == source_kind)
        with self.engine.connect() as conn:
            rows = conn.execute(query.order_by(runs.c.created_at.desc(), runs.c.run_id.desc()).limit(limit)).mappings().all()
            previous = self._previous(conn, {r["doc_sha256"] for r in rows if r["doc_sha256"]})
        return [self._run(r, previous) for r in rows]

    def count_active_runs(self, owner_id: str, stale_after_s: int = 300) -> int:
        """Active runs of one user; runs whose executor stopped heart-beating (instance gone) do not count."""
        query = (select(func.count()).select_from(runs.join(run_meta, run_meta.c.run_id == runs.c.run_id))
                 .where(runs.c.tenant_id == self.tenant_id, run_meta.c.owner_id == owner_id,
                        runs.c.status.in_(ACTIVE_RUN_STATUSES), run_meta.c.heartbeat_at >= int(time.time()) - stale_after_s))
        with self.engine.connect() as conn:
            return int(conn.execute(query).scalar() or 0)

    # document leases: serialize runs of the same document across instances ----------------
    def acquire_doc_lock(self, key: str, run_id: str, ttl_s: int) -> str | None:
        """None when ``run_id`` now holds the lease; otherwise the run id holding it ("" if it just changed hands)."""
        t = int(time.time())
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(doc_locks).values(lock_key=key, run_id=run_id, expires_at=t + ttl_s))
            return None
        except IntegrityError:
            pass
        with self.engine.begin() as conn:
            taken = conn.execute(update(doc_locks).where(doc_locks.c.lock_key == key,
                                                         (doc_locks.c.expires_at < t) | (doc_locks.c.run_id == run_id))
                                 .values(run_id=run_id, expires_at=t + ttl_s)).rowcount == 1
            holder = None if taken else conn.execute(select(doc_locks.c.run_id).where(doc_locks.c.lock_key == key)).scalar()
        return None if taken else (holder or "")

    def refresh_doc_lock(self, key: str, run_id: str, ttl_s: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(doc_locks).where(doc_locks.c.lock_key == key, doc_locks.c.run_id == run_id)
                         .values(expires_at=int(time.time()) + ttl_s))

    def release_doc_lock(self, key: str, run_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(doc_locks).where(doc_locks.c.lock_key == key, doc_locks.c.run_id == run_id))

    def add_event(self, run_id: str, stage: str, kind: str, payload: dict) -> dict:
        with self._seq_lock:
            if run_id not in self._seq:  # a resumed (reviewed) or restarted run continues after its last stored event
                with self.engine.connect() as conn:
                    last = conn.execute(select(func.max(run_events.c.seq)).where(run_events.c.run_id == run_id)).scalar()
                self._seq[run_id] = int(last or 0)
            seq = self._seq[run_id] + 1
            self._seq[run_id] = seq
        event = {"run_id": run_id, "seq": seq, "ts": now_ms(), "stage": stage, "kind": kind, "payload": payload}
        with self.engine.begin() as conn:
            conn.execute(insert(run_events).values(run_id=run_id, seq=seq, ts=event["ts"], stage=stage, kind=kind,
                                                   payload=json.dumps(payload, default=str)))
        return event

    def forget_seq(self, run_id: str) -> None:
        """Drop the cached event counter once this instance stops writing a run's events."""
        with self._seq_lock:
            self._seq.pop(run_id, None)

    def events(self, run_id: str, after: int = 0) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(run_events).where(run_events.c.run_id == run_id, run_events.c.seq > after)
                                .order_by(run_events.c.seq)).mappings().all()
        return [{"run_id": r["run_id"], "seq": r["seq"], "ts": r["ts"], "stage": r["stage"], "kind": r["kind"],
                 "payload": json.loads(r["payload"])} for r in rows]

    # reports & actions -------------------------------------------------
    def save_report(self, report: dict) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(reports).values(report_id=report["report_id"], tenant_id=self.tenant_id,
                                                policy_id=report["policy"]["policy_id"], created_at=now(),
                                                body=json.dumps(report, default=str)))
            for action in report.get("actions", []):
                conn.execute(insert(actions).values(action_id=action["action_id"], report_id=report["report_id"],
                                                    status=action["status"], body=json.dumps(action)))

    def get_report(self, report_id: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(reports).where(reports.c.report_id == report_id,
                                                     reports.c.tenant_id == self.tenant_id)).mappings().first()
        if not row:
            return None
        report = json.loads(row["body"])
        report["actions"] = self.list_actions(report_id)
        return report

    def list_reports(self, limit: int = 50, policy_id: str | None = None) -> list[dict]:
        query = select(reports.c.report_id, reports.c.policy_id, reports.c.created_at, reports.c.body).where(
            reports.c.tenant_id == self.tenant_id)
        if policy_id:
            query = query.where(reports.c.policy_id == policy_id)
        with self.engine.connect() as conn:
            rows = conn.execute(query.order_by(reports.c.created_at.desc()).limit(limit)).mappings().all()
        out = []
        for row in rows:
            body = json.loads(row["body"])
            out.append({"report_id": row["report_id"], "policy_id": row["policy_id"], "created_at": row["created_at"],
                        "title": body["policy"]["title"], "summary": body["summary"], "mode": body.get("mode")})
        return out

    def list_actions(self, report_id: str | None = None, status: str | None = None) -> list[dict]:
        query = select(actions)
        if report_id:
            query = query.where(actions.c.report_id == report_id)
        if status:
            query = query.where(actions.c.status == status)
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        return [{**json.loads(r["body"]), "report_id": r["report_id"], "status": r["status"],
                 "decided_by": r["decided_by"], "decided_at": r["decided_at"]} for r in rows]

    def get_action(self, action_id: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(actions).where(actions.c.action_id == action_id)).mappings().first()
        return {**json.loads(row["body"]), "report_id": row["report_id"], "status": row["status"],
                "decided_by": row["decided_by"]} if row else None

    def decide_action(self, action_id: str, status: str, decided_by: str, expected: str = "PENDING_APPROVAL") -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(update(actions).where(actions.c.action_id == action_id, actions.c.status == expected)
                                  .values(status=status, decided_by=decided_by, decided_at=now()))
        return result.rowcount == 1

    # worklists, audit, outbox -------------------------------------------
    def save_worklist(self, rule_id: str, items: list[dict]) -> str:
        worklist_id = new_id("wl")
        with self.engine.begin() as conn:
            conn.execute(insert(worklists).values(worklist_id=worklist_id, rule_id=rule_id, created_at=now(),
                                                  items=json.dumps(items)))
        return worklist_id

    def get_worklist(self, worklist_id: str) -> list[dict]:
        with self.engine.connect() as conn:
            row = conn.execute(select(worklists.c["items"]).where(worklists.c.worklist_id == worklist_id)).first()
        return json.loads(row[0]) if row else []

    def audit(self, actor: str, tool: str, llm_intent: Any, executed_query: str, result_summary: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(audit_log).values(ts=now(), tenant_id=self.tenant_id, actor=actor, tool=tool,
                                                  llm_intent=json.dumps(llm_intent, default=str),
                                                  executed_query=executed_query,
                                                  result_summary=json.dumps(result_summary, default=str)[:4000]))

    def audit_entries(self, limit: int = 100) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(audit_log).where(audit_log.c.tenant_id == self.tenant_id)
                                .order_by(audit_log.c.id.desc()).limit(limit)).mappings().all()
        return [dict(r) for r in rows]

    def add_outbox(self, action_id: str, channel: str, recipient: str, body: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(outbox).values(action_id=action_id, channel=channel, recipient=recipient, body=body,
                                               created_at=now()))

    def outbox_for(self, action_id: str) -> list[dict]:
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(outbox).where(outbox.c.action_id == action_id)).mappings()]

    def mark_processed(self, uri: str, run_id: str) -> bool:
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(processed_objects).values(uri=uri, run_id=run_id, created_at=now()))
            return True
        except Exception:  # primary-key collision: already processed
            return False

    # assistant conversations (always scoped to tenant + user; never by id alone) ----------
    def _owned(self, user_id: str):
        return (conversations.c.tenant_id == self.tenant_id) & (conversations.c.user_id == user_id)

    @staticmethod
    def _conversation(row: Any) -> dict:
        return {"conversation_id": row["conversation_id"], "title": row["title"] or "Untitled conversation",
                "session_id": row["session_id"] or row["conversation_id"], "created_at": row["created_at"],
                "updated_at": row["updated_at"] or row["created_at"]}

    def create_conversation(self, user_id: str, title: str) -> dict:
        conversation_id, ts = new_id("conv"), now_ms()
        with self.engine.begin() as conn:
            conn.execute(insert(conversations).values(conversation_id=conversation_id, tenant_id=self.tenant_id,
                                                      user_id=user_id, title=title[:200], session_id=conversation_id,
                                                      created_at=ts, updated_at=ts))
        return self.get_conversation(conversation_id, user_id) or {}

    def get_conversation(self, conversation_id: str, user_id: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(conversations).where(conversations.c.conversation_id == conversation_id,
                                                           self._owned(user_id))).mappings().first()
        return self._conversation(row) if row else None

    def list_conversations(self, user_id: str, limit: int = 100) -> list[dict]:
        counts = (select(chat_messages.c.conversation_id, func.count().label("n"))
                  .where(chat_messages.c.tenant_id == self.tenant_id, chat_messages.c.user_id == user_id)
                  .group_by(chat_messages.c.conversation_id).subquery())
        query = (select(conversations, counts.c.n).outerjoin(counts, counts.c.conversation_id == conversations.c.conversation_id)
                 .where(self._owned(user_id)).order_by(conversations.c.updated_at.desc()).limit(limit))
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        return [{**self._conversation(r), "message_count": int(r["n"] or 0)} for r in rows]

    def rename_conversation(self, conversation_id: str, user_id: str, title: str) -> dict | None:
        with self.engine.begin() as conn:
            result = conn.execute(update(conversations).where(conversations.c.conversation_id == conversation_id,
                                                              self._owned(user_id)).values(title=title[:200]))
        return self.get_conversation(conversation_id, user_id) if result.rowcount == 1 else None

    def delete_conversation(self, conversation_id: str, user_id: str) -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(delete(conversations).where(conversations.c.conversation_id == conversation_id,
                                                              self._owned(user_id)))
            if result.rowcount != 1:
                return False
            conn.execute(delete(chat_messages).where(chat_messages.c.conversation_id == conversation_id,
                                                     chat_messages.c.tenant_id == self.tenant_id,
                                                     chat_messages.c.user_id == user_id))
        return True

    def add_chat_message(self, conversation_id: str, user_id: str, role: str, content: str,
                         trace: dict | None = None, status: str = "complete") -> dict:
        """Append to a conversation the caller has already resolved via ``get_conversation``."""
        message = {"message_id": new_id("msg"), "conversation_id": conversation_id, "role": role, "content": content,
                   "trace": trace or {}, "status": status if status in CHAT_STATUSES else "complete", "created_at": now_ms()}
        with self.engine.begin() as conn:
            conn.execute(insert(chat_messages).values(
                message_id=message["message_id"], conversation_id=conversation_id, tenant_id=self.tenant_id,
                user_id=user_id, role=role, content=content, trace=json.dumps(message["trace"], default=str),
                status=message["status"], created_at=message["created_at"]))
            conn.execute(update(conversations).where(conversations.c.conversation_id == conversation_id,
                                                     self._owned(user_id)).values(updated_at=message["created_at"]))
        return message

    def chat_messages(self, conversation_id: str, user_id: str, limit: int = 500) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(chat_messages).where(
                chat_messages.c.conversation_id == conversation_id, chat_messages.c.tenant_id == self.tenant_id,
                chat_messages.c.user_id == user_id).order_by(chat_messages.c.id).limit(limit)).mappings().all()
        out = []
        for r in rows:
            trace = _json(r["trace"], {})
            out.append({"message_id": r["message_id"], "conversation_id": r["conversation_id"],
                        "role": r["role"] if r["role"] in {"user", "assistant"} else "assistant",
                        "content": r["content"] or "", "status": r["status"] or "complete", "created_at": r["created_at"],
                        "trace": {"thinking": str(trace.get("thinking") or ""),
                                  "tool_calls": [t for t in trace.get("tool_calls") or [] if isinstance(t, dict)]}})
        return out


_STORE: AppStore | None = None


def get_app_store() -> AppStore:
    global _STORE
    if _STORE is None:
        _STORE = AppStore()
    return _STORE


def set_app_store(store: AppStore | None) -> None:
    global _STORE
    _STORE = store
