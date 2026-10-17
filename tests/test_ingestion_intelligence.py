"""Document-type classifier (L3), reference extraction, non-binding documents and the extraction cache (L8). No network, no LLM."""

import os
import tempfile
import zlib
from pathlib import Path

os.environ.update(YUKDHIS_RUNTIME_DIR=tempfile.mkdtemp(prefix="yukdhis-test-"), YUKDHIS_BACKEND="local",
                  AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import pytest  # noqa: E402

from core.config import get_settings  # noqa: E402
from core.models import ComplianceCheck, ExtractedPolicy, Policy, ProductScope, Rule, SourceCitation  # noqa: E402
from ingestion import extractor  # noqa: E402
from ingestion.cache import ExtractionCache  # noqa: E402
from ingestion.classifier import classify_text  # noqa: E402
from ingestion.documents import parse_document  # noqa: E402
from ingestion.references import announced_instrument, extract_references, pdf_link_uris, self_identifiers  # noqa: E402
from pipeline import stages  # noqa: E402
from pipeline.deterministic import run_pipeline  # noqa: E402

CIRCULARS = Path("data/circulars")
DIRECTIONS_URL = "https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=13001&Mode=0"
PRESS_RELEASE = f"""# Press Release: 2026-2027/1271

RBI issues Directions on 'Credit Valuation Adjustment (CVA) Framework'

The Reserve Bank had issued draft Directions on the CVA Framework on August 7, 2026 for public comments.
The draft Directions proposed allowing eligible banks to choose a simpler approach. Comments were incorporated.
The final Reserve Bank of India (Commercial Banks – Credit Valuation Adjustment Framework) Directions, 2026 are
available at {DIRECTIONS_URL}. They are effective from April 1, 2027.

(Puneet Pancholy) Chief General Manager
"""
DRAFT = """# Draft Reserve Bank of India (Commercial Banks – Credit Valuation Adjustment Framework) Directions, 2026

These draft Directions are issued for public comments.

## Proposals
- Eligible banks may choose a simpler approach for computing the CVA capital charge.
- Comments and feedback may be sent by email by August 28, 2026.
"""
MASTER_DIRECTION = """# Master Direction – Know Your Customer (KYC) Direction, 2016

RBI/DBR/2015-16/18 Master Direction DBR.AML.BC.No.81/14.01.001/2015-16

## Chapter I
- Regulated entities shall carry out customer due diligence.
"""
FINAL_AFTER_DRAFT = """# Reserve Bank of India (Commercial Banks – Credit Valuation Adjustment Framework) Directions, 2026

RBI/2026-27/180

A draft of these Directions was issued for public comments. After considering the feedback received, the final
Directions are issued as under.
- Banks shall compute CVA capital using the approach approved by their Board.
"""
FAQ = """# Frequently Asked Questions on the Foreign Exchange Risk Reserve

Q1. Which contracts are covered? A. Contracts where the AD buys foreign currency against INR.
"""
FERR_CIRCULAR = """# RBI/2026-27/293 — Risk Management and Inter-Bank Dealings

- Reference: A.P. (DIR Series) Circular No. 27
- Date: October 12, 2026
- Addressees: All Authorised Dealer Category-I banks

## Provisions
- AD Category-I banks shall maintain a Foreign Exchange Risk Reserve of 20% of the INR-equivalent notional of each FX derivative contract undertaken to hedge their own assets and liabilities.
- These directions amend paragraph 2.3(iii) of the Master Direction – Risk Management and Inter-Bank Dealings, dated July 5, 2016 (https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=10394).
- A.P. (DIR Series) Circular No. 12 dated June 1, 2025 is hereby superseded.
- See also https://example.com/not-rbi and RBI/2025-26/101.
"""
FERR_SNIPPET = ("AD Category-I banks shall maintain a Foreign Exchange Risk Reserve of 20% of the INR-equivalent notional of each "
                "FX derivative contract undertaken to hedge their own assets and liabilities.")


def _doc(text: str, name: str = "doc.md"):
    return parse_document(text.encode(), name)


def _classify(text: str):
    document = _doc(text)
    return classify_text(document.title, document.text)


@pytest.mark.parametrize("name", sorted(p.name for p in CIRCULARS.glob("*.md")))
def test_samples_are_circulars(name):
    result = _classify((CIRCULARS / name).read_text())
    assert result.doc_type == "circular" and result.confidence >= 0.8 and "RBI/YYYY-YY/NNN" in result.reason


@pytest.mark.parametrize("text, expected", [
    (PRESS_RELEASE, "press_release"), (DRAFT, "draft_or_consultation"), (MASTER_DIRECTION, "master_direction"),
    (FINAL_AFTER_DRAFT, "master_direction"), (FAQ, "faq"), (FERR_CIRCULAR, "circular"),
    ("Some unrelated note about lunch.", "other"),
])
def test_classifier_on_synthetic_snippets(text, expected):
    result = _classify(text)
    assert result.doc_type == expected, result
    assert result.reason and 0.0 <= result.confidence <= 1.0


def test_reference_extraction_relations_and_self_reference():
    document = _doc(FERR_CIRCULAR)
    refs = extract_references(document.text, [], self_identifiers(document.title, document.text))
    by_ref = {r.ref: r for r in refs}
    assert "RBI/2026-27/293" not in by_ref and "A.P. (DIR Series) Circular No. 27" not in by_ref  # the document itself
    master = next(r for r in refs if r.ref.startswith("Master Direction"))
    assert master.relation == "amends" and master.url == "https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=10394"
    assert by_ref["A.P. (DIR Series) Circular No. 12"].relation == "supersedes"
    assert by_ref["RBI/2025-26/101"].relation == "refers_to"
    assert not any("example.com" in (r.url or "") for r in refs)


def test_pdf_link_annotations_are_read_from_compressed_streams():
    annotation = b"<< /Type /Annot /Subtype /Link /A << /S /URI /URI (https://rbidocs.rbi.org.in/rdocs/notification/PDFs/NT27.PDF) >> >>"
    pdf = (b"%PDF-1.7\n1 0 obj\n<< /Length 10 /Filter /FlateDecode >>\nstream\n" + zlib.compress(annotation) +
           b"\nendstream\nendobj\n2 0 obj << /A << /URI (https://www.rbi.org.in/Scripts/PressReleaseDisplay.aspx?prid=1) >> >>"
           b"\nendobj\n%%EOF")
    uris = pdf_link_uris(pdf)
    assert set(uris) == {"https://rbidocs.rbi.org.in/rdocs/notification/PDFs/NT27.PDF",
                         "https://www.rbi.org.in/Scripts/PressReleaseDisplay.aspx?prid=1"}
    refs = extract_references("Press release text", uris)
    assert announced_instrument(refs).url.endswith("NT27.PDF")
    assert pdf_link_uris(b"not a pdf /URI (https://www.rbi.org.in/x)") == []


def _write(name: str, text: str) -> dict:
    path = get_settings().runtime_dir / "uploads" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return {"path": str(path), "filename": name, "display_uri": f"upload://{name}", "trigger": "upload"}


def _fake_gemini(calls: list):
    def fake(document, feedback, progress=None):
        calls.append(document.document_id)
        policy = Policy(policy_id="policy:A.P. (DIR Series) Circular No. 27", title="FERR", issuer="RBI",
                        reference="A.P. (DIR Series) Circular No. 27", effective_date="2026-10-12")
        rule = Rule(rule_id=f"rule:A.P. (DIR Series) Circular No. 27:ferr-{len(calls)}", title="Maintain FERR",
                    description=FERR_SNIPPET, effective_date="2026-10-12",
                    scope=ProductScope(product_terms=["FX derivative contracts involving INR"]),
                    checks=[ComplianceCheck(kind="control_required", description="Maintain FERR", capability="maintain_ferr")],
                    citation=SourceCitation(document_id=document.document_id, snippet=FERR_SNIPPET))
        return ExtractedPolicy(policy=policy, rules=[rule])
    return fake


@pytest.fixture
def llm(monkeypatch):
    calls: list = []
    monkeypatch.setattr(extractor, "llm_enabled", lambda: True)
    monkeypatch.setattr(extractor, "_gemini_extract", _fake_gemini(calls))
    return calls


def _extract(source: dict) -> tuple[dict, dict]:
    state = {"source": source}
    stages.stage_parse(state)
    return state, stages.stage_extract(state)


def test_extraction_cache_hit_miss_bypass_and_stable_rule_ids(llm):
    source = _write("ferr-cache.md", FERR_CIRCULAR)
    state, first = _extract(source)
    assert first["cached"] is False and len(llm) == 1
    cache_file = get_settings().runtime_dir / "extraction-cache" / f"{state['document_sha256']}.json"
    assert cache_file.exists()
    _, second = _extract(dict(source))
    assert second["cached"] is True and len(llm) == 1  # no LLM call on a hit
    assert [r["rule_id"] for r in second["rules"]] == [r["rule_id"] for r in first["rules"]]
    _, refreshed = _extract({**source, "refresh_extraction": True})
    assert refreshed["cached"] is False and len(llm) == 2
    _, after_refresh = _extract(dict(source))
    assert after_refresh["cached"] is True and after_refresh["rules"][0]["rule_id"].endswith("ferr-2")
    assert ExtractionCache().get(state["document_sha256"], "another-model") is None  # model id is part of the key
    assert first["document_classification"]["doc_type"] == "circular"
    assert any(r["relation"] == "amends" for r in first["references"])


def test_full_run_on_ferr_circular_is_inconclusive_with_no_actions(llm):
    state = run_pipeline(_write("ferr-run.md", FERR_CIRCULAR))
    report = stages.get_app_store().get_report(state["report_id"])
    assert report["summary"]["overall_status"] == "INCONCLUSIVE" and report["actions"] == []
    assert report["document_classification"]["doc_type"] == "circular"
    assert report["references"] and report["applicability"][0]["applicable"] is None


@pytest.mark.parametrize("text, words", [
    (PRESS_RELEASE, ["press release", DIRECTIONS_URL]),
    (DRAFT, ["draft or consultation", "final Directions"]),
])
def test_non_binding_documents_produce_no_obligations(llm, text, words):
    events: list = []
    with pytest.raises(ValueError) as error:
        run_pipeline(_write(f"nonbinding-{len(text)}.md", text), emit=lambda *e: events.append(e))
    message = str(error.value)
    assert message.startswith("No binding obligations were found")
    assert all(w in message for w in words)
    assert llm == []  # the LLM is never asked to extract rules from a non-binding document
    parsed = next(e for e in events if e[1] == "stage_completed")
    assert parsed[2]["output"]["document_classification"]["doc_type"] in {"press_release", "draft_or_consultation"}


def test_non_binding_without_llm_and_event_sink():
    events: list = []
    state = {"source": _write("pr-sink.md", PRESS_RELEASE)}
    with stages.event_sink(lambda *e: events.append(e)):
        stages.stage_parse(state)
        with pytest.raises(ValueError, match="press release"):
            stages.stage_extract(state)
    assert events == [("reader", "document_classified", {k: state["document_classification"][k]
                                                          for k in ("doc_type", "confidence", "reason")})]
    assert any(r["url"] == DIRECTIONS_URL for r in state["references"])


def test_guess_mime_does_not_depend_on_system_mime_tables(monkeypatch):
    import mimetypes

    from ingestion.documents import guess_mime

    monkeypatch.setattr(mimetypes, "guess_type", lambda *_a, **_k: (None, None))  # like python:3.11-slim
    assert guess_mime("Circular.DOCX") == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert guess_mime("a.pdf") == "application/pdf" and guess_mime("a.md") == "text/markdown"
    assert guess_mime("a.htm") == "text/html" and guess_mime("a.exe") == "application/octet-stream"
