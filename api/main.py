"""FastAPI bridge for the Autonomous Compliance Engine (deployed on Cloud Run).

The React UI on Firebase Hosting calls ``/api/*`` (Hosting rewrite to this
service) and opens the run-event WebSocket directly against the Cloud Run URL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict, deque
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from google.genai import types
from pydantic import BaseModel, Field

from api.adk_runtime import ASSISTANT_APP, assistant_runner, session_service
from api.auth import User, current_user, verify_firebase_token, verify_service_caller
from api.runs import choose_mode, start_run
from bank_mcp_server.remediation import dispatch, request_dispatch
from core.app_store import get_app_store
from core.config import DATA_DIR, ROOT, get_settings
from ingestion.documents import DOCAI_TYPES, TEXT_TYPES, archive_to_gcs, document_id_for, guess_mime
from ingestion.watcher import eligible, list_incoming, source_for
from knowledge_graph.mapper import review_rule
from knowledge_graph.ontology import ontology_summary
from knowledge_graph.store import get_graph_store
from knowledge_graph.traversal import blast_radius
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
                   allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])

_hits: dict[str, deque] = defaultdict(deque)
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    if request.method == "POST":
        key = request.client.host if request.client else "unknown"
        window, now = _hits[key], time.monotonic()
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= RATE_LIMIT:
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
        window.append(now)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


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
@app.post("/api/runs")
async def create_run(request: Request, file: UploadFile | None = File(default=None), sample_id: str | None = Form(default=None),
                     mode: str | None = Form(default=None), as_of: str | None = Form(default=None),
                     user: User = Depends(current_user)) -> dict:
    if as_of:
        try:
            date.fromisoformat(as_of)
        except ValueError:
            raise HTTPException(422, "as_of must be an ISO date")
    if sample_id:
        path = (SAMPLES_DIR / Path(sample_id).name)
        if not path.exists() or path.suffix != ".md":
            raise HTTPException(404, "Unknown sample")
        source = {"path": str(path), "filename": path.name, "display_uri": f"sample://{path.name}", "trigger": "sample"}
    elif file is not None:
        content = await file.read(settings.max_upload_bytes + 1)
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(413, "File too large")
        filename = Path(file.filename or "upload.md").name
        mime = guess_mime(filename)
        if mime not in ALLOWED_TYPES:
            raise HTTPException(415, "Upload PDF, DOCX, HTML, Markdown or text")
        if mime == "application/pdf" and not content.startswith(b"%PDF"):
            raise HTTPException(415, "File content is not a PDF")
        gcs_uri = archive_to_gcs(content, filename)
        if gcs_uri:
            source = {"gcs_uri": gcs_uri, "filename": filename, "trigger": "upload"}
        else:
            target = settings.runtime_dir / "uploads" / f"{document_id_for(content)}-{filename}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            source = {"path": str(target), "filename": filename, "display_uri": f"upload://{filename}", "trigger": "upload"}
    else:
        raise HTTPException(422, "Provide a file or a sample_id")
    return start_run(source, user.user_id, mode, as_of)


@app.get("/api/runs")
def runs(user: User = Depends(current_user)) -> list[dict]:
    return get_app_store().list_runs()


@app.get("/api/runs/{run_id}")
def run_status(run_id: str, after: int = 0, user: User = Depends(current_user)) -> dict:
    store = get_app_store()
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "Unknown run")
    return {"run": run, "events": store.events(run_id, after)}


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
            for event in store.events(run_id, seq):
                await websocket.send_json(event)
                seq = event["seq"]
            run = store.get_run(run_id)
            if not run or run["status"] in {"SUCCEEDED", "FAILED"}:
                for event in store.events(run_id, seq):
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


@app.post("/api/actions/{action_id}/decision")
def decide_action(action_id: str, body: Decision, user: User = Depends(current_user)) -> dict:
    store = get_app_store()
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


@app.post("/api/rules/review")
def rule_review(body: RuleReview, user: User = Depends(current_user)) -> dict:
    try:
        result = review_rule(get_graph_store(), body.rule_id, body.decision, user.email, date.today().isoformat())
    except KeyError:
        raise HTTPException(404, "Unknown rule")
    get_app_store().audit(user.email, "rule_review", body.model_dump(), body.decision, result)
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


@app.get("/api/graph/blast-radius")
def graph_blast_radius(rule_id: str, as_of: str | None = None, user: User = Depends(current_user)) -> dict:
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
    return start_run(source, "watcher", None, None)


@app.post("/api/watch/poll")
async def watch_poll(caller: str = Depends(verify_service_caller)) -> dict:
    started = []
    for source in list_incoming():
        if get_app_store().mark_processed(source["gcs_uri"], "pending"):
            started.append(start_run(source, "watcher", None, None))
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
