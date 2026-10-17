"""Extraction guards: draft language is rejected, approved circulars are not, modality defaults to MUST."""

import os
import tempfile
from pathlib import Path

os.environ.update(YUKDHIS_RUNTIME_DIR=tempfile.mkdtemp(prefix="yukdhis-test-"), YUKDHIS_BACKEND="local",
                  AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import pytest  # noqa: E402

from core.models import Rule  # noqa: E402
from ingestion.documents import parse_document  # noqa: E402
from ingestion.extractor import extract_policy  # noqa: E402
from ingestion.validator import DRAFT_MESSAGE, validate_extraction  # noqa: E402

CIRCULARS = Path("data/circulars")


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
