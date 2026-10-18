"""FastAPI bridge for the Autonomous Compliance Engine (deployed on Cloud Run).

The React UI on Firebase Hosting calls ``/api/*`` (Hosting rewrite to this
service) and opens the run-event WebSocket directly against the Cloud Run URL.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import time
import uuid
import zipfile
from collections import defaultdict, deque
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from google.genai import types
from pydantic import BaseModel, Field

from api.adk_runtime import ASSISTANT_APP, assistant_runner, memory_service, session_service
from api.auth import User, current_user, verify_firebase_token, verify_service_caller
from api.chat import (TurnInProgress, active_turn, auto_title, build_prompt, follow, single_error, sse_response, start_turn,
                      stop_turn)
from api.runs import (PAUSED, RUN_STATUSES, TERMINAL, WATCHER_USER, RunLimitExceeded, choose_mode, ensure_capacity,
                      interrupt_local_runs, reap_stale_runs, reject_run, request_cancel, resume_run, sha256_of, start_run)
from bank_mcp_server.remediation import dispatch, request_dispatch
from core.app_store import SOURCE_KINDS, get_app_store
from core.config import DATA_DIR, ROOT, get_settings
from core.models import ExtractedPolicy, Rule
from ingestion.documents import DOCAI_TYPES, TEXT_TYPES, archive_to_gcs, document_id_for, guess_mime
from ingestion.validator import validate_extraction
from ingestion.watcher import eligible, list_incoming, source_for
from knowledge_graph.governance import describe_ontology
from knowledge_graph.mapper import review_rule
from knowledge_graph.ontology import ontology_summary
from knowledge_graph.packs import PackError
from knowledge_graph.store import get_graph_store
from knowledge_graph.traversal import blast_radius
from pipeline.deterministic import GRAPH_TRAVERSAL_LOCK
from pipeline.stages import default_as_of
from privacy.sanitizer import get_sanitizer

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("yukdhis.api")
settings = get_settings()
SAMPLES_DIR = DATA_DIR / "circulars"
ALLOWED_TYPES = TEXT_TYPES | DOCAI_TYPES

app = FastAPI(title="Yukdhis - Autonomous Compliance Engine", version="1.0.0",
              docs_url="/api/docs" if settings.auth_mode == "none" else None, redoc_url=None, openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False,
                   allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])

_hits: dict[str, deque] = defaultdict(deque)
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    started = time.perf_counter()
    inbound = request.headers.get("x-request-id", "")
    request_id = inbound if 0 < len(inbound) <= 128 and inbound.isprintable() else uuid.uuid4().hex
    request.state.request_id = request_id
    status = 500
    try:
        if request.method == "POST":
            key = request.client.host if request.client else "unknown"
            window, now = _hits[key], time.monotonic()
            while window and now - window[0] > 60:
                window.popleft()
            if len(window) >= RATE_LIMIT:
                response = JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
            else:
                window.append(now)
                response = await call_next(request)
        else:
            response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")  # SSE sets "no-store, no-transform"
        return response
    finally:  # one JSON line per request; Cloud Logging parses stdout JSON. Never log tokens, bodies or query strings.
        print(json.dumps({"severity": "ERROR" if status >= 500 else "INFO", "event": "http_request", "request_id": request_id,
                          "method": request.method, "path": request.url.path, "status": status,
                          "latency_ms": round((time.perf_counter() - started) * 1000, 1)}), flush=True)


@app.on_event("startup")
def _reap_on_startup() -> None:
    try:
        reap_stale_runs(force=True)
    except Exception:  # noqa: BLE001 - startup must not fail because the store is briefly unavailable
        log.warning("Could not reap stale runs at startup", exc_info=True)


@app.on_event("shutdown")
def _interrupt_on_shutdown() -> None:
    """Cloud Run sends SIGTERM before replacing an instance: in-process runs cannot survive it, so say so."""
    try:
        interrupt_local_runs()
    except Exception:  # noqa: BLE001
        log.warning("Could not mark local runs as interrupted", exc_info=True)


@app.exception_handler(Exception)
async def unhandled(request: Request, error: Exception):
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse({"detail": "Internal error", "path": request.url.path}, status_code=500)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/config")
def config(user: User = Depends(current_user)) -> dict:
    return {
        "tenant_id": settings.tenant_id, "user": user.email, "default_mode": choose_mode(None),
        "llm_ready": settings.is_gcp_llm_ready, "reasoning_model": settings.reasoning_model, "as_of": default_as_of(),
        "backends": {k: getattr(settings, f"{k}_backend") for k in
                     ("graph", "operational", "document", "privacy", "gemma", "dispatch", "memory")},
    }


@app.get("/api/samples")
def samples(user: User = Depends(current_user)) -> list[dict]:
    return [{"sample_id": p.name, "title": p.read_text().splitlines()[0].lstrip("# ").strip()}
            for p in sorted(SAMPLES_DIR.glob("*.md"))]


# Runs ----------------------------------------------------------------------
MAX_BATCH = 10                 # runs started by one request (files + samples + text + url)
MAX_TEXT_BYTES = 500 * 1024    # pasted circular
MAX_URL_LENGTH = 2048
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
BINARY_SIGNATURES = (b"%PDF", b"PK\x03\x04", b"\x89PNG", b"GIF8", b"\xd0\xcf\x11\xe0")
EDITABLE_RULE_FIELDS = ("title", "description", "modality", "effective_date", "penalty_risk")


def content_problem(content: bytes, mime: str) -> str | None:
    """Magic-byte check of an upload against its declared type; a user-safe reason, or None when it matches."""
    if not content.strip():
        return "The file is empty."
    if mime == "application/pdf":
        return None if content.startswith(b"%PDF") else "The file is named .pdf but its content is not a PDF."
    if mime == DOCX_TYPE:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                is_docx = content.startswith(b"PK\x03\x04") and "word/document.xml" in archive.namelist()
        except zipfile.BadZipFile:
            is_docx = False
        return None if is_docx else "The file is named .docx but its content is not a Word document."
    if content.startswith(BINARY_SIGNATURES) or b"\x00" in content[:65536]:
        return "The file is named as text, Markdown or HTML but its content is binary."
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return "Text, Markdown and HTML files must be UTF-8 encoded."
    return None


def _store_document(content: bytes, filename: str, trigger: str) -> dict:
    gcs_uri = archive_to_gcs(content, filename)
    if gcs_uri:
        return {"gcs_uri": gcs_uri, "filename": filename, "trigger": trigger}
    target = settings.runtime_dir / "uploads" / f"{document_id_for(content)}-{filename}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return {"path": str(target), "filename": filename, "display_uri": f"{trigger}://{filename}", "trigger": trigger}


async def _upload_source(file: UploadFile) -> tuple[dict, str]:
    content = await file.read(settings.max_upload_bytes + 1)
    filename = Path(file.filename or "upload.md").name[:200] or "upload.md"
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(413, f"{filename} is larger than {settings.max_upload_bytes // (1024 * 1024)} MB")
    mime = guess_mime(filename)
    if mime not in ALLOWED_TYPES:
        raise HTTPException(415, f"{filename}: upload PDF, DOCX, HTML, Markdown or text")
    if problem := content_problem(content, mime):
        raise HTTPException(400, f"{filename}: {problem}")
    return _store_document(content, filename, "upload"), sha256_of(content)


def _sample_source(sample_id: str) -> dict:
    path = SAMPLES_DIR / Path(sample_id).name
    if not path.exists() or path.suffix != ".md":
        raise HTTPException(404, "Unknown sample")
    return {"path": str(path), "filename": path.name, "display_uri": f"sample://{path.name}", "trigger": "sample"}


def _text_source(text: str) -> tuple[dict, str]:
    content = text.strip().encode("utf-8")
    if not content:
        raise HTTPException(422, "Pasted text is empty")
    if len(content) > MAX_TEXT_BYTES:
        raise HTTPException(413, f"Pasted text is larger than {MAX_TEXT_BYTES // 1024} KB")
    if b"\x00" in content:
        raise HTTPException(400, "Pasted text contains binary data")
    sha = sha256_of(content)
    return _store_document(content, f"pasted-circular-{sha[:8]}.md", "text"), sha


async def _url_source(url: str) -> dict:
    url = url.strip()
    if len(url) > MAX_URL_LENGTH or not url.lower().startswith(("https://", "http://")):
        raise HTTPException(400, "Enter a full http(s) link to the circular")
    try:
        from ingestion.fetch import fetch_circular
    except ImportError:
        raise HTTPException(501, "Fetching circulars from a link is not available on this server")
    try:
        source = await asyncio.to_thread(fetch_circular, url)
    except ValueError as error:  # user-safe message from the fetcher (host not allowed, not a circular, too large...)
        raise HTTPException(400, str(error)[:500])
    return {**source, "trigger": source.get("trigger") or "url"}


def _check_as_of(as_of: str | None) -> None:
    if as_of:
        try:
            date.fromisoformat(as_of)
        except ValueError:
            raise HTTPException(422, "as_of must be an ISO date")


def _public_run(run: dict, user: User) -> dict:
    out = {k: v for k, v in run.items() if k not in {"owner_id", "source", "checkpoint", "heartbeat_at"}}
    out["is_mine"] = run.get("owner_id") == user.user_id
    return out


def _visible_run(run_id: str, private: bool = False) -> dict:
    run = get_app_store().get_run(run_id, private=private)  # tenant-scoped
    if not run:
        raise HTTPException(404, "Unknown run")
    return run


def _my_run(run_id: str, user: User) -> dict:
    run = get_app_store().get_run(run_id, private=True)
    if not run or run.get("owner_id") != user.user_id:  # never confirm that someone else's run exists
        raise HTTPException(404, "Unknown run")
    return run


@app.post("/api/runs")
async def create_run(file: UploadFile | None = File(default=None), files: list[UploadFile] | None = File(default=None),
                     sample_id: str | None = Form(default=None), sample_ids: list[str] | None = Form(default=None),
                     text: str | None = Form(default=None), url: str | None = Form(default=None),
                     mode: str | None = Form(default=None), as_of: str | None = Form(default=None),
                     review_rules: bool = Form(default=False), user: User = Depends(current_user)) -> dict:
    """One run per item: each file, each sample, the pasted text and the URL. All items are validated first."""
    _check_as_of(as_of)
    samples = list(dict.fromkeys([s for s in [sample_id, *(sample_ids or [])] if s]))
    uploads = [f for f in [file, *(files or [])] if f is not None]
    count = len(samples) + len(uploads) + bool(text and text.strip()) + bool(url and url.strip())
    if not count:
        raise HTTPException(422, "Provide a file, a sample, pasted text or a URL")
    if count > MAX_BATCH:
        raise HTTPException(422, f"Start at most {MAX_BATCH} analyses at once")
    try:
        ensure_capacity(user.user_id, count)
    except RunLimitExceeded as error:
        raise HTTPException(429, str(error))
    items: list[tuple[dict, str, str | None]] = [(_sample_source(s), "sample", None) for s in samples]
    for upload in uploads:
        source, sha = await _upload_source(upload)
        items.append((source, "upload", sha))
    if text and text.strip():
        source, sha = _text_source(text)
        items.append((source, "text", sha))
    if url and url.strip():
        items.append((await _url_source(url), "url", None))
    started = [start_run(source, user.user_id, mode, as_of, email=user.email, source_kind=kind, review_rules=review_rules,
                         doc_sha256=sha) for source, kind, sha in items]
    get_app_store().audit(user.email, "run_start", {"runs": [r["run_id"] for r in started], "kinds": [k for _, k, _ in items],
                                                    "review_rules": review_rules, "as_of": as_of}, "START", {"count": len(started)})
    return {"run_id": started[0]["run_id"], "mode": started[0]["mode"], "run_ids": [r["run_id"] for r in started],
            "runs": started}


@app.get("/api/runs")
def runs(status: str | None = None, mine: bool = False, source_kind: str | None = None,
         limit: int = Query(default=20, ge=1, le=100), user: User = Depends(current_user)) -> list[dict]:
    """Newest first. ``status`` takes one or more comma-separated statuses; ``mine=1`` keeps the caller's runs."""
    statuses = [s.strip().upper() for s in (status or "").split(",") if s.strip()]
    if unknown := sorted(set(statuses) - RUN_STATUSES):
        raise HTTPException(422, f"Unknown status: {', '.join(unknown)}")
    if source_kind and source_kind not in SOURCE_KINDS:
        raise HTTPException(422, "Unknown source_kind")
    reap_stale_runs()  # runs orphaned by a deploy or crash show as FAILED (interrupted) instead of running forever
    rows = get_app_store().list_runs(limit, statuses or None, user.user_id if mine else None, source_kind)
    return [_public_run(r, user) for r in rows]


