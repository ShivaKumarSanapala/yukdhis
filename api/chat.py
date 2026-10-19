"""Assistant chat streamed as Server-Sent Events, with per-user conversation memory.

The ADK runner runs with ``StreamingMode.SSE``; its events become typed frames::

    event: start        {conversation_id, title, created, user_message_id}
    event: thinking     {delta}                          Gemini thought-summary text
    event: tool_call    {id, name, args}
    event: tool_result  {id, name, output, status, duration_ms}   output clipped to MAX_TOOL_CHARS
    event: text         {delta}                          answer Markdown
    event: error        {message}
    event: done         {conversation_id, message_id, status}     status: complete | stopped | error

plus a ``: ping`` comment every ``HEARTBEAT_SECONDS``.  Whatever reaches Gemini
still passes ``mask_pii_before_model``; tool args/outputs shown to the user get
the same local scrub the callback applies to function responses, and sessions
are re-sanitized before they are written to Memory Bank.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from typing import Any, AsyncIterator, Callable

from fastapi.responses import StreamingResponse
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.genai import types

from core.app_store import AppStore
from privacy.sanitizer import Sanitizer

log = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 15.0
MAX_TOOL_CHARS = 4000
MAX_THINKING_CHARS = 20000
TITLE_CHARS = 60
SSE_HEADERS = {"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"}
_BACKGROUND: set[asyncio.Task] = set()


def sse(event: str, data: dict) -> str:
    """One SSE frame; JSON escapes newlines so ``data`` is always a single line."""
    return f"event: {event}\ndata: {json.dumps(data, default=str, ensure_ascii=False)}\n\n"


def sse_response(frames: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(frames, media_type="text/event-stream", headers=SSE_HEADERS)


async def single_error(message: str) -> AsyncIterator[str]:
    yield sse("error", {"message": message})


def clip(value: Any, limit: int = MAX_TOOL_CHARS) -> Any:
    text = json.dumps(value, default=str, ensure_ascii=False)
    return value if len(text) <= limit else {"truncated": True, "chars": len(text), "preview": text[:limit]}


def auto_title(text: str, limit: int = TITLE_CHARS) -> str:
    line = " ".join(text.split())
    return line if len(line) <= limit else line[:limit - 1].rstrip() + "…"


def build_prompt(text: str, report_id: str | None = None) -> str:
    return f"[UI context: the user currently has impact report {report_id} open.]\n\n{text}" if report_id else text


class TraceTranslator:
    """ADK events -> (sse_event, payload) pairs, keeping the answer and trace for persistence.

    With SSE streaming ADK yields ``partial`` chunks (deltas) and then one
    aggregated non-partial event per model response; the aggregate's text is
    only emitted when no deltas were streamed for it (non-streaming models).
    """

    def __init__(self, scrub: Callable[[Any], Any] = lambda value: value, clock: Callable[[], float] = time.monotonic):
        self.scrub, self.clock = scrub, clock
        self.text, self.thinking, self.errors = "", "", []
        # The answer is the text after the last tool call. Text the model writes before calling a tool (its plan, or
        # "I cannot find that") is not part of the answer, so it is neither shown nor saved as the answer.
        self.answer = ""
        self.tools: dict[str, dict] = {}
        self._started: dict[str, float] = {}
        self._streamed = False
        self._gap = {"text": False, "thinking": False}

    def _delta(self, kind: str, delta: str) -> tuple[str, dict] | None:
        if not delta:
            return None
        current = self.text if kind == "text" else self.thinking
        if self._gap[kind] and current and not current.endswith("\n\n"):
            delta = "\n\n" + delta  # new model turn after a tool call starts a new paragraph
        self._gap[kind] = False
        if kind == "text":
            self.text += delta
            self.answer += delta if self.answer else delta.lstrip("\n")  # no separator before the first answer text
        else:
            self.thinking = (self.thinking + delta)[:MAX_THINKING_CHARS]
        return kind, {"delta": delta}

    def _parts(self, event: Any) -> list[tuple[str, dict]]:
        parts = (event.content.parts if event.content else None) or []
        return [d for p in parts if p.text and (d := self._delta("thinking" if p.thought else "text", p.text))]

    def translate(self, event: Any) -> list[tuple[str, dict]]:
        if event.partial:
            out = self._parts(event)
            self._streamed = self._streamed or bool(out)
            return out
        out = [] if self._streamed else self._parts(event)
        self._streamed = False
        for call in event.get_function_calls():
            self.answer = ""  # text before a tool call is a preamble, not the answer
            call_id = call.id or f"call-{len(self.tools) + 1}"
            entry = {"id": call_id, "name": call.name, "args": clip(self.scrub(call.args or {})), "status": "running"}
            self.tools[call_id], self._started[call_id] = entry, self.clock()
            self._gap = {"text": True, "thinking": True}
            out.append(("tool_call", {k: entry[k] for k in ("id", "name", "args")}))
        for response in event.get_function_responses():
            call_id = response.id or next((k for k, v in reversed(self.tools.items())
                                           if v["name"] == response.name and v["status"] == "running"), f"call-{len(self.tools) + 1}")
            raw = response.response
            entry = self.tools.setdefault(call_id, {"id": call_id, "name": response.name, "args": {}})
            started = self._started.get(call_id)
            entry.update(output=clip(self.scrub(raw)),
                         status="error" if isinstance(raw, dict) and raw.get("status") == "error" else "ok",
                         duration_ms=int((self.clock() - started) * 1000) if started is not None else None)
            out.append(("tool_result", {k: entry[k] for k in ("id", "name", "output", "status", "duration_ms")}))
        if event.error_code or event.error_message:
            message = event.error_message or f"The model stopped early ({event.error_code})."
            self.errors.append(message)
            out.append(("error", {"message": message}))
        return out

    def trace(self, stopped: bool = False) -> dict:
        tools = [{**t, "status": "cancelled"} if stopped and t.get("status") == "running" else t for t in self.tools.values()]
        return {"thinking": self.thinking, "tool_calls": tools}


def sanitized_session(session: Any, sanitizer: Sanitizer) -> Any:
    """Copy of an ADK session with user text and tool payloads masked and thoughts dropped (for Memory Bank)."""
    copy = session.model_copy(deep=True)
    for event in copy.events:
        if not event.content or not event.content.parts:
            continue
        event.content.parts = [p for p in event.content.parts if not p.thought]
        for part in event.content.parts:
            if part.text:
                part.text = sanitizer.sanitize(part.text, session.id).text
            if part.function_call and part.function_call.args:
                part.function_call.args = sanitizer.sanitize_value(part.function_call.args, session.id)
            if part.function_response and part.function_response.response:
                part.function_response.response = sanitizer.sanitize_value(part.function_response.response, session.id)
    return copy


def _spawn(coro: Any) -> None:
    task = asyncio.ensure_future(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


async def _close(pending: asyncio.Future | None, agen: Any) -> None:
    if pending is not None:
        with suppress(BaseException):
            await pending
    if agen is not None:
        with suppress(BaseException):
            await agen.aclose()


async def remember(sessions: Any, memory: Any, sanitizer: Sanitizer, app_name: str, user_id: str, session_id: str) -> None:
    """Best-effort cross-conversation recall (load_memory / preload_memory)."""
    try:
        session = await sessions.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
        if session is not None:
            await memory.add_session_to_memory(sanitized_session(session, sanitizer))
    except Exception:
        log.warning("Could not add chat session %s to memory", session_id, exc_info=True)


async def stream_turn(*, store: AppStore, runner: Any, sessions: Any, memory: Any, sanitizer: Sanitizer, app_name: str,
                      user_id: str, conversation: dict, created: bool, question: str, prompt: str,
                      heartbeat: float = HEARTBEAT_SECONDS) -> AsyncIterator[str]:
    conversation_id, session_id = conversation["conversation_id"], conversation["session_id"]
    user_message = store.add_chat_message(conversation_id, user_id, "user", question)
    yield sse("start", {"conversation_id": conversation_id, "title": conversation["title"], "created": created,
                        "user_message_id": user_message["message_id"]})
    translator = TraceTranslator(scrub=lambda value: sanitizer.sanitize_value(value, session_id))
    abort, pending, agen = asyncio.Event(), None, None
    saved: dict | None = None

    def persist(status: str) -> dict:
        nonlocal saved
        if saved is None:
            saved = store.add_chat_message(conversation_id, user_id, "assistant", translator.answer,
                                           translator.trace(stopped=status == "stopped"), status)
        return saved

    try:
        try:
            if await sessions.get_session(app_name=app_name, user_id=user_id, session_id=session_id) is None:
                await sessions.create_session(app_name=app_name, user_id=user_id, session_id=session_id)
            agen = runner.run_async(user_id=user_id, session_id=session_id, abort_signal=abort,
                                    new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
                                    run_config=RunConfig(streaming_mode=StreamingMode.SSE))
            pending = asyncio.ensure_future(agen.__anext__())
            while True:
                done, _ = await asyncio.wait({pending}, timeout=heartbeat)
                if not done:
                    yield ": ping\n\n"
                    continue
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    pending = None
                    break
                pending = asyncio.ensure_future(agen.__anext__())
                for name, data in translator.translate(event):
                    yield sse(name, data)
        except Exception as error:  # never leak internals to the browser
            log.exception("Chat turn failed for conversation %s", conversation_id)
            message = f"The assistant could not finish this answer ({type(error).__name__})."
            translator.errors.append(message)
            yield sse("error", {"message": message})
        status = "error" if translator.errors and not translator.text.strip() else "complete"
        message = persist(status)
        yield sse("done", {"conversation_id": conversation_id, "message_id": message["message_id"], "status": status})
        if status == "complete":
            _spawn(remember(sessions, memory, sanitizer, app_name, user_id, session_id))
    finally:  # client disconnected or pressed Stop: keep what was streamed so far
        abort.set()
        if pending is not None and not pending.done():
            pending.cancel()
        if pending is not None or agen is not None:
            _spawn(_close(pending, agen))
        persist("stopped")


# Background turns ------------------------------------------------------------------------------------------
# A reply is generated by a task that outlives the HTTP request: switching conversation, closing the tab or losing the
# network only detaches the viewer, and Stop is an explicit call. Frames are kept in memory so a viewer can re-attach
# and replay them. This needs the instance to keep running after the response ends (Cloud Run CPU always allocated,
# as the run pipeline already requires); a re-attach that lands on another instance finds no turn and simply reads
# the saved conversation.
TURN_KEEP_SECONDS = 30.0


class TurnInProgress(Exception):
    pass


class Turn:
    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        self.frames: list[str] = []
        self.finished = False
        self.task: asyncio.Task | None = None
        self.changed = asyncio.Condition()

    async def publish(self, frame: str | None) -> None:
        async with self.changed:
            if frame is None:
                self.finished = True
            else:
                self.frames.append(frame)
            self.changed.notify_all()


_TURNS: dict[tuple[str, str], Turn] = {}


def active_turn(user_id: str, conversation_id: str) -> Turn | None:
    turn = _TURNS.get((user_id, conversation_id))
    return turn if turn is not None and not turn.finished else None


async def _produce(turn: Turn, key: tuple[str, str], stream: AsyncIterator[str]) -> None:
    try:
        async for frame in stream:
            if not frame.startswith(":"):  # heartbeats belong to each viewer, not to the replay buffer
                await turn.publish(frame)
    except asyncio.CancelledError:  # Stop: stream_turn's finally has saved the partial answer as "stopped"
        await turn.publish(sse("done", {"conversation_id": turn.conversation_id, "message_id": "", "status": "stopped"}))
    finally:
        with suppress(BaseException):
            await stream.aclose()
        await turn.publish(None)
        asyncio.get_running_loop().call_later(TURN_KEEP_SECONDS, lambda: _TURNS.get(key) is turn and _TURNS.pop(key, None))


def start_turn(*, user_id: str, conversation: dict, **kwargs: Any) -> Turn:
    """Begin a reply in the background (one at a time per conversation)."""
    key = (user_id, conversation["conversation_id"])
    if active_turn(*key) is not None:
        raise TurnInProgress()
    turn = Turn(conversation["conversation_id"])
    _TURNS[key] = turn
    stream = stream_turn(user_id=user_id, conversation=conversation, **kwargs)
    turn.task = asyncio.ensure_future(_produce(turn, key, stream))
    return turn


async def follow(turn: Turn, start: int = 0, heartbeat: float = HEARTBEAT_SECONDS) -> AsyncIterator[str]:
    """Replay a turn's frames from ``start`` and then follow it live; ends when the turn does."""
    index = start
    while True:
        while index < len(turn.frames):
            yield turn.frames[index]
            index += 1
        if turn.finished:
            return
        try:
            async with turn.changed:
                await asyncio.wait_for(turn.changed.wait_for(lambda: len(turn.frames) > index or turn.finished), heartbeat)
        except asyncio.TimeoutError:
            yield ": ping\n\n"


def stop_turn(user_id: str, conversation_id: str) -> bool:
    turn = active_turn(user_id, conversation_id)
    if turn is None or turn.task is None:
        return False
    turn.task.cancel()
    return True
