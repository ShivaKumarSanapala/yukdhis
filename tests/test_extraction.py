"""Extraction guards: draft language is rejected, approved circulars are not, modality defaults to MUST, and
unexecutable obligations are grounded and keep a report from being COMPLIANT."""

import os
import tempfile
from pathlib import Path

os.environ.update(YUKDHIS_RUNTIME_DIR=tempfile.mkdtemp(prefix="yukdhis-test-"), YUKDHIS_BACKEND="local",
                  AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import pytest  # noqa: E402

from core.config import get_settings  # noqa: E402
from core.models import (CheckResult, ComplianceCheck, ExtractedPolicy, Policy, ProductScope, Rule,  # noqa: E402
                         SourceCitation, UnexecutableObligation)
from ingestion import extractor  # noqa: E402
from ingestion.documents import parse_document  # noqa: E402
from ingestion.extractor import extract_policy  # noqa: E402
from ingestion.validator import DRAFT_MESSAGE, validate_extraction  # noqa: E402
from knowledge_graph.store import LocalGraphStore  # noqa: E402
from pipeline import stages  # noqa: E402
from pipeline.deterministic import run_pipeline  # noqa: E402

CIRCULARS = Path("data/circulars")
RULE_TEXT = "Lenders shall give borrowers of floating rate loans the choice of a higher EMI or a longer tenor at each reset."
RESERVE_TEXT = "Banks shall hold a reserve of 2% of the notional of each FX swap with the Reserve Bank, maintained daily."
MIXED_CIRCULAR = f"""# RBI/2026-27/301 — Reset Choices and Swap Reserves

- Reference: TEST/UNEXEC/1
- Date: October 1, 2026
- Addressees: All scheduled commercial banks

## Provisions
- {RULE_TEXT}
- {RESERVE_TEXT}
"""

def _extraction(name):
    path = CIRCULARS / name
    document = parse_document(path.read_bytes(), path.name)
    return document, extract_policy(document, use_llm=False)[0]


@pytest.mark.parametrize("name", sorted(p.name for p in CIRCULARS.glob("*.md")))
def test_approved_circulars_never_trigger_draft_guard(name):
    document, extraction = _extraction(name)
    assert all(rule.modality == "MUST" for rule in extraction.rules)  # template rules keep the default
    assert not [i for i in validate_extraction(extraction, document) if i.message == DRAFT_MESSAGE]


def test_draft_snippet_is_a_rule_scoped_error():
    document, extraction = _extraction("rbi_2023_08_18_floating_rate_reset_emi_loans.md")
    rule = extraction.rules[0]
    rule.citation.snippet = "RBI had issued draft Directions and is inviting feedback from stakeholders."
    issues = [i for i in validate_extraction(extraction) if i.message == DRAFT_MESSAGE]
    assert [(i.severity, i.field, i.rule_id) for i in issues] == [("ERROR", "citation.snippet", rule.rule_id)]


def test_modality_defaults_to_must_and_parses_may():
    _, extraction = _extraction("rbi_2023_08_18_floating_rate_reset_emi_loans.md")
    data = extraction.rules[0].model_dump()
    assert Rule.model_validate({k: v for k, v in data.items() if k != "modality"}).modality == "MUST"
    assert Rule.model_validate({**data, "modality": "MAY"}).modality == "MAY"


def _mixed(document_id: str, reserve_citation: str = RESERVE_TEXT) -> ExtractedPolicy:
    policy = Policy(policy_id="policy:TEST/UNEXEC/1", title="Reset choices and swap reserves", issuer="RBI",
                    reference="TEST/UNEXEC/1", effective_date="2026-10-01")
    rule = Rule(rule_id="rule:TEST/UNEXEC/1:emi-tenor", title="Offer EMI or tenor choice at reset", description=RULE_TEXT,
                effective_date="2026-10-01", scope=ProductScope(rate_type="FLOATING", category="RETAIL_LOAN"),
                checks=[ComplianceCheck(kind="control_required", description="EMI or tenor choice offered",
                                        capability="emi_or_tenor_choice")],
                citation=SourceCitation(document_id=document_id, snippet=RULE_TEXT))
    reserve = UnexecutableObligation(citation=reserve_citation, reason="Reserve balances held with RBI are not onboarded.",
                                     missing_data=["rbi_reserve_balances", "treasury_trades"])
    return ExtractedPolicy(policy=policy, rules=[rule], unexecutable=[reserve])


def _mixed_document(name: str = "mixed.md"):
    return parse_document(MIXED_CIRCULAR.encode(), name)


def test_unexecutable_defaults_to_empty_for_old_extractions():
    _, extraction = _extraction("rbi_2023_08_18_floating_rate_reset_emi_loans.md")
    old = {k: v for k, v in extraction.model_dump().items() if k != "unexecutable"}
    assert ExtractedPolicy.model_validate(old).unexecutable == []


def test_unexecutable_citations_must_be_verbatim():
    document = _mixed_document()
    assert validate_extraction(_mixed(document.document_id), document) == []
    bad = _mixed(document.document_id, "Banks must keep an FX swap reserve with the RBI.")
    issues = [i for i in validate_extraction(bad, document) if i.severity == "ERROR"]
    assert [(i.field, i.rule_id) for i in issues] == [("unexecutable[0].citation", None)]
    only = _mixed(document.document_id).model_copy(update={"rules": []})  # nothing executable is not "no rules"
    assert not [i for i in validate_extraction(only, document) if i.message == "No rules extracted."]


def test_fake_gemini_non_verbatim_unexecutable_is_retried_then_fails(monkeypatch):
    document = _mixed_document()
    answers = iter([_mixed(document.document_id, "An invented paraphrase of the reserve obligation."),
                    _mixed(document.document_id)])
    feedback = []

    def fake(doc, fb, progress=None):
        feedback.append(fb)
        return next(answers)

    monkeypatch.setattr(extractor, "_gemini_extract", fake)
    extraction, issues, provenance = extract_policy(document, use_llm=True)
    assert provenance["attempts"] == 2 and "unexecutable[0].citation" in feedback[1]
    assert extraction.unexecutable[0].missing_data == ["rbi_reserve_balances", "treasury_trades"]

    monkeypatch.setattr(extractor, "_gemini_extract",
                        lambda doc, fb, progress=None: _mixed(document.document_id, "Paraphrased again."))
    with pytest.raises(ValueError, match="Unexecutable obligation citation is not found verbatim"):
        extract_policy(document, use_llm=True)


@pytest.mark.parametrize("statuses, unexecutable, expected", [
    (["COMPLIANT"], 0, "COMPLIANT"),
    (["COMPLIANT"], 1, "INCONCLUSIVE"),
    (["UPCOMING"], 1, "INCONCLUSIVE"),
    ([], 2, "INCONCLUSIVE"),
    (["GAP", "COMPLIANT"], 1, "NON_COMPLIANT"),
])
def test_unexecutable_obligations_never_roll_up_to_compliant(statuses, unexecutable, expected):
    results = [CheckResult(rule_id="r", check_index=i, kind="record_flag", description="d", status=s)
               for i, s in enumerate(statuses)]
    assert stages.overall_status(results, [], unexecutable) == expected


def test_report_carries_unexecutable_obligations(monkeypatch, tmp_path):
    graph = LocalGraphStore(tmp_path / "graph.db", "unexec-test")
    graph.load_architecture()
    monkeypatch.setattr(stages, "get_graph_store", lambda: graph)
    monkeypatch.setattr(extractor, "llm_enabled", lambda: True)
    monkeypatch.setattr(extractor, "_gemini_extract", lambda doc, fb, progress=None: _mixed(doc.document_id))
    path = get_settings().runtime_dir / "uploads" / "unexecutable-run.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(MIXED_CIRCULAR)
    state = run_pipeline({"path": str(path), "filename": path.name, "display_uri": f"upload://{path.name}"})
    report = stages.get_app_store().get_report(state["report_id"])
    assert [r["status"] for r in report["results"]] == ["COMPLIANT"]  # the executable rule passes ...
    assert report["summary"]["unexecutable_obligations"] == 1
    assert report["summary"]["overall_status"] == "INCONCLUSIVE"  # ... but the report cannot be COMPLIANT
    assert report["unexecutable"][0]["citation"] == RESERVE_TEXT and report["actions"] == []