@app.get("/api/runs/{run_id}")
def run_status(run_id: str, after: int = 0, user: User = Depends(current_user)) -> dict:
    reap_stale_runs()
    run = _visible_run(run_id)
    return {"run": _public_run(run, user), "events": get_app_store().events(run_id, after)}


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str, user: User = Depends(current_user)) -> dict:
    """Owner only. Runs on the event loop: it may cancel this instance's task for the run."""
    run = _my_run(run_id, user)
    status = request_cancel(run)
    get_app_store().audit(user.email, "run_cancel", {"run_id": run_id}, "CANCEL", {"status": status})
    return {"run_id": run_id, "status": status}


class RunReview(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    rules: list[Rule] | None = Field(default=None, max_length=200)
    comment: str = Field(default="", max_length=1000)


def _approved_extraction(extraction: dict, edited: list[Rule] | None) -> tuple[dict, dict]:
    """Merge reviewer edits into the extracted rules. Rules can be edited (EDITABLE_RULE_FIELDS) or removed, not added;
    checks, scope and citations stay as extracted so every rule is still grounded in the circular."""
    original = ExtractedPolicy.model_validate(extraction)
    if edited is None:
        return original.model_dump(), {"edited": [], "removed": []}
    by_id = {r.rule_id: r for r in original.rules}
    merged, changed, seen = [], [], set()
    for rule in edited:
        base = by_id.get(rule.rule_id)
        if base is None:
            raise HTTPException(422, f"Unknown rule {rule.rule_id!r}: rules can be edited or removed, not added")
        if rule.rule_id in seen:
            raise HTTPException(422, f"Rule {rule.rule_id!r} is listed twice")
        seen.add(rule.rule_id)
        if not rule.title.strip():
            raise HTTPException(422, f"Rule {rule.rule_id!r} needs a title")
        updates = {f: getattr(rule, f) for f in EDITABLE_RULE_FIELDS if getattr(rule, f) != getattr(base, f)}
        updates["title"] = rule.title.strip()
        if any(updates[f] != getattr(base, f) for f in updates):
            changed.append(rule.rule_id)
        merged.append(base.model_copy(update=updates))
    if not merged:
        raise HTTPException(422, "Approve at least one rule, or reject the run")
    candidate = ExtractedPolicy(policy=original.policy, rules=merged, unexecutable=original.unexecutable)
    errors = [i for i in validate_extraction(candidate) if i.severity == "ERROR"]
    if errors:
        raise HTTPException(422, "; ".join(f"{i.rule_id or 'policy'} {i.field}: {i.message}" for i in errors)[:1000])
    return candidate.model_dump(), {"edited": changed, "removed": sorted(set(by_id) - seen)}


@app.post("/api/runs/{run_id}/review")
async def review_run(run_id: str, body: RunReview, user: User = Depends(current_user)) -> dict:
    """Owner only: approve (optionally with edited or removed rules) and continue, or reject a paused run."""
    store = get_app_store()
    run = _my_run(run_id, user)
    if run["status"] != "AWAITING_REVIEW":
        raise HTTPException(409, "Run is not awaiting review")
    if body.decision == "REJECT":
        if not reject_run(run_id):
            raise HTTPException(409, "Run is not awaiting review")
        store.add_event(run_id, "pipeline", "run_rejected", {"by": user.email, "comment": body.comment})
        store.audit(user.email, "run_review", {"run_id": run_id, "comment": body.comment}, "REJECT", {})
        return {"run_id": run_id, "status": "REJECTED"}
    extraction = (run.get("checkpoint") or {}).get("extraction")
    if not extraction:
        raise HTTPException(409, "This run has no extracted rules to review")
    approved, changes = _approved_extraction(extraction, body.rules)
    if not resume_run(run, approved):
        raise HTTPException(409, "Run is not awaiting review")
    store.add_event(run_id, "reader", "rules_reviewed", {"decision": "APPROVE", "by": user.email,
                                                         "rules": len(approved["rules"]), **changes})
    store.audit(user.email, "run_review", {"run_id": run_id, "comment": body.comment, **changes}, "APPROVE",
                {"rules": len(approved["rules"])})
    return {"run_id": run_id, "status": "QUEUED", "rules": len(approved["rules"]), **changes}


class Rerun(BaseModel):
    as_of: str | None = Field(default=None, max_length=10)
    mode: Literal["agentic", "deterministic"] | None = None
    review_rules: bool = False


@app.post("/api/runs/{run_id}/rerun")
async def rerun(run_id: str, body: Rerun, user: User = Depends(current_user)) -> dict:
    """Retry or re-run with another date. Your own runs and watcher-started runs (the new run is yours)."""
    run = get_app_store().get_run(run_id, private=True)
    if not run or run.get("owner_id") not in {user.user_id, WATCHER_USER} or not run.get("source"):
        raise HTTPException(404, "Unknown run")
    _check_as_of(body.as_of)
    try:
        ensure_capacity(user.user_id)
    except RunLimitExceeded as error:
        raise HTTPException(429, str(error))
    started = start_run(run["source"], user.user_id, body.mode or run["mode"], body.as_of or run.get("as_of"),
                        email=user.email, source_kind=run["source_kind"], review_rules=body.review_rules,
                        doc_sha256=run.get("doc_sha256"), rerun_of=run_id)
    get_app_store().audit(user.email, "run_rerun", {"run_id": run_id, **body.model_dump()}, "START", started)
    return {**started, "run_ids": [started["run_id"]], "runs": [started]}


@app.websocket("/ws/runs/{run_id}")
async def run_events(websocket: WebSocket, run_id: str):
    """First client message must be {"token": "<firebase id token>"} when auth is on (no tokens in URLs)."""
    await websocket.accept()
    try:
        if settings.auth_mode == "firebase":
            hello = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=10))
            await asyncio.to_thread(verify_firebase_token, hello.get("token", ""))
        store, seq = get_app_store(), 0
        while True:
            run = store.get_run(run_id)  # tenant-scoped
            for event in store.events(run_id, seq) if run else []:
                await websocket.send_json(event)
                seq = event["seq"]
            if not run or run["status"] in TERMINAL | PAUSED:
                for event in store.events(run_id, seq) if run else []:  # events written with the final status
                    await websocket.send_json(event)
                await websocket.send_json({"kind": "closed", "status": run["status"] if run else "UNKNOWN"})
                break
            await asyncio.sleep(0.4)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        return
    except HTTPException:
        await websocket.close(code=4401)
        return
    await websocket.close()


