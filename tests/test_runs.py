"""Analysis runs: queue and per-user cap, same-document serialization, cancel, human review, new inputs, metadata.

No network and no LLM: queue/cancel tests swap the deterministic runner for a gated fake; review, text and multi-file
tests run the real deterministic pipeline on the bundled sample circulars.
"""

import io
import os
import sys
import tempfile
import threading
import time
import types
import zipfile
from pathlib import Path

os.environ.update(YUKDHIS_RUNTIME_DIR=tempfile.mkdtemp(prefix="yukdhis-test-"), YUKDHIS_BACKEND="local",
                  AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import api.runs as runs  # noqa: E402
from api.auth import User, current_user  # noqa: E402
from api.main import app, content_problem  # noqa: E402
from core.app_store import get_app_store, runs as runs_table  # noqa: E402

CIRCULARS = Path("data/circulars")
DOCS = "rbi_2023_09_13_release_of_property_documents.md"
FLOATING = "rbi_2023_08_18_floating_rate_reset_emi_loans.md"
DONE = {"SUCCEEDED", "FAILED", "CANCELLED", "REJECTED", "AWAITING_REVIEW"}


class GatedPipeline:
    """Stand-in for ``run_pipeline``: emits a stage, then waits (still emitting, so cancel is observed) until released."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def __call__(self, source, as_of=None, emit=None, *, preset=None, stop_after=None):
        with self.lock:
            self.calls.append(source["filename"])
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            emit("reader", "stage_started", {"label": "Parse document"})
            while not self.release.wait(0.03):
                emit("reader", "tool_call", {"tool": "waiting"})
            emit("reader", "stage_completed", {"label": "Parse document", "output": {}})
            return {"report_id": f"rpt-fake-{len(self.calls)}"}
        finally:
            with self.lock:
                self.active -= 1


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(runs, "POLL_SECONDS", 0.05)


@pytest.fixture
def gated(monkeypatch, fast):
    fake = GatedPipeline()
    monkeypatch.setattr(runs, "run_pipeline", fake)
    yield fake
    fake.release.set()


def wait_for(client, run_id, statuses=DONE, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()["run"]
        if run["status"] in statuses:
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} stuck in {run['status']}")


def kinds(client, run_id):
    return [e["kind"] for e in client.get(f"/api/runs/{run_id}").json()["events"]]


def start_text(client, text, **data):
    response = client.post("/api/runs", data={"text": text, "mode": "deterministic", **data})
    assert response.status_code == 200, response.text
    return response.json()["run_id"]


def test_queue_then_running_and_per_user_cap(monkeypatch, gated):
    monkeypatch.setattr(runs, "MAX_CONCURRENT_RUNS", 1)
    monkeypatch.setattr(runs, "MAX_ACTIVE_RUNS_PER_USER", 3)
    with TestClient(app) as client:
        first = start_text(client, "# Circular A\n\n- Banks shall do A.")
        wait_for(client, first, {"RUNNING"})
        second = start_text(client, "# Circular B\n\n- Banks shall do B.")
        queued = wait_for(client, second, {"QUEUED"})
        time.sleep(0.3)
        queued = client.get(f"/api/runs/{second}").json()
        assert queued["run"]["status"] == "QUEUED" and queued["run"]["queued_position"] == 1
        event = next(e for e in queued["events"] if e["kind"] == "run_queued")
        assert event["payload"]["position"] == 1
        assert len(gated.calls) == 1  # only the first one executes

        over = client.post("/api/runs", data={"text": "# C\n\n- c", "sample_ids": [DOCS], "mode": "deterministic"})
        assert over.status_code == 429 and "limit of 3" in over.json()["detail"]

        gated.release.set()
        assert wait_for(client, first)["status"] == "SUCCEEDED"
        done = wait_for(client, second)
        assert done["status"] == "SUCCEEDED" and done["queued_position"] is None and done["started_at"]
        assert kinds(client, second).index("run_queued") < kinds(client, second).index("run_started")
        assert gated.max_active == 1


def test_runs_of_the_same_document_are_serialized(gated):
    with TestClient(app) as client:
        first = client.post("/api/runs", data={"sample_id": DOCS, "mode": "deterministic"}).json()["run_id"]
        wait_for(client, first, {"RUNNING"})
        second = client.post("/api/runs", data={"sample_id": DOCS, "mode": "deterministic"}).json()["run_id"]
        other = start_text(client, "# Unrelated circular\n\n- Banks shall do X.")
        wait_for(client, other, {"RUNNING"})  # a different document is not held up
        time.sleep(0.3)
        body = client.get(f"/api/runs/{second}").json()
        assert body["run"]["status"] == "QUEUED"
        waiting = next(e for e in body["events"] if e["kind"] == "run_waiting")
        assert waiting["payload"]["blocking_run_id"] == first and "same document" in waiting["payload"]["message"]
        assert gated.calls.count(DOCS) == 1

        gated.release.set()
        for run_id in (first, second, other):
            assert wait_for(client, run_id)["status"] == "SUCCEEDED"
        a, b = (client.get(f"/api/runs/{r}").json()["run"] for r in (first, second))
        assert a["doc_sha256"] == b["doc_sha256"] and len(a["doc_sha256"]) == 64
        assert first in b["previous_run_ids"] and b["previous_report_id"] == a["report_id"]
        assert gated.max_active == 2


def test_cancel_deterministic_run_and_queued_run_and_404_for_others(monkeypatch, gated):
    monkeypatch.setattr(runs, "MAX_CONCURRENT_RUNS", 1)
    with TestClient(app) as client:
        running = start_text(client, "# Cancel me\n\n- Banks shall stop.")
        wait_for(client, running, {"RUNNING"})
        queued = start_text(client, "# Queued one\n\n- Banks shall wait.")
        wait_for(client, queued, {"QUEUED"})

        app.dependency_overrides[current_user] = lambda: User(user_id="someone-else", email="other@bank.test")
        try:
            assert client.post(f"/api/runs/{running}/cancel").status_code == 404
            assert client.post(f"/api/runs/{running}/review", json={"decision": "REJECT"}).status_code == 404
            assert client.get(f"/api/runs/{running}").status_code == 200  # same tenant may still read it
        finally:
            app.dependency_overrides.clear()

        assert client.post(f"/api/runs/{queued}/cancel").json()["status"] == "CANCEL_REQUESTED"
        assert wait_for(client, queued)["status"] == "CANCELLED"
        assert client.post(f"/api/runs/{running}/cancel").json()["status"] == "CANCEL_REQUESTED"
        cancelled = wait_for(client, running)
        assert cancelled["status"] == "CANCELLED" and cancelled["finished_at"]
        assert "run_cancelled" in kinds(client, running) and "run_completed" not in kinds(client, running)
        assert client.post(f"/api/runs/{running}/cancel").json()["status"] == "CANCELLED"  # idempotent
        assert gated.calls == [gated.calls[0]]  # the queued run never executed
        audit = [a for a in get_app_store().audit_entries() if a["tool"] == "run_cancel"]
        assert len(audit) >= 2


def test_cancel_requested_on_another_instance_reaches_the_executor(gated):
    with TestClient(app) as client:
        run_id = start_text(client, "# Remote cancel\n\n- Banks shall stop elsewhere.")
        wait_for(client, run_id, {"RUNNING"})
        # another instance only writes the shared status; this instance's watcher must notice it
        assert get_app_store().transition_run(run_id, {"RUNNING"}, "CANCEL_REQUESTED")
        assert wait_for(client, run_id)["status"] == "CANCELLED"


def _start_review(client):
    run_id = client.post("/api/runs", data={"sample_id": DOCS, "mode": "deterministic", "review_rules": "true"}).json()["run_id"]
    run = wait_for(client, run_id)
    assert run["status"] == "AWAITING_REVIEW", run["error"]
    return run_id, run


def test_review_pause_then_approve_with_edits_then_reject(fast):
    with TestClient(app) as client:
        run_id, run = _start_review(client)
        assert run["review_rules"] and run["report_id"] is None and len(run["rules"]) == 3
        events = client.get(f"/api/runs/{run_id}").json()["events"]
        extracted = next(e for e in events if e["kind"] == "rules_extracted")
        assert len(extracted["payload"]["rules"]) == 3
        assert not any(e["kind"] == "stage_started" and e["stage"] == "mapper" for e in events)  # paused before mapping

        rules = run["rules"]
        edited = [{**rules[0], "title": "Release documents within 30 days (reviewed)", "modality": "MUST"},
                  {**rules[1], "effective_date": "2024-01-01"}]  # rules[2] removed
        response = client.post(f"/api/runs/{run_id}/review", json={"decision": "APPROVE", "rules": edited})
        assert response.status_code == 200, response.text
        assert response.json()["removed"] == [rules[2]["rule_id"]] and len(response.json()["edited"]) == 2
        assert client.post(f"/api/runs/{run_id}/review", json={"decision": "APPROVE"}).status_code == 409

        done = wait_for(client, run_id, {"SUCCEEDED", "FAILED", "CANCELLED"})
        assert done["status"] == "SUCCEEDED", done["error"]
        report = client.get(f"/api/reports/{done['report_id']}").json()
        assert [r["rule_id"] for r in report["rules"]] == [rules[0]["rule_id"], rules[1]["rule_id"]]
        assert report["rules"][0]["title"] == "Release documents within 30 days (reviewed)"
        assert report["rules"][1]["effective_date"] == "2024-01-01"
        assert report["rules"][0]["checks"] == rules[0]["checks"]  # checks stay as extracted
        all_kinds = kinds(client, run_id)
        assert all_kinds.count("run_started") == 1 and "run_resumed" in all_kinds and "rules_reviewed" in all_kinds
        assert "stage_skipped" in all_kinds
        seqs = [e["seq"] for e in client.get(f"/api/runs/{run_id}").json()["events"]]
        assert seqs == sorted(set(seqs))  # the resumed run continues the event sequence

        rejected_id, _ = _start_review(client)
        response = client.post(f"/api/runs/{rejected_id}/review", json={"decision": "REJECT", "comment": "wrong circular"})
        assert response.json()["status"] == "REJECTED"
        rejected = client.get(f"/api/runs/{rejected_id}").json()["run"]
        assert rejected["status"] == "REJECTED" and rejected["report_id"] is None and rejected["finished_at"]


def test_invalid_edited_rules_return_422(fast):
    with TestClient(app) as client:
        run_id, run = _start_review(client)
        rule = run["rules"][0]
        review = lambda rules: client.post(f"/api/runs/{run_id}/review", json={"decision": "APPROVE", "rules": rules})  # noqa: E731
        assert review([{**rule, "modality": "SHOULD"}]).status_code == 422          # not a Modality
        assert review([{**rule, "effective_date": "2024-13-45"}]).status_code == 422  # validator: not an ISO date
        assert review([{**rule, "rule_id": "rule:invented"}]).status_code == 422     # rules cannot be added
        assert review([rule, rule]).status_code == 422                               # duplicate
        assert review([{**rule, "title": "  "}]).status_code == 422
        assert review([]).status_code == 422
        assert client.post(f"/api/runs/{run_id}/review", json={"decision": "MAYBE"}).status_code == 422
        assert client.get(f"/api/runs/{run_id}").json()["run"]["status"] == "AWAITING_REVIEW"
        assert client.post(f"/api/runs/{run_id}/cancel").json()["status"] == "CANCELLED"  # paused runs cancel at once


def test_text_and_url_inputs(monkeypatch, fast):
    content = (CIRCULARS / DOCS).read_text()
    calls = []

    def fetch_circular(url):
        calls.append(url)
        if "bad" in url:
            raise ValueError("Only rbi.org.in circulars can be fetched")
        return {"path": str((CIRCULARS / DOCS).resolve()), "filename": "rbi-fetched.md", "display_uri": url, "trigger": "url"}

    monkeypatch.setitem(sys.modules, "ingestion.fetch", types.SimpleNamespace(fetch_circular=fetch_circular))
    with TestClient(app) as client:
        text_run = wait_for(client, start_text(client, content))
        assert text_run["status"] == "SUCCEEDED", text_run["error"]
        assert text_run["source_kind"] == "text" and text_run["filename"].startswith("pasted-circular-")
        assert text_run["filename"].endswith(".md")

        url = "https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12524"
        response = client.post("/api/runs", data={"url": url, "mode": "deterministic"})
        assert response.status_code == 200, response.text
        url_run = wait_for(client, response.json()["run_id"])
        assert url_run["status"] == "SUCCEEDED" and url_run["source_kind"] == "url" and calls == [url]
        assert len(url_run["doc_sha256"]) == 64 and url_run["filename"] == "rbi-fetched.md"

        bad = client.post("/api/runs", data={"url": "https://bad.example/x", "mode": "deterministic"})
        assert bad.status_code == 400 and "rbi.org.in" in bad.json()["detail"]
        assert client.post("/api/runs", data={"url": "file:///etc/passwd"}).status_code == 400
        assert client.post("/api/runs", data={"text": "x" * (500 * 1024 + 1)}).status_code == 413
        assert client.post("/api/runs", data={"mode": "deterministic"}).status_code == 422


def test_multi_file_start_creates_one_run_per_item(fast):
    files = [("files", (name, (CIRCULARS / name).read_bytes(), "text/markdown")) for name in (DOCS, FLOATING)]
    with TestClient(app) as client:
        response = client.post("/api/runs", data={"sample_ids": [FLOATING], "mode": "deterministic"}, files=files)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["run_ids"]) == 3 == len(set(body["run_ids"])) and body["run_id"] == body["run_ids"][0]
        finished = [wait_for(client, run_id) for run_id in body["run_ids"]]
        assert [r["status"] for r in finished] == ["SUCCEEDED"] * 3, [r["error"] for r in finished]
        assert sorted(r["source_kind"] for r in finished) == ["sample", "upload", "upload"]
        assert len({r["report_id"] for r in finished}) == 3


def _docx(body: bytes = b"<w:document/>") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", body)
    return buffer.getvalue()


def test_magic_bytes_must_match_the_declared_type():
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert content_problem(b"%PDF-1.7\n...", "application/pdf") is None
    assert content_problem(_docx(), docx) is None
    assert content_problem("# Circular\n\n- Banks shall …".encode(), "text/markdown") is None
    assert content_problem(b"hello", "application/pdf")
    assert content_problem(b"plain text", docx)
    zip_only = io.BytesIO()
    with zipfile.ZipFile(zip_only, "w") as archive:
        archive.writestr("notes.txt", "not word")
    assert content_problem(zip_only.getvalue(), docx)
    assert content_problem(b"\x89PNG\r\n\x1a\n\x00\x00", "text/markdown")
    assert content_problem(b"%PDF-1.4 disguised", "text/markdown")
    assert content_problem(b"abc\x00def", "text/plain")
    assert content_problem(b"\xff\xfe\xfa", "text/markdown")
    with TestClient(app) as client:
        for name, data in (("fake.pdf", b"not a pdf"), ("binary.md", b"\x89PNG\r\n\x1a\n\x00\x00\x00"),
                           ("fake.docx", b"just text"), ("empty.md", b"   ")):
            response = client.post("/api/runs", files={"file": (name, data)}, data={"mode": "deterministic"})
            assert response.status_code == 400 and name in response.json()["detail"], (name, response.text)
        assert client.post("/api/runs", files={"file": ("x.exe", b"MZ")}).status_code == 415


def test_run_metadata_filters_watcher_runs_and_old_rows(monkeypatch, fast):
    def pipeline(source, as_of=None, emit=None, **_):
        emit("orchestrator", "stage_started", {"label": "Compile impact report"})
        return {"report_id": "rpt-watcher"}

    monkeypatch.setattr(runs, "run_pipeline", pipeline)
    monkeypatch.setattr(runs, "_source_sha", lambda source: "a" * 64)
    import api.main as main

    monkeypatch.setattr(main, "list_incoming", lambda: [{"gcs_uri": "gs://circulars/incoming/new.md",
                                                          "filename": "new.md", "trigger": "watcher"}])
    store = get_app_store()
    with store.engine.begin() as conn:  # a run written before run metadata existed
        conn.execute(runs_table.insert().values(run_id="run-legacy0001", tenant_id=store.tenant_id, status="SUCCEEDED",
                                                mode="deterministic", document='{"filename": "old.md", "trigger": "sample", "user_id": "local-officer"}',
                                                report_id=None, created_at="2020-01-01T00:00:00+00:00",
                                                updated_at="2020-01-01T00:00:00+00:00"))
    with TestClient(app) as client:
        mine = wait_for(client, client.post("/api/runs", data={"sample_id": DOCS, "mode": "deterministic"}).json()["run_id"])
        for field in ("created_by", "source_kind", "filename", "doc_sha256", "current_stage", "started_at", "finished_at",
                      "queued_position", "previous_run_ids", "report_id", "is_mine"):
            assert field in mine, field
        assert mine["created_by"] == "officer@localhost" and mine["source_kind"] == "sample" and mine["is_mine"]
        assert mine["filename"] == DOCS and mine["current_stage"] == "orchestrator" and "owner_id" not in mine
        assert "source" not in mine and "checkpoint" not in mine

        started = client.post("/api/watch/poll").json()["started"]
        watcher = wait_for(client, started[0]["run_id"])
        assert watcher["source_kind"] == "watcher" and watcher["created_by"] == "watcher" and not watcher["is_mine"]
        assert client.post(f"/api/runs/{watcher['run_id']}/cancel").status_code == 404  # not the caller's run

        listed = client.get("/api/runs", params={"limit": 100}).json()
        created = [r["created_at"] for r in listed]
        assert created == sorted(created, reverse=True)
        legacy = next(r for r in listed if r["run_id"] == "run-legacy0001")
        assert legacy["source_kind"] == "sample" and legacy["filename"] == "old.md" and legacy["previous_run_ids"] == []
        assert "user_id" not in legacy["document"]

        assert all(r["is_mine"] for r in client.get("/api/runs", params={"mine": 1, "limit": 100}).json())
        assert watcher["run_id"] not in {r["run_id"] for r in client.get("/api/runs?mine=1&limit=100").json()}
        inbox = client.get("/api/runs", params={"source_kind": "watcher"}).json()
        assert [r["run_id"] for r in inbox] == [watcher["run_id"]]
        succeeded = client.get("/api/runs", params={"status": "SUCCEEDED,FAILED", "limit": 100}).json()
        assert succeeded and all(r["status"] in {"SUCCEEDED", "FAILED"} for r in succeeded)
        assert client.get("/api/runs", params={"status": "RUNNING"}).json() == []
        assert len(client.get("/api/runs", params={"limit": 1}).json()) == 1
        assert client.get("/api/runs", params={"status": "BOGUS"}).status_code == 422
        assert client.get("/api/runs", params={"limit": 0}).status_code == 422

        rerun = client.post(f"/api/runs/{watcher['run_id']}/rerun", json={"as_of": "2024-06-30"}).json()
        again = wait_for(client, rerun["run_id"])
        assert again["rerun_of"] == watcher["run_id"] and again["as_of"] == "2024-06-30" and again["is_mine"]
        assert client.post(f"/api/runs/{watcher['run_id']}/rerun", json={"as_of": "June"}).status_code == 422


def test_reader_is_skipped_when_rules_were_approved():
    from agents.compliance_coordinator.sub_agents.reader import skip_when_rules_approved

    context = types.SimpleNamespace(state={"source": {}})
    assert skip_when_rules_approved(context) is None
    context.state.update(rules_approved=True, extraction={"rules": [{"title": "Release within 30 days", "modality": "MUST",
                                                                       "effective_date": "2023-12-01"}]})
    content = skip_when_rules_approved(context)
    assert content is not None and "approved 1 extracted rule" in content.parts[0].text
