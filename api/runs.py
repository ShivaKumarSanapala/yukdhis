"""Run manager: queues, executes, pauses for human review, resumes and cancels pipeline runs.

Status and events live in the app store, so any Cloud Run instance can serve them (WebSocket or polling) and a cancel
received by one instance reaches the executor on another. ``agentic`` drives the ADK SequentialAgent with Gemini;
``deterministic`` runs the same stages without an LLM.

Queue.  Each instance executes at most ``MAX_CONCURRENT_RUNS`` (default 3) runs at once (an ``asyncio.Semaphore``);
the others stay ``QUEUED`` with a ``run_queued`` event that carries their position. A user may have at most
``MAX_ACTIVE_RUNS_PER_USER`` (default 5) queued or running runs (``RunLimitExceeded``, HTTP 429). Runs of the same
document (tenant + content SHA-256) are serialized by a lease in the store (``doc_locks``, refreshed by a heartbeat and
expiring if the instance dies), so two runs never re-map the same policy into the graph at once; the waiting run gets
a ``run_waiting`` event naming the run it waits for. The lease is taken before the slot so a waiting run never blocks
a slot.

Cancel.  The API sets ``CANCEL_REQUESTED`` in the store. A per-run watcher polls it (``RUN_POLL_SECONDS``) and, like a
cancel request landing on the executing instance, sets the run's cancel flag and cancels its task. Deterministic runs
execute in a worker thread whose event callback raises ``RunCancelled``; the task waits for the thread to stop before
releasing the document lease. Agentic runs check the flag between ADK events and ``aclose`` the event generator in the
task that iterates it (closing it elsewhere trips OpenTelemetry's "created in a different Context" error).
The run ends ``CANCELLED`` with a ``run_cancelled`` event.

Human review (``review_rules``).  In both modes the parse and extract stage functions run first (``stop_after="extract"``)
and the run pauses as ``AWAITING_REVIEW`` with a ``rules_extracted`` event; the paused state is checkpointed in the
store (not in memory), so the review can resume on any instance. Approving resumes with the approved rules: the
deterministic runner skips parse/extract (``preset``); the agentic pipeline gets them through session state and the
Reader agent is skipped by its ``before_agent_callback``. Rejecting ends the run ``REJECTED``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from google.adk.agents.run_config import RunConfig, StreamingMode
from google.genai import types

from api.adk_runtime import PIPELINE_APP, memory_service, pipeline_runner, session_service
from core.app_store import ACTIVE_RUN_STATUSES, TRIGGER_KINDS, get_app_store
from core.app_store import now_ms as now  # run timestamps in ms: elapsed times of fast runs stay meaningful
from core.config import get_settings
from pipeline.deterministic import EXTRACTION_KEYS, run_pipeline
from pipeline.stages import default_as_of

log = logging.getLogger(__name__)
MAX_CONCURRENT_RUNS = max(1, int(os.getenv("MAX_CONCURRENT_RUNS", "3")))
MAX_ACTIVE_RUNS_PER_USER = max(1, int(os.getenv("MAX_ACTIVE_RUNS_PER_USER", "5")))
POLL_SECONDS = float(os.getenv("RUN_POLL_SECONDS", "1.0"))
LOCK_TTL_S, HEARTBEAT_S, STALE_AFTER_S = 120, 20, 300
THINKING_FLUSH_S = 1.5
WATCHER_USER = "watcher"
ACTIVE = set(ACTIVE_RUN_STATUSES)
PAUSED = {"AWAITING_REVIEW"}
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "REJECTED"}
RUN_STATUSES = ACTIVE | PAUSED | TERMINAL
AGENT_STAGE = {
    "reader_agent": "reader", "mapper_agent": "mapper", "graphrag_agent": "graphrag", "precedent_agent": "precedent",
    "data_analyst_agent": "data_analyst", "evaluator_agent": "evaluator", "orchestrator_agent": "orchestrator",
}


class RunCancelled(Exception):
    """Raised inside a run once its cancellation was requested."""


class RunLimitExceeded(Exception):
    """The user already has the maximum number of active runs."""


@dataclass
class _Local:
    """What this instance knows about a run it executes."""

    task: asyncio.Task | None = None
    cancel: threading.Event = field(default_factory=threading.Event)


_TASKS: set[asyncio.Task] = set()
_LOCAL: dict[str, _Local] = {}
_WAITING: list[str] = []  # run ids waiting for a slot on this instance, in FIFO order
_SLOTS: tuple[asyncio.AbstractEventLoop, int, asyncio.Semaphore] | None = None


def _slots() -> asyncio.Semaphore:
    """One semaphore per event loop (tests start a loop per TestClient) and per configured limit."""
    global _SLOTS
    loop = asyncio.get_running_loop()
    if _SLOTS is None or _SLOTS[0] is not loop or _SLOTS[1] != MAX_CONCURRENT_RUNS:
        _SLOTS = (loop, MAX_CONCURRENT_RUNS, asyncio.Semaphore(MAX_CONCURRENT_RUNS))
    return _SLOTS[2]


def choose_mode(requested: str | None) -> str:
    settings = get_settings()
    mode = (requested or settings.agent_mode).lower()
    if mode == "agentic" and not settings.is_gcp_llm_ready:
        return "deterministic"
    return mode if mode in {"agentic", "deterministic"} else "deterministic"


def ensure_capacity(owner_id: str, count: int = 1) -> None:
    active = get_app_store().count_active_runs(owner_id, STALE_AFTER_S)
    if active + count > MAX_ACTIVE_RUNS_PER_USER:
        more = f"Starting {count} more would exceed" if count > 1 else "That is"
        raise RunLimitExceeded(f"You have {active} active analyses. {more} the limit of {MAX_ACTIVE_RUNS_PER_USER} per "
                               "user. Wait for one to finish or cancel one, then try again.")


def sha256_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _source_sha(source: dict) -> str | None:
    """Content hash for sources not read by the API (GCS objects, fetched URLs); None if unreadable."""
    try:
        from pipeline.stages import _source_bytes

        return sha256_of(_source_bytes(source))
    except Exception:  # noqa: BLE001 - the run itself reports a missing document clearly
        log.warning("Could not hash the source document", exc_info=True)
        return None


def _spawn(run_id: str, coro: Any) -> None:
    local = _LOCAL.setdefault(run_id, _Local())
    local.cancel.clear()
    task = asyncio.get_running_loop().create_task(coro)
    local.task = task
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def start_run(source: dict, user_id: str, mode: str | None = None, as_of: str | None = None, *, email: str | None = None,
              source_kind: str | None = None, review_rules: bool = False, doc_sha256: str | None = None,
              rerun_of: str | None = None) -> dict:
    store = get_app_store()
    mode, as_of = choose_mode(mode), as_of or default_as_of()
    if doc_sha256 is None and source.get("path") and not source.get("gcs_uri"):
        path = Path(source["path"])
        doc_sha256 = sha256_of(path.read_bytes()) if path.is_file() else None
    kind = source_kind or TRIGGER_KINDS.get(source.get("trigger") or "", "upload")
    run_id = store.create_run(mode, {k: v for k, v in source.items() if k not in {"path", "doc_sha256"}}, {
        "owner_id": user_id, "created_by": email or user_id, "source_kind": kind, "filename": source.get("filename"),
        "doc_sha256": doc_sha256 or source.get("doc_sha256"), "as_of": as_of, "review_rules": review_rules,
        "rerun_of": rerun_of, "source": source})
    _spawn(run_id, _execute(run_id, source, user_id, mode, as_of, review=review_rules))
    return {"run_id": run_id, "mode": mode, "filename": source.get("filename")}


def resume_run(run: dict, extraction: dict) -> bool:
    """Approve a paused run: continue with the reviewed rules (skipping extraction). False if it is no longer paused."""
    store = get_app_store()
    checkpoint = {**(run.get("checkpoint") or {}), "extraction": extraction}
    if not store.transition_run(run["run_id"], PAUSED, "QUEUED", checkpoint=checkpoint, rules=extraction["rules"],
                                heartbeat_at=int(time.time()), current_stage=None):
        return False
    _spawn(run["run_id"], _execute(run["run_id"], run["source"], run["owner_id"] or WATCHER_USER, run["mode"],
                                   run.get("as_of") or default_as_of(), preset=checkpoint))
    return True


def reject_run(run_id: str) -> bool:
    return get_app_store().transition_run(run_id, PAUSED, "REJECTED", finished_at=now(), current_stage=None)


def request_cancel(run: dict) -> str:
    """Ask the run to stop; returns the status it is in afterwards."""
    store, run_id = get_app_store(), run["run_id"]
    if store.transition_run(run_id, PAUSED, "CANCELLED", finished_at=now()):  # nothing executes while paused
        store.add_event(run_id, "pipeline", "run_cancelled", {"while": "awaiting_review"})
        return "CANCELLED"
    local = _LOCAL.get(run_id)
    heartbeat = (run.get("heartbeat_at") or 0)
    if local is None and run["status"] in ACTIVE and heartbeat and time.time() - heartbeat > STALE_AFTER_S:
        if store.transition_run(run_id, ACTIVE, "CANCELLED", finished_at=now()):  # its instance is gone
            store.add_event(run_id, "pipeline", "run_cancelled", {"reason": "The executing instance stopped responding."})
            return "CANCELLED"
    if not store.transition_run(run_id, {"QUEUED", "RUNNING"}, "CANCEL_REQUESTED"):
        return store.run_status(run_id) or "UNKNOWN"
    if local:
        _cancel_local(local)
    return "CANCEL_REQUESTED"


INTERRUPTED = "Interrupted: the server restarted while this run was in progress. Re-run it."
_REAPED_AT = 0.0


def reap_stale_runs(force: bool = False) -> list[str]:
    """Fail active runs whose executing instance is gone (deploys replace instances; in-process runs die with them).

    Cheap enough to call on reads; throttled to once a minute per instance unless ``force``.
    """
    global _REAPED_AT
    if not force and time.monotonic() - _REAPED_AT < 60:
        return []
    _REAPED_AT = time.monotonic()
    store, reaped = get_app_store(), []
    for run_id in store.stale_active_runs(STALE_AFTER_S):
        if run_id in _LOCAL:  # still executing here: its watcher keeps heart-beating
            continue
        if store.transition_run(run_id, ACTIVE, "FAILED", error=INTERRUPTED, finished_at=now(), current_stage=None,
                                queued_position=None):
            store.add_event(run_id, "pipeline", "run_failed", {"error": INTERRUPTED, "interrupted": True})
            _log("WARNING", "run_interrupted", run_id=run_id)
            reaped.append(run_id)
    return reaped


def interrupt_local_runs() -> list[str]:
    """On instance shutdown (SIGTERM from a deploy or scale-in): mark the runs executing here as interrupted."""
    store, interrupted = get_app_store(), []
    for run_id, local in list(_LOCAL.items()):
        if local.task is None or local.task.done():
            continue
        if store.transition_run(run_id, ACTIVE, "FAILED", error=INTERRUPTED, finished_at=now(), current_stage=None,
                                queued_position=None):
            store.add_event(run_id, "pipeline", "run_failed", {"error": INTERRUPTED, "interrupted": True})
            interrupted.append(run_id)
    if interrupted:
        _log("WARNING", "runs_interrupted", run_ids=interrupted)
    return interrupted


def _cancel_local(local: _Local) -> None:
    if local.cancel.is_set():
        return
    local.cancel.set()
    if local.task and not local.task.done():
        local.task.cancel()


async def _watch(run_id: str, local: _Local, lock_key: Callable[[], str | None]) -> None:
    """Picks up cancel requests made on any instance; heart-beats the run and its document lease."""
    store, beat = get_app_store(), 0.0
    while True:
        await asyncio.sleep(POLL_SECONDS)
        try:
            if store.run_status(run_id) == "CANCEL_REQUESTED":
                _cancel_local(local)
            if time.monotonic() - beat >= HEARTBEAT_S:
                beat = time.monotonic()
                store.heartbeat(run_id)
                if key := lock_key():
                    store.refresh_doc_lock(key, run_id, LOCK_TTL_S)
        except Exception:  # noqa: BLE001 - a transient store error must not kill the run
            log.warning("Run watcher for %s failed", run_id, exc_info=True)


async def _wait_for_document(run_id: str, key: str) -> None:
    store, announced = get_app_store(), None
    while (holder := store.acquire_doc_lock(key, run_id, LOCK_TTL_S)) is not None:
        if holder and holder != announced:
            announced = holder
            store.add_event(run_id, "pipeline", "run_waiting", {
                "reason": "same_document", "blocking_run_id": holder,
                "message": f"Waiting for run {holder}, which is analysing the same document. Runs of one document take "
                           "turns so its policy is never re-mapped into the graph twice at the same time."})
        await asyncio.sleep(POLL_SECONDS)


def _publish_positions() -> None:
    store = get_app_store()
    for index, waiting in enumerate(_WAITING):
        store.update_run(waiting, queued_position=index + 1)


async def _wait_for_slot(run_id: str) -> asyncio.Semaphore:
    slots = _slots()
    if not slots.locked():
        await slots.acquire()
        return slots
    store = get_app_store()
    _WAITING.append(run_id)
    position = len(_WAITING)
    store.update_run(run_id, queued_position=position)
    store.add_event(run_id, "pipeline", "run_queued", {
        "position": position, "limit": MAX_CONCURRENT_RUNS,
        "message": f"Queued at position {position}: {MAX_CONCURRENT_RUNS} analyses run at once on this server."})
    try:
        await slots.acquire()
    finally:
        _WAITING.remove(run_id)
        _publish_positions()
    return slots


def _log(severity: str, event: str, **fields: Any) -> None:
    print(json.dumps({"severity": severity, "event": event, **fields}, default=str), flush=True)


async def _execute(run_id: str, source: dict, user_id: str, mode: str, as_of: str, review: bool = False,
                   preset: dict | None = None) -> None:
    store = get_app_store()
    local = _LOCAL.setdefault(run_id, _Local())
    started = time.perf_counter()
    lock_key: str | None = None
    held_key: str | None = None
    slot: asyncio.Semaphore | None = None
    watcher = asyncio.create_task(_watch(run_id, local, lambda: held_key))
    try:
        sha = (store.get_run(run_id) or {}).get("doc_sha256")
        if not sha and (sha := await asyncio.to_thread(_source_sha, source)):
            store.update_run(run_id, doc_sha256=sha)
        lock_key = f"{store.tenant_id}:{sha or source.get('gcs_uri') or source.get('path') or run_id}"[:160]
        await _wait_for_document(run_id, lock_key)
        held_key = lock_key
        slot = await _wait_for_slot(run_id)
        if not store.transition_run(run_id, {"QUEUED"}, "RUNNING", started_at=now(), queued_position=None):
            raise RunCancelled()
        store.add_event(run_id, "pipeline", "run_resumed" if preset else "run_started",
                        {"mode": mode, "as_of": as_of, "document": source.get("filename"), "review_rules": review})
        if review and not preset:
            await _pause_for_review(run_id, source, as_of, local)
            return
        if mode == "agentic":
            report_id = await _agentic(run_id, source, user_id, as_of, local, preset)
        else:
            report_id = (await _deterministic(run_id, source, as_of, local, preset)).get("report_id")
        if not report_id:
            raise RuntimeError("The pipeline finished without producing a report.")
        # final events are written before the final status: followers stop reading once they see it
        duration_s = round(time.perf_counter() - started, 2)
        store.add_event(run_id, "pipeline", "run_completed", {"report_id": report_id, "duration_s": duration_s})
        store.transition_run(run_id, {"RUNNING", "CANCEL_REQUESTED"}, "SUCCEEDED", report_id=report_id, finished_at=now())
        _log("INFO", "run_completed", run_id=run_id, report_id=report_id, mode=mode, duration_s=duration_s)
    except (RunCancelled, asyncio.CancelledError):
        _cancelled(run_id, started)
    except Exception as error:
        if local.cancel.is_set():  # e.g. the agent generator raising while it is being closed
            _cancelled(run_id, started)
            return
        log.exception("Run %s failed", run_id)
        duration_s = round(time.perf_counter() - started, 2)
        store.add_event(run_id, "pipeline", "run_failed", {"error": str(error)[:2000], "duration_s": duration_s,
                                                           "stage": (store.get_run(run_id) or {}).get("current_stage")})
        store.update_run(run_id, status="FAILED", error=str(error)[:2000], finished_at=now(), queued_position=None)
        _log("ERROR", "run_failed", run_id=run_id, error=str(error)[:300], duration_s=duration_s)
    finally:
        watcher.cancel()
        if slot is not None:
            slot.release()
        if held_key:
            store.release_doc_lock(held_key, run_id)
        if _LOCAL.get(run_id) is local:
            _LOCAL.pop(run_id, None)
        store.forget_seq(run_id)


def _cancelled(run_id: str, started: float) -> None:
    store = get_app_store()
    duration_s = round(time.perf_counter() - started, 2)
    store.add_event(run_id, "pipeline", "run_cancelled", {"duration_s": duration_s,
                                                          "stage": (store.get_run(run_id) or {}).get("current_stage")})
    store.update_run(run_id, status="CANCELLED", finished_at=now(), queued_position=None)
    _log("INFO", "run_cancelled", run_id=run_id, duration_s=duration_s)


def _emitter(run_id: str, local: _Local) -> Callable[[str, str, dict], None]:
    store = get_app_store()

    def emit(stage: str, kind: str, payload: dict) -> None:
        if local.cancel.is_set() or (kind == "stage_started" and store.run_status(run_id) == "CANCEL_REQUESTED"):
            local.cancel.set()
            raise RunCancelled()
        store.add_event(run_id, stage, kind, payload)
        if kind == "stage_started":
            store.update_run(run_id, current_stage=stage)

    return emit


async def _deterministic(run_id: str, source: dict, as_of: str, local: _Local, preset: dict | None = None,
                         stop_after: str | None = None) -> dict:
    worker = asyncio.ensure_future(asyncio.to_thread(run_pipeline, source, as_of, _emitter(run_id, local), preset=preset,
                                                     stop_after=stop_after))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        local.cancel.set()  # the thread stops at its next event; wait so the document lease is held until it has
        with contextlib.suppress(BaseException):
            await worker
        raise


async def _pause_for_review(run_id: str, source: dict, as_of: str, local: _Local) -> None:
    store = get_app_store()
    state = await _deterministic(run_id, source, as_of, local, stop_after="extract")
    checkpoint = {k: state[k] for k in EXTRACTION_KEYS if k in state}
    extraction = state["extraction"]
    store.add_event(run_id, "reader", "rules_extracted", {
        "policy": extraction["policy"], "rules": extraction["rules"], "issues": state.get("validation_issues", []),
        "provenance": state.get("extraction_provenance", {})})
    if not store.transition_run(run_id, {"RUNNING"}, "AWAITING_REVIEW", checkpoint=checkpoint, rules=extraction["rules"],
                                current_stage="review"):
        raise RunCancelled()
    store.add_event(run_id, "pipeline", "awaiting_review", {"rules": len(extraction["rules"])})


async def _agentic(run_id: str, source: dict, user_id: str, as_of: str, local: _Local, preset: dict | None = None) -> str | None:
    store = get_app_store()
    service = session_service()
    state = {"source": source, "as_of": as_of, "run_id": run_id}
    if preset:  # reviewed rules: the Reader agent's before_agent_callback skips extraction
        state.update(preset, rules_approved=True)
    session = await service.create_session(app_name=PIPELINE_APP, user_id=user_id, state=state)
    prompt = (f"A new regulatory document '{source.get('filename')}' has arrived. Run the full compliance impact analysis "
              f"as of {as_of} and produce the impact report.")
    if preset:
        prompt += " Its rules were already extracted and approved by a compliance officer; start from the mapping."
    current = None
    last_error: tuple[str, str] | None = None  # (stage, message) of the latest tool error, explains a missing report
    trace = _Trace(run_id)
    # SSE streaming: partial events carry thought summaries as they are generated, so the timeline shows them live.
    events = pipeline_runner().run_async(user_id=user_id, session_id=session.id,
                                         new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
                                         run_config=RunConfig(streaming_mode=StreamingMode.SSE))
    try:
        async for event in events:
            if local.cancel.is_set():
                raise RunCancelled()
            stage = AGENT_STAGE.get(event.author, event.author)
            if stage != current and stage in AGENT_STAGE.values():
                if store.run_status(run_id) == "CANCEL_REQUESTED":
                    local.cancel.set()
                    raise RunCancelled()
                trace.flush(done=True)
                store.add_event(run_id, stage, "stage_started", {"agent": event.author})
                store.update_run(run_id, current_stage=stage)
                current = stage
            parts = event.content.parts if event.content and event.content.parts else []
            thought = "".join(part.text or "" for part in parts if getattr(part, "thought", False))
            if event.partial:
                trace.think(stage, event.author, thought)
                continue
            trace.finish_thought(stage, event.author, thought)  # the final event repeats the whole thought summary
            for call in event.get_function_calls():
                trace.tool_call(stage, call)
            for response in event.get_function_responses():
                trace.tool_result(stage, response)
                if isinstance(response.response, dict) and response.response.get("status") == "error":
                    last_error = (stage, str(response.response.get("message") or f"{response.name} failed"))
            text = "".join(part.text or "" for part in parts if not getattr(part, "thought", False))
            if text.strip():
                store.add_event(run_id, stage, "agent_message", {"agent": event.author, "text": text.strip()[:6000]})
        trace.flush(done=True)
    finally:
        await events.aclose()  # same task that iterates it (see module docstring)
    final = await service.get_session(app_name=PIPELINE_APP, user_id=user_id, session_id=session.id)
    halted = final.state.get("pipeline_halted")  # the Reader could not read the document: that is the real reason
    if not final.state.get("report_id") and (halted or last_error):
        store.update_run(run_id, current_stage="reader" if halted else last_error[0])
        raise RuntimeError(halted or last_error[1])
    try:
        await memory_service().add_session_to_memory(final)
    except Exception:  # memory is an enhancement; the report is already persisted
        log.warning("Could not add session %s to memory", session.id, exc_info=True)
    return final.state.get("report_id")


class _Trace:
    """Turns ADK events into run events the UI renders as each agent's live thinking and tool calls.

    Thought deltas from partial events are buffered and written at most every ``THINKING_FLUSH_S`` as one cumulative
    ``thinking`` event per stream (the UI keeps the latest text per stream), so the store is not hit per token."""

    def __init__(self, run_id: str) -> None:
        self.run_id, self.store = run_id, get_app_store()
        self.count, self.stage, self.agent, self.text, self.flushed_at = 0, "", "", "", 0.0
        self.calls: dict[str, float] = {}

    def _stream(self) -> str:
        return f"{self.agent}-{self.count}"

    def think(self, stage: str, agent: str, delta: str) -> None:
        if not delta:
            return
        if agent != self.agent:
            self.flush(done=True)
            self.stage, self.agent = stage, agent
        self.text += delta
        if time.monotonic() - self.flushed_at >= THINKING_FLUSH_S:
            self.flush(done=False)

    def finish_thought(self, stage: str, agent: str, full: str) -> None:
        if full and agent == self.agent and full.startswith(self.text[:200]):
            self.text = full  # the final event carries the complete summary
        elif full and not self.text:
            self.stage, self.agent, self.text = stage, agent, full
        self.flush(done=True)

    def flush(self, done: bool) -> None:
        if not self.text:
            return
        self.store.add_event(self.run_id, self.stage, "thinking",
                             {"agent": self.agent, "stream": self._stream(), "text": self.text[-8000:], "done": done})
        self.flushed_at = time.monotonic()
        if done:
            self.count, self.text = self.count + 1, ""

    def tool_call(self, stage: str, call: Any) -> None:
        self.calls[call.id or call.name] = time.monotonic()
        self.store.add_event(self.run_id, stage, "tool_call", {"tool": call.name, "id": call.id, "args": _compact(call.args)})

    def tool_result(self, stage: str, response: Any) -> None:
        started = self.calls.pop(response.id or response.name, None)
        output = response.response
        payload = {"tool": response.name, "id": response.id, "output": _compact(output),
                   "status": "error" if isinstance(output, dict) and output.get("status") == "error" else "ok"}
        if started is not None:
            payload["duration_ms"] = round((time.monotonic() - started) * 1000)
        self.store.add_event(self.run_id, stage, "tool_result", payload)


def _compact(value: Any, limit: int = 3000) -> Any:
    text = json.dumps(value, default=str)
    return value if len(text) <= limit else {"truncated": True, "preview": text[:limit]}