# Reports, actions, rules -----------------------------------------------------
@app.get("/api/reports")
def reports(user: User = Depends(current_user)) -> list[dict]:
    return get_app_store().list_reports()


@app.get("/api/reports/{report_id}")
def report(report_id: str, user: User = Depends(current_user)) -> dict:
    body = get_app_store().get_report(report_id)
    if not body:
        raise HTTPException(404, "Unknown report")
    return body


class Decision(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]
    comment: str = Field(default="", max_length=1000)


def _require_resolved_scope(store, action_id: str) -> None:
    """Approving is blocked while the action's rule scope is UNRESOLVED; old reports without scope_status stay approvable."""
    action = store.get_action(action_id)
    report = store.get_report(action["report_id"]) if action else None
    if not report:
        return
    entry = next((b for b in report.get("blast_radius", []) if b.get("rule_id") == action.get("rule_id")), None)
    if entry and entry.get("scope_status", "RESOLVED") == "UNRESOLVED":
        raise HTTPException(409, "Rule scope could not be resolved from the circular; approve is blocked until the rule is reviewed")


@app.post("/api/actions/{action_id}/decision")
def decide_action(action_id: str, body: Decision, user: User = Depends(current_user)) -> dict:
    store = get_app_store()
    if body.decision == "APPROVED":
        _require_resolved_scope(store, action_id)
    if not store.decide_action(action_id, body.decision, user.email):
        raise HTTPException(409, "Action is not pending approval")
    store.audit(user.email, "human_decision", {"action_id": action_id, "comment": body.comment}, body.decision, {})
    if body.decision == "APPROVED":
        return {"action_id": action_id, "status": "APPROVED", "dispatch": request_dispatch(action_id)}
    return {"action_id": action_id, "status": "REJECTED"}


