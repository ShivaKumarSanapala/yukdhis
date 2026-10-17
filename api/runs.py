"""Run manager: executes the pipeline in the background and records events.

Events go to the app store (so any Cloud Run instance can serve them over
WebSocket or polling).  ``agentic`` drives the ADK SequentialAgent with
Gemini; ``deterministic`` runs the same stages without an LLM.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from google.genai import types

from api.adk_runtime import PIPELINE_APP, memory_service, pipeline_runner, session_service
from core.app_store import get_app_store
from core.config import get_settings
from pipeline.deterministic import run_pipeline
from pipeline.stages import default_as_of

log = logging.getLogger(__name__)
_TASKS: set[asyncio.Task] = set()
AGENT_STAGE = {
    "reader_agent": "reader", "mapper_agent": "mapper", "graphrag_agent": "graphrag", "precedent_agent": "precedent",
    "data_analyst_agent": "data_analyst", "evaluator_agent": "evaluator", "orchestrator_agent": "orchestrator",
}


def choose_mode(requested: str | None) -> str:
    settings = get_settings()
    mode = (requested or settings.agent_mode).lower()
    if mode == "agentic" and not settings.is_gcp_llm_ready:
        return "deterministic"
    return mode if mode in {"agentic", "deterministic"} else "deterministic"


def start_run(source: dict, user_id: str, mode: str | None = None, as_of: str | None = None) -> dict:
    store = get_app_store()
    mode = choose_mode(mode)
    run_id = store.create_run(mode, {k: v for k, v in source.items() if k != "path"} | {"user_id": user_id})
    task = asyncio.get_running_loop().create_task(_execute(run_id, source, user_id, mode, as_of or default_as_of()))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {"run_id": run_id, "mode": mode}


async def _execute(run_id: str, source: dict, user_id: str, mode: str, as_of: str) -> None:
    store = get_app_store()
    started = time.perf_counter()
    store.update_run(run_id, status="RUNNING")
    store.add_event(run_id, "pipeline", "run_started", {"mode": mode, "as_of": as_of, "document": source.get("filename")})
    try:
        if mode == "agentic":
            report_id = await _agentic(run_id, source, user_id, as_of)
        else:
            state = await asyncio.to_thread(run_pipeline, source, as_of,
                                            lambda stage, kind, payload: store.add_event(run_id, stage, kind, payload))
            report_id = state["report_id"]
        if not report_id:
            raise RuntimeError("The pipeline finished without producing a report.")
        store.update_run(run_id, status="SUCCEEDED", report_id=report_id)
        duration_s = round(time.perf_counter() - started, 2)
        store.add_event(run_id, "pipeline", "run_completed", {"report_id": report_id, "duration_s": duration_s})
        print(json.dumps({"severity": "INFO", "event": "run_completed", "run_id": run_id, "report_id": report_id,
                          "mode": mode, "duration_s": duration_s}), flush=True)
    except Exception as error:
        log.exception("Run %s failed", run_id)
        store.update_run(run_id, status="FAILED", error=str(error)[:2000])
        duration_s = round(time.perf_counter() - started, 2)
        store.add_event(run_id, "pipeline", "run_failed", {"error": str(error)[:2000], "duration_s": duration_s})
        print(json.dumps({"severity": "ERROR", "event": "run_failed", "run_id": run_id, "error": str(error)[:300],
                          "duration_s": duration_s}), flush=True)


async def _agentic(run_id: str, source: dict, user_id: str, as_of: str) -> str | None:
    store = get_app_store()
    service = session_service()
    session = await service.create_session(app_name=PIPELINE_APP, user_id=user_id,
                                           state={"source": source, "as_of": as_of, "run_id": run_id})
    prompt = (f"A new regulatory document '{source.get('filename')}' has arrived. Run the full compliance impact analysis "
              f"as of {as_of} and produce the impact report.")
    current = None
    async for event in pipeline_runner().run_async(user_id=user_id, session_id=session.id,
                                                   new_message=types.Content(role="user", parts=[types.Part(text=prompt)])):
        stage = AGENT_STAGE.get(event.author, event.author)
        if stage != current and stage in AGENT_STAGE.values():
            store.add_event(run_id, stage, "stage_started", {"agent": event.author})
            current = stage
        for call in event.get_function_calls():
            store.add_event(run_id, stage, "tool_call", {"tool": call.name, "args": _compact(call.args)})
        for response in event.get_function_responses():
            store.add_event(run_id, stage, "tool_result", {"tool": response.name, "output": _compact(response.response)})
        text = "".join(part.text or "" for part in (event.content.parts if event.content else []) if not getattr(part, "thought", False))
        if text.strip() and not event.partial:
            store.add_event(run_id, stage, "agent_message", {"agent": event.author, "text": text.strip()[:6000]})
    final = await service.get_session(app_name=PIPELINE_APP, user_id=user_id, session_id=session.id)
    try:
        await memory_service().add_session_to_memory(final)
    except Exception:  # memory is an enhancement; the report is already persisted
        log.warning("Could not add session %s to memory", session.id, exc_info=True)
    return final.state.get("report_id")


def _compact(value: Any, limit: int = 3000) -> Any:
    text = json.dumps(value, default=str)
    return value if len(text) <= limit else {"truncated": True, "preview": text[:limit]}
