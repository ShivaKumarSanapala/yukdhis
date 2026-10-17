"""Assistant chat: SSE framing/translation (mocked ADK runner, no LLM), persistence and per-user isolation."""

import asyncio
import dataclasses
import json
import os
import tempfile

os.environ.update(YUKDHIS_RUNTIME_DIR=tempfile.mkdtemp(prefix="yukdhis-test-"), YUKDHIS_BACKEND="local",
                  AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import pytest  # noqa: E402
from google.adk.events import Event  # noqa: E402
from google.adk.sessions import Session  # noqa: E402
from google.genai import types  # noqa: E402

from api.auth import User, current_user  # noqa: E402
from api.chat import TraceTranslator, clip, sanitized_session, stream_turn  # noqa: E402
from core.app_store import AppStore, set_app_store  # noqa: E402
from privacy.sanitizer import Sanitizer  # noqa: E402

AUTHOR = "compliance_assistant"
ALICE, BOB = User("user-a", "a@bank.test"), User("user-b", "b@bank.test")


def ev(*parts, partial=False, role="model", **extra):
    return Event(author=AUTHOR, partial=partial, content=types.Content(role=role, parts=list(parts)), **extra)


def script():
    call = types.FunctionCall(id="fc-1", name="list_impact_reports", args={})
    response = types.FunctionResponse(id="fc-1", name="list_impact_reports",
                                      response={"reports": [{"report_id": "rpt-1", "note": "mail rahul@example.com"}]})
    return [
        ev(types.Part(text="Looking up reports", thought=True), partial=True),
        ev(types.Part(text="Looking up reports", thought=True), types.Part(function_call=call)),
        ev(types.Part(function_response=response), role="user"),
        ev(types.Part(text="There are "), partial=True),
        ev(types.Part(text="**2** reports (report rpt-1)."), partial=True),
        ev(types.Part(text="There are **2** reports (report rpt-1).")),  # aggregate: must not be re-emitted
    ]


class FakeRunner:
    def __init__(self, events=None, delay=0.0):
        self.events, self.delay, self.calls = events or script(), delay, []

    async def run_async(self, *, user_id, session_id, new_message, run_config, abort_signal):
        self.calls.append({"user_id": user_id, "session_id": session_id, "text": new_message.parts[0].text,
                           "streaming": run_config.streaming_mode.value})
        for event in self.events:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield event


class FakeSessions:
    def __init__(self):
        self.store = {}

    async def get_session(self, *, app_name, user_id, session_id):
        return self.store.get((app_name, user_id, session_id))

    async def create_session(self, *, app_name, user_id, session_id, state=None):
        session = Session(id=session_id, app_name=app_name, user_id=user_id)
        self.store[(app_name, user_id, session_id)] = session
        return session

    async def delete_session(self, *, app_name, user_id, session_id):
        self.store.pop((app_name, user_id, session_id), None)


class FakeMemory:
    def __init__(self):
        self.sessions = []

    async def add_session_to_memory(self, session):
        self.sessions.append(session)


def frames(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.split("\n\n"):
        lines = [line for line in block.split("\n") if line and not line.startswith(":")]
        if lines:
            fields = dict(line.split(": ", 1) for line in lines)
            out.append((fields["event"], json.loads(fields["data"])))
    return out


@pytest.fixture()
def store(tmp_path):
    app_store = AppStore(url=f"sqlite:///{tmp_path / 'app.db'}")
    set_app_store(app_store)
    yield app_store
    set_app_store(None)


@pytest.fixture()
def chat(store, monkeypatch):
    import api.main as main
    from fastapi.testclient import TestClient

    runner, sessions, memory = FakeRunner(), FakeSessions(), FakeMemory()
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, project="test-project"))
    monkeypatch.setattr(main, "assistant_runner", lambda: runner)
    monkeypatch.setattr(main, "session_service", lambda: sessions)
    monkeypatch.setattr(main, "memory_service", lambda: memory)
    main.app.dependency_overrides[current_user] = lambda: ALICE
    with TestClient(main.app) as client:
        yield client, runner, sessions
    main.app.dependency_overrides.clear()


def test_translator_maps_adk_stream_to_typed_events():
    translator = TraceTranslator(scrub=lambda v: Sanitizer().sanitize_value(v, "s1"), clock=iter([1.0, 1.25]).__next__)
    out = [pair for event in script() for pair in translator.translate(event)]
    assert [name for name, _ in out] == ["thinking", "tool_call", "tool_result", "text", "text"]
    assert out[1][1] == {"id": "fc-1", "name": "list_impact_reports", "args": {}}
    result = out[2][1]
    assert result["status"] == "ok" and result["duration_ms"] == 250 and "rahul@example.com" not in json.dumps(result)
    assert translator.text == "There are **2** reports (report rpt-1)." and translator.thinking == "Looking up reports"


def test_translator_non_streaming_fallback_errors_and_paragraph_gap():
    translator = TraceTranslator()
    assert translator.translate(ev(types.Part(text="Let me check."))) == [("text", {"delta": "Let me check."})]
    translator.translate(ev(types.Part(function_call=types.FunctionCall(id="c1", name="run_safe_sql", args={"sql": "x"}))))
    assert translator.translate(ev(types.Part(text="Done."), partial=True)) == [("text", {"delta": "\n\nDone."})]
    stopped = translator.trace(stopped=True)["tool_calls"][0]
    assert stopped["status"] == "cancelled"
    failed = translator.translate(Event(author=AUTHOR, error_code="MAX_TOKENS"))
    assert failed[0][0] == "error" and "MAX_TOKENS" in failed[0][1]["message"]
    assert clip({"x": "y" * 9000})["truncated"] is True