@app.get("/api/actions/{action_id}/outbox")
def action_outbox(action_id: str, user: User = Depends(current_user)) -> list[dict]:
    return get_app_store().outbox_for(action_id)


class RuleReview(Decision):
    rule_id: str = Field(max_length=300)
    report_id: str | None = Field(default=None, max_length=100)  # report whose stored copy of the rule should show the decision


@app.post("/api/rules/review")
def rule_review(body: RuleReview, user: User = Depends(current_user)) -> dict:
    try:
        result = review_rule(get_graph_store(), body.rule_id, body.decision, user.email, date.today().isoformat())
    except KeyError:
        raise HTTPException(404, "Unknown rule")
    store = get_app_store()
    report = store.get_report(body.report_id) if body.report_id else None  # tenant-scoped lookup
    if report:
        for rule in report.get("rules", []):
            if rule.get("rule_id") == body.rule_id:
                rule["status"] = body.decision
        store.save_report(report)  # keeps the decision across reloads; decided actions are untouched
    store.audit(user.email, "rule_review", body.model_dump(), body.decision, result)
    return result


@app.get("/api/audit")
def audit(user: User = Depends(current_user)) -> list[dict]:
    return get_app_store().audit_entries()


# Graph ------------------------------------------------------------------------
@app.get("/api/graph")
def graph(as_of: str | None = None, user: User = Depends(current_user)) -> dict:
    return {"as_of": as_of or default_as_of(), **get_graph_store().snapshot(as_of or default_as_of())}


