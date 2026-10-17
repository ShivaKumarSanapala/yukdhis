"""Runs survive deploys honestly, large PDFs are split for Document AI, and a Reader failure stops the pipeline."""

import io
import os
import tempfile
import time
import types

os.environ.update(YUKDHIS_RUNTIME_DIR=tempfile.mkdtemp(prefix="yukdhis-test-"), YUKDHIS_BACKEND="local",
                  AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import pytest  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402
from sqlalchemy import insert, update  # noqa: E402

import api.runs as runs  # noqa: E402
from core.app_store import get_app_store, run_meta, runs as runs_table  # noqa: E402
from ingestion import documents  # noqa: E402


def _pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# stale runs ---------------------------------------------------------------------------
def _legacy_run(store, status: str, updated_at: str) -> str:
    """A run created before run_meta existed (no heartbeat), as left behind by an earlier deploy."""
    run_id = f"run-legacy{time.time_ns() % 10**9}"
    with store.engine.begin() as conn:
        conn.execute(insert(runs_table).values(run_id=run_id, tenant_id=store.tenant_id, status=status, mode="agentic",
                                               document="{}", created_at=updated_at, updated_at=updated_at))
    return run_id


def test_orphaned_runs_are_marked_interrupted(monkeypatch):
    store = get_app_store()
    legacy = _legacy_run(store, "RUNNING", "2026-10-16T22:13:02+00:00")
    recent_legacy = _legacy_run(store, "RUNNING", store_now())
    stale = store.create_run("deterministic", {"filename": "a.md"}, {"owner_id": "u1"})
    store.update_run(stale, status="RUNNING")
    with store.engine.begin() as conn:
        conn.execute(update(run_meta).where(run_meta.c.run_id == stale).values(heartbeat_at=int(time.time()) - 3600))
    alive = store.create_run("deterministic", {"filename": "b.md"}, {"owner_id": "u1"})
    store.update_run(alive, status="RUNNING")  # fresh heartbeat
    local = store.create_run("deterministic", {"filename": "c.md"}, {"owner_id": "u1"})
    store.update_run(local, status="RUNNING")
    with store.engine.begin() as conn:
        conn.execute(update(run_meta).where(run_meta.c.run_id == local).values(heartbeat_at=int(time.time()) - 3600))
    monkeypatch.setitem(runs._LOCAL, local, runs._Local())  # still executing on this instance

    reaped = runs.reap_stale_runs(force=True)

    assert set(reaped) == {legacy, stale}
    for run_id in (legacy, stale):
        run = store.get_run(run_id)
        assert run["status"] == "FAILED" and "Interrupted" in run["error"]
        assert store.events(run_id)[-1]["kind"] == "run_failed"
    assert store.get_run(alive)["status"] == "RUNNING"
    assert store.get_run(recent_legacy)["status"] == "RUNNING"
    assert store.get_run(local)["status"] == "RUNNING"
    assert runs.reap_stale_runs() == []  # throttled
    for run_id in (alive, recent_legacy, local):  # leave no active runs behind for other test modules
        store.update_run(run_id, status="SUCCEEDED")


def store_now() -> str:
    from core.app_store import now
    return now()


def test_shutdown_interrupts_runs_executing_here(monkeypatch):
    store = get_app_store()
    run_id = store.create_run("deterministic", {"filename": "d.md"}, {"owner_id": "u1"})
    store.update_run(run_id, status="RUNNING")
    pending = types.SimpleNamespace(done=lambda: False)
    monkeypatch.setitem(runs._LOCAL, run_id, runs._Local(task=pending))
    assert runs.interrupt_local_runs() == [run_id]
    assert store.get_run(run_id)["status"] == "FAILED"


# Document AI page limit -------------------------------------------------------------------
def test_pdf_parts_split_at_the_document_ai_limit():
    small = _pdf(5)
    assert documents.pdf_parts(small) == [(0, small)]
    parts = documents.pdf_parts(_pdf(35))
    assert [offset for offset, _ in parts] == [0, 30]
    assert [len(PdfReader(io.BytesIO(part)).pages) for _, part in parts] == [30, 5]
    with pytest.raises(ValueError, match="limit is 40"):
        documents.pdf_parts(_pdf(41), max_pages=40)
    with pytest.raises(ValueError, match="could not be read"):
        documents.pdf_parts(b"%PDF-1.7 broken")


def test_document_ai_processes_each_part_and_offsets_pages(monkeypatch):
    from google.cloud import documentai

    seen = []

    class FakeClient:
        def __init__(self, **_):
            pass

        def process_document(self, request):
            pages = len(PdfReader(io.BytesIO(request.raw_document.content)).pages)
            seen.append(pages)
            chunk = types.SimpleNamespace(chunk_id="c1", content=f"Part with {pages} pages",
                                          page_span=types.SimpleNamespace(page_start=1, page_end=pages))
            return types.SimpleNamespace(document=types.SimpleNamespace(chunked_document=types.SimpleNamespace(chunks=[chunk])))

    monkeypatch.setattr(documentai, "DocumentProcessorServiceClient", FakeClient)
    settings = types.SimpleNamespace(documentai_processor="projects/p/locations/us/processors/x", require=lambda *_: None)
    parsed = documents._document_ai(_pdf(35), "application/pdf", "doc1", "upload://big.pdf", "big.pdf", settings)
    assert seen == [30, 5]
    assert [(c.chunk_id, c.page_start, c.page_end) for c in parsed.chunks] == [("doc1:p0-c1", 1, 30), ("doc1:p1-c1", 31, 35)]


# Reader failure halts the agentic pipeline ----------------------------------------------------
def test_reader_failure_halts_later_agents():
    from agents.compliance_coordinator.agent import root_agent
    from agents.compliance_coordinator.sub_agents._common import skip_when_halted
    from agents.compliance_coordinator.tools import HALT_KEY, _halt_on_error

    context = types.SimpleNamespace(state={})

    def boom():
        raise ValueError("Document pages exceed the limit")

    with pytest.raises(ValueError):
        _halt_on_error(context, boom)
    assert "exceed" in context.state[HALT_KEY]
    skipped = skip_when_halted(context)
    assert skipped and "could not be read" in skipped.parts[0].text
    assert _halt_on_error(context, lambda: {"ok": 1}) == {"ok": 1} and context.state[HALT_KEY] is None
    assert skip_when_halted(context) is None
    downstream = [a for a in root_agent.sub_agents if a.name != "reader_agent"]
    assert downstream and all(a.before_agent_callback is skip_when_halted for a in downstream)