def test_stream_endpoint_frames_persists_and_continues(chat, store):
    client, runner, sessions = chat
    response = client.post("/api/chat/stream", json={"text": "How many reports   do we have?", "context": {"report_id": "rpt-1"}})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    events = frames(response.text)
    names = [name for name, _ in events]
    assert names == ["start", "thinking", "tool_call", "tool_result", "text", "text", "done"]
    start, done = events[0][1], events[-1][1]
    conversation_id = start["conversation_id"]
    assert start["created"] is True and start["title"] == "How many reports do we have?"
    assert done == {"conversation_id": conversation_id, "message_id": done["message_id"], "status": "complete"}
    assert runner.calls[0]["session_id"] == conversation_id and runner.calls[0]["streaming"] == "sse"
    assert runner.calls[0]["text"].startswith("[UI context: the user currently has impact report rpt-1 open.]")

    follow_up = frames(client.post("/api/chat/stream", json={"text": "And last week?", "conversation_id": conversation_id}).text)
    assert follow_up[0][1]["created"] is False and runner.calls[1]["session_id"] == conversation_id

    body = client.get(f"/api/conversations/{conversation_id}").json()
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user", "assistant"]
    assert body["messages"][0]["content"] == "How many reports   do we have?"  # stored without the UI-context prefix
    answer = body["messages"][1]
    assert answer["content"] == "There are **2** reports (report rpt-1)." and answer["status"] == "complete"
    assert answer["trace"]["thinking"] == "Looking up reports"
    assert answer["trace"]["tool_calls"][0]["name"] == "list_impact_reports"
    assert "rahul@example.com" not in json.dumps(answer["trace"])
    listed = client.get("/api/conversations").json()
    assert [(c["conversation_id"], c["message_count"]) for c in listed] == [(conversation_id, 4)]
    assert client.patch(f"/api/conversations/{conversation_id}", json={"title": "  Report count "}).json()["title"] == "Report count"


def test_conversations_are_isolated_per_user(chat, store):
    import api.main as main

    client, _, sessions = chat
    conversation_id = frames(client.post("/api/chat/stream", json={"text": "Alice's question"}).text)[0][1]["conversation_id"]
    main.app.dependency_overrides[current_user] = lambda: BOB
    assert client.get("/api/conversations").json() == []
    assert client.get(f"/api/conversations/{conversation_id}").status_code == 404
    assert client.patch(f"/api/conversations/{conversation_id}", json={"title": "mine"}).status_code == 404
    assert client.delete(f"/api/conversations/{conversation_id}").status_code == 404
    assert client.post("/api/chat/stream", json={"text": "hi", "conversation_id": conversation_id}).status_code == 404
    assert store.chat_messages(conversation_id, BOB.user_id) == []
    main.app.dependency_overrides[current_user] = lambda: ALICE
    assert client.get(f"/api/conversations/{conversation_id}").json()["title"] == "Alice's question"
    assert client.delete(f"/api/conversations/{conversation_id}").json()["deleted"] is True
    assert client.get(f"/api/conversations/{conversation_id}").status_code == 404
    assert not any(key[2] == conversation_id for key in sessions.store)


def test_stream_without_llm_is_a_clean_error_event(store, monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    from fastapi.testclient import TestClient

    import api.main as main

    with TestClient(main.app) as client:
        response = client.post("/api/chat/stream", json={"text": "hello"})
        assert response.status_code == 200
        assert [name for name, _ in frames(response.text)] == ["error"]
        assert client.get("/api/conversations").json() == []
        assert client.post("/api/chat/stream", json={"text": "hello", "conversation_id": "bad id!"}).status_code == 422


def test_stop_keeps_partial_answer_and_heartbeats(store):
    conversation = store.create_conversation(ALICE.user_id, "Stop me")
    sanitizer, sessions = Sanitizer(), FakeSessions()

    async def consume():
        stream = stream_turn(store=store, runner=FakeRunner(script()[:4], delay=0.03), sessions=sessions,
                             memory=FakeMemory(), sanitizer=sanitizer, app_name="app", user_id=ALICE.user_id,
                             conversation=conversation, created=False, question="q", prompt="q", heartbeat=0.01)
        seen = []
        async for frame in stream:
            seen.append(frame)
            if frame.startswith("event: text"):
                await stream.aclose()  # what Starlette does when the browser aborts the fetch
                break
        return seen

    seen = asyncio.run(consume())
    assert any(frame.startswith(": ping") for frame in seen)
    answer = store.chat_messages(conversation["conversation_id"], ALICE.user_id)[-1]
    assert answer["role"] == "assistant" and answer["status"] == "stopped" and answer["content"] == "There are "


def test_memory_copy_is_sanitized_and_drops_thoughts():
    session = Session(id="s-mem", app_name="app", user_id="u", events=[
        ev(types.Part(text="PAN ABCPV1234K for rahul@example.com"), role="user"),
        ev(types.Part(text="secret plan", thought=True), types.Part(text="ok")),
    ])
    copy = sanitized_session(session, Sanitizer())
    dumped = copy.model_dump_json()
    assert "ABCPV1234K" not in dumped and "rahul@example.com" not in dumped and "secret plan" not in dumped
    assert session.events[0].content.parts[0].text.startswith("PAN ABCPV1234K")  # original untouched