@app.get("/api/graph/ontology")
def ontology(user: User = Depends(current_user)) -> dict:
    return ontology_summary()


@app.get("/api/ontology")
def ontology_packs(user: User = Depends(current_user)) -> dict:
    """Enabled ontology packs with versions and counts, the merged vocabulary and pending LLM proposals (read-only)."""
    try:
        return describe_ontology(get_graph_store())
    except PackError as error:
        raise HTTPException(status_code=500, detail=f"Ontology packs are invalid: {error}") from error


@app.get("/api/graph/blast-radius")
def graph_blast_radius(rule_id: str, as_of: str | None = None, user: User = Depends(current_user)) -> dict:
    with GRAPH_TRAVERSAL_LOCK:  # shares the graph store's query log with running pipelines
        return blast_radius(get_graph_store(), rule_id, as_of or default_as_of()).model_dump()


# Privacy shield demo & chat ------------------------------------------------------
class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    session_id: str | None = Field(default=None, max_length=128)


@app.post("/api/privacy/sanitize")
def sanitize(body: TextIn, user: User = Depends(current_user)) -> dict:
    result = get_sanitizer().sanitize(body.text, f"{user.user_id}:{body.session_id or 'privacy-demo'}")
    return {"sanitized": result.text, "entity_counts": result.entity_counts, "engine": result.engine}


