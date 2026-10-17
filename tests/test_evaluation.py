"""Gold-set evaluation of extraction (template of record as gold). No network, no LLM."""

import os
import tempfile
from pathlib import Path

os.environ.update(YUKDHIS_RUNTIME_DIR=tempfile.mkdtemp(prefix="yukdhis-test-"), YUKDHIS_BACKEND="local",
                  AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import pytest  # noqa: E402

from core.models import ComplianceCheck, Rule, SourceCitation  # noqa: E402
from ingestion import extractor  # noqa: E402
from ingestion.documents import parse_document  # noqa: E402
from ingestion.evaluation import score_extraction  # noqa: E402
from ingestion.templates import match_template  # noqa: E402
from scripts import eval_extraction  # noqa: E402

CIRCULARS = Path("data/circulars")
SAMPLES = sorted(p.name for p in CIRCULARS.glob("rbi_[0-9]*.md"))


def gold(name: str):
    document = parse_document((CIRCULARS / name).read_bytes(), name)
    return document, match_template(document)(document)


@pytest.mark.parametrize("name", SAMPLES)
def test_template_against_itself_scores_one(name):
    _, reference = gold(name)
    scores = score_extraction(reference, reference)
    assert {scores["rules"][k] for k in ("precision", "recall", "f1")} == {1.0}
    assert {scores["checks"][k] for k in ("precision", "recall", "f1")} == {1.0}
    assert scores["fields"] == {"modality": 1.0, "effective_date": 1.0, "capability": 1.0}


def test_missing_extra_and_wrong_fields_are_scored():
    _, reference = gold("rbi_2023_09_13_release_of_property_documents.md")
    candidate = reference.model_copy(deep=True)
    dropped = candidate.rules.pop()  # recall loses one rule and its check
    first, second = candidate.rules
    first.title, first.modality = "Original documents back in a month", "MAY"  # retitled: matched by citation
    second.effective_date = "2024-01-01"
    second.checks[0].capability = "late_release_payout"  # renamed capability
    candidate.rules.append(Rule(rule_id="rule:x:invented", title="Publish a release dashboard", description="Invented.",
                                effective_date=reference.policy.effective_date, scope=first.scope,
                                checks=[ComplianceCheck(kind="control_required", description="d", capability="dashboard")],
                                citation=SourceCitation(document_id="d", snippet="Nothing like this is in the circular.")))
    scores = score_extraction(candidate, reference)
    assert (scores["rules"]["precision"], scores["rules"]["recall"]) == (pytest.approx(2 / 3, abs=1e-3), pytest.approx(2 / 3, abs=1e-3))
    gold_checks = sum(len(r.checks) for r in reference.rules)
    assert scores["checks"]["gold"] == gold_checks and scores["checks"]["true_positive"] == gold_checks - 1 - len(dropped.checks)
    assert scores["fields"] == {"modality": 0.5, "effective_date": 0.5, "capability": 0.5}
    retitled = next(m for m in scores["matched_rules"] if m["candidate"] == first.rule_id)
    assert retitled["gold"] == first.rule_id and retitled["basis"] == "citation"


def test_cross_check_stores_per_field_scores_in_provenance(monkeypatch):
    document, reference = gold("rbi_2023_08_18_floating_rate_reset_emi_loans.md")
    candidate = reference.model_copy(deep=True)
    candidate.rules[0].modality = "MAY"
    monkeypatch.setattr(extractor, "_gemini_extract", lambda doc, fb, progress=None: candidate)
    extraction, issues, provenance = extractor.extract_policy(document, use_llm=True)
    assert extraction == reference  # the template stays the source of truth
    scores = provenance["extraction_scores"]
    assert scores["fields"]["modality"] == pytest.approx(2 / 3, abs=1e-3) and scores["rules"]["f1"] == 1.0
    assert provenance["gemini_agreement"] == scores["checks"]["recall"] == extractor._agreement(candidate, reference) == 1.0


def test_eval_script_without_llm_prints_perfect_scores(capsys):
    assert eval_extraction.main(["--no-llm"]) == 0
    out = capsys.readouterr().out
    assert all(name in out for name in SAMPLES) and out.count("F1 1.00") == 2 * len(SAMPLES)
