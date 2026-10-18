"""Circular retrieval index: indexing is idempotent and tenant-scoped, search ranks by meaning, the assistant tool is wired."""

import os
import tempfile

os.environ.update(YUKDHIS_RUNTIME_DIR=tempfile.mkdtemp(prefix="yukdhis-test-"), YUKDHIS_BACKEND="local",
                  AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import pytest  # noqa: E402

from core import circular_index  # noqa: E402
from core.app_store import AppStore, set_app_store  # noqa: E402
from core.models import DocumentChunk, ParsedDocument  # noqa: E402

WORDS = ["gold", "release", "documents", "interest", "reset", "capital"]


def fake_embed(texts, query=False):
    """Bag-of-words over a tiny vocabulary: texts about the same words are close."""
    return [[float(t.lower().count(w)) for w in WORDS] + [0.01] for t in texts]


def doc(doc_id="doc-1", chunks=None):
    chunks = chunks or [("c1", "Gold loans: the bank must release gold ornaments on repayment."),
                        ("c2", "Floating interest rate reset and EMI changes."),
                        ("c3", "Capital adequacy for credit valuation adjustment.")]
    return ParsedDocument(document_id=doc_id, source_uri="upload://x", mime_type="text/markdown", title="Test circular", parser="local",
                          chunks=[DocumentChunk(chunk_id=f"{doc_id}:{cid}", content=text, headings=["H"], page_start=1, page_end=1)
                                  for cid, text in chunks])


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    app = AppStore(url=f"sqlite:///{tmp_path / 'app.db'}")
    set_app_store(app)
    monkeypatch.setenv("CIRCULAR_INDEX", "1")  # scoped to these tests: other tests must not call the embedding service
    monkeypatch.setattr(circular_index, "embed_texts", fake_embed)
    yield app
    set_app_store(None)


def test_index_is_idempotent_and_search_ranks_by_meaning():
    assert circular_index.index_document(doc()) == 3
    assert circular_index.index_document(doc()) == 0  # already indexed
    assert circular_index.index_document(doc(), force=True) == 3
    top = circular_index.search("releasing gold documents", top_k=2)
    assert top[0]["chunk_id"] == "doc-1:c1" and top[0]["score"] > top[1]["score"]
    assert top[0]["title"] == "Test circular" and top[0]["pages"] == [1, 1]
    assert circular_index.search("capital", top_k=1)[0]["chunk_id"] == "doc-1:c3"


def test_search_can_be_restricted_to_one_document():
    circular_index.index_document(doc("doc-1"))
    circular_index.index_document(doc("doc-2", [("a", "Gold gold gold release")]))
    hits = circular_index.search("gold", top_k=5, document_id="doc-1")
    assert {h["document_id"] for h in hits} == {"doc-1"}


def test_index_is_tenant_scoped(store):
    circular_index.index_document(doc())
    store.tenant_id = "another-bank"
    assert circular_index.search("gold") == []


def test_personal_identifiers_are_masked_before_storage():
    circular_index.index_document(doc("doc-pii", [("p", "Contact rahul.verma@example.com about PAN ABCPV1234K for gold loans.")]))
    stored = circular_index.search("gold", top_k=1)[0]["text"]
    assert "rahul.verma@example.com" not in stored and "ABCPV1234K" not in stored


def test_indexing_failure_never_raises(monkeypatch):
    def boom(texts, query=False):
        raise RuntimeError("embedding service down")

    monkeypatch.setattr(circular_index, "embed_texts", boom)
    assert circular_index.index_document_safely(doc()) == 0


def test_assistant_tool_returns_clipped_passages_and_is_registered():
    from agents.compliance_assistant.agent import ASSISTANT_TOOLS
    from agents.compliance_assistant.tools import search_circulars

    assert search_circulars in ASSISTANT_TOOLS
    circular_index.index_document(doc())
    original = circular_index.embed_texts
    result = search_circulars("release gold", top_k=2)
    assert result["status"] == "success" and result["passages"][0]["chunk_id"] == "doc-1:c1"
    assert search_circulars("   ")["status"] == "error"
    assert circular_index.embed_texts is original