@app.post("/api/chat")
async def chat(body: TextIn, user: User = Depends(current_user)) -> dict:
    if not settings.is_gcp_llm_ready:
        raise HTTPException(503, "Chat needs Gemini on Vertex AI (set GOOGLE_CLOUD_PROJECT).")
    service = session_service()
    session = None
    if body.session_id:
        session = await service.get_session(app_name=ASSISTANT_APP, user_id=user.user_id, session_id=body.session_id)
    if session is None:
        session = await service.create_session(app_name=ASSISTANT_APP, user_id=user.user_id)
    reply, tools = "", []
    async for event in assistant_runner().run_async(user_id=user.user_id, session_id=session.id,
                                                    new_message=types.Content(role="user", parts=[types.Part(text=body.text)])):
        tools += [call.name for call in event.get_function_calls()]
        if event.is_final_response() and event.content:
            reply += "".join(p.text or "" for p in event.content.parts if not getattr(p, "thought", False))
    return {"session_id": session.id, "reply": reply.strip(), "tools_used": tools}


# Assistant conversations (streamed, persisted per user) ------------------------------
ID_PATTERN = r"^[A-Za-z0-9_.:\-]+$"


class ChatContext(BaseModel):
    report_id: str | None = Field(default=None, max_length=64, pattern=ID_PATTERN)


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = Field(default=None, max_length=64, pattern=ID_PATTERN)
    thread_id: str | None = Field(default=None, max_length=64, pattern=ID_PATTERN)  # the run or report being discussed
    context: ChatContext | None = None


class TitleIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)


def _my_conversation(conversation_id: str, user: User) -> dict:
    conversation = get_app_store().get_conversation(conversation_id, user.user_id)
    if not conversation:  # also for someone else's conversation: never confirm it exists
        raise HTTPException(404, "Unknown conversation")
    return conversation


@app.post("/api/chat/stream")
async def chat_stream(body: ChatIn, user: User = Depends(current_user)):
    """SSE answer stream; call the Cloud Run URL directly (Hosting rewrites buffer responses)."""
    store = get_app_store()
    if body.thread_id:  # the server, not the browser, decides which of this user's conversations belongs to the thread
        if not store.thread_exists(body.thread_id):
            raise HTTPException(404, "Unknown run or report")
        conversation = store.thread_conversation(user.user_id, body.thread_id)
    else:
        conversation = _my_conversation(body.conversation_id, user) if body.conversation_id else None
    if not settings.is_gcp_llm_ready:
        return sse_response(single_error("The assistant needs Gemini on Vertex AI (set GOOGLE_CLOUD_PROJECT on the API)."))
    created = conversation is None
    if conversation is None:
        conversation = store.create_conversation(user.user_id, auto_title(body.text))
        if body.thread_id and not store.link_thread(user.user_id, body.thread_id, conversation["conversation_id"]):
            store.delete_conversation(conversation["conversation_id"], user.user_id)  # lost a race with this user's other tab
            conversation, created = store.thread_conversation(user.user_id, body.thread_id) or conversation, False
    try:
        turn = start_turn(
            store=store, runner=assistant_runner(), sessions=session_service(), memory=memory_service(),
            sanitizer=get_sanitizer(), app_name=ASSISTANT_APP, user_id=user.user_id, conversation=conversation,
            created=created, question=body.text, prompt=build_prompt(body.text, body.context.report_id if body.context else None))
    except TurnInProgress:
        raise HTTPException(409, "A reply is already being written in this conversation")
    return sse_response(follow(turn))


@app.get("/api/conversations/{conversation_id}/stream")
async def reattach_conversation(conversation_id: str, user: User = Depends(current_user)):
    """Re-attach to the reply being written in the background: replays what was produced so far, then follows it live."""
    _my_conversation(conversation_id, user)
    turn = active_turn(user.user_id, conversation_id)
    if turn is None:
        raise HTTPException(404, "No reply in progress")
    return sse_response(follow(turn))


@app.post("/api/conversations/{conversation_id}/stop")
async def stop_conversation(conversation_id: str, user: User = Depends(current_user)) -> dict:
    """Stop the reply being written (the partial answer is kept). Switching away does not stop it."""
    _my_conversation(conversation_id, user)
    return {"stopped": stop_turn(user.user_id, conversation_id)}


@app.get("/api/conversations")
def list_conversations(user: User = Depends(current_user)) -> list[dict]:
    return [{**c, "active": active_turn(user.user_id, c["conversation_id"]) is not None}
            for c in get_app_store().list_conversations(user.user_id)]


@app.get("/api/threads/{thread_id}/conversation")
def thread_conversation(thread_id: str, user: User = Depends(current_user)) -> dict:
    """The caller's own conversation about a run or report (null until they ask something); never anyone else's."""
    store = get_app_store()
    if len(thread_id) > 64 or not re.match(ID_PATTERN, thread_id) or not store.thread_exists(thread_id):
        raise HTTPException(404, "Unknown run or report")
    conversation = store.thread_conversation(user.user_id, thread_id)
    if not conversation:
        return {"conversation": None}
    return {"conversation": {**conversation, "active": active_turn(user.user_id, conversation["conversation_id"]) is not None,
                             "messages": store.chat_messages(conversation["conversation_id"], user.user_id)}}


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str, user: User = Depends(current_user)) -> dict:
    conversation = _my_conversation(conversation_id, user)
    return {**conversation, "active": active_turn(user.user_id, conversation_id) is not None,
            "messages": get_app_store().chat_messages(conversation_id, user.user_id)}


@app.patch("/api/conversations/{conversation_id}")
def rename_conversation(conversation_id: str, body: TitleIn, user: User = Depends(current_user)) -> dict:
    title = " ".join(body.title.split())
    if not title:
        raise HTTPException(422, "Title must not be blank")
    conversation = get_app_store().rename_conversation(conversation_id, user.user_id, title)
    if not conversation:
        raise HTTPException(404, "Unknown conversation")
    return conversation


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, user: User = Depends(current_user)) -> dict:
    conversation = _my_conversation(conversation_id, user)
    get_app_store().delete_conversation(conversation_id, user.user_id)
    try:  # the ADK session holds the model-side history; Memory Bank recall is intentionally kept
        await session_service().delete_session(app_name=ASSISTANT_APP, user_id=user.user_id,
                                               session_id=conversation["session_id"])
    except Exception:
        log.warning("Could not delete ADK session for %s", conversation_id, exc_info=True)
    return {"conversation_id": conversation_id, "deleted": True}


# Watcher, action engine (service-to-service) --------------------------------------
@app.post("/api/events/gcs")
async def gcs_event(request: Request, caller: str = Depends(verify_service_caller)) -> dict:
    """Eventarc CloudEvent for google.cloud.storage.object.v1.finalized."""
    payload = await request.json()
    bucket, name = payload.get("bucket"), payload.get("name", "")
    if not bucket or not eligible(name):
        return {"status": "ignored", "name": name}
    source = source_for(bucket, name)
    if not get_app_store().mark_processed(source["gcs_uri"], "pending"):
        return {"status": "duplicate", "name": name}
    return start_run(source, WATCHER_USER, None, None, email=WATCHER_USER, source_kind="watcher")


@app.post("/api/watch/poll")
async def watch_poll(caller: str = Depends(verify_service_caller)) -> dict:
    started = []
    for source in list_incoming():
        if get_app_store().mark_processed(source["gcs_uri"], "pending"):
            started.append(start_run(source, WATCHER_USER, None, None, email=WATCHER_USER, source_kind="watcher"))
    return {"started": started}


@app.post("/internal/actions/dispatch")
async def action_engine(request: Request, caller: str = Depends(verify_service_caller)) -> dict:
    """Pub/Sub push endpoint; only enabled on the action-engine service."""
    if os.getenv("ENABLE_ACTION_ENGINE") != "1":
        raise HTTPException(404, "Not found")
    envelope = await request.json()
    import base64

    message = json.loads(base64.b64decode(envelope["message"]["data"]))
    return await asyncio.to_thread(dispatch, message["action_id"])


WEB_DIST = ROOT / "web" / "dist"
if WEB_DIST.exists():  # optional same-origin UI for local runs
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
