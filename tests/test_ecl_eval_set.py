"""The RBI Expected Credit Loss eval set (data/circulars/rbi_ecl_*.md, see docs/evals/ecl-timeline.md).

Offline checks only: every document parses, the deterministic classifier puts consultations and press releases out
of scope for obligations, and the binding instruments are recognised as directions. LLM extraction is evaluated
manually per the timeline doc.
"""

from pathlib import Path

import pytest

from ingestion.classifier import classify_document
from ingestion.documents import parse_document

CIRCULARS = Path("data/circulars")
EXPECTED = {
    "rbi_ecl_2023_01_16_discussion_paper.md": "draft_or_consultation",
    "rbi_ecl_2023_01_16_press_release_discussion_paper.md": "press_release",
    "rbi_ecl_2023_10_04_press_release_working_group.md": "press_release",
    "rbi_ecl_2025_10_01_statement_developmental_regulatory_policies.md": "press_release",
    "rbi_ecl_2025_10_07_draft_directions_scb.md": "draft_or_consultation",
    "rbi_ecl_2025_10_07_press_release_draft_directions.md": "press_release",
    "rbi_ecl_2025_11_28_iracp_directions_v2026_01_01.md": "master_direction",
    "rbi_ecl_2025_11_28_iracp_directions_v2026_07_01.md": "master_direction",
    "rbi_ecl_2026_04_27_acpir_directions_final.md": "master_direction",
    "rbi_ecl_2026_04_27_iracp_repeal_directions.md": "master_direction",
}
BINDING = {"circular", "master_direction", "notification"}
AMENDMENTS = ["rbi_ecl_2025_12_04_iracp_amendment_directions.md", "rbi_ecl_2026_04_29_iracp_amendment_directions.md"]


def test_eval_set_is_complete():
    assert sorted(p.name for p in CIRCULARS.glob("rbi_ecl_*.md")) == sorted([*EXPECTED, *AMENDMENTS])


@pytest.mark.parametrize("name", AMENDMENTS)
def test_amendment_directions_are_binding(name):
    document = parse_document((CIRCULARS / name).read_bytes(), name)
    assert classify_document(document, use_llm=False).doc_type in BINDING


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_deterministic_classification(name):
    document = parse_document((CIRCULARS / name).read_bytes(), name)
    assert len(document.chunks) >= 1 and document.title.startswith(("RBI", "Draft"))
    result = classify_document(document, use_llm=False)
    assert result.doc_type == EXPECTED[name], (name, result)


def test_final_directions_carry_their_effective_date_and_repeal():
    text = (CIRCULARS / "rbi_ecl_2026_04_27_acpir_directions_final.md").read_text()
    assert "RBI/DOR/2026-27/398" in text and "shall come into force with effect from April 1, 2027" in text
    repeal = (CIRCULARS / "rbi_ecl_2026_04_27_iracp_repeal_directions.md").read_text()
    assert "RBI/DOR/2026-27/36" in repeal and "hereby repeals" in repeal


def test_iracp_versions_differ_by_the_july_2026_amendment():
    january = (CIRCULARS / "rbi_ecl_2025_11_28_iracp_directions_v2026_01_01.md").read_text()
    july = (CIRCULARS / "rbi_ecl_2025_11_28_iracp_directions_v2026_07_01.md").read_text()
    assert "62A." not in january and "62A." in july  # resolution-plan provisioning inserted from July 1, 2026


def test_july_2026_amendment_matches_the_consolidated_text():
    amendment = (CIRCULARS / "rbi_ecl_2026_04_29_iracp_amendment_directions.md").read_text()
    assert "Paragraph 57(4) shall be deleted" in amendment and "come into force with effect from July 1, 2026" in amendment
    july = (CIRCULARS / "rbi_ecl_2025_11_28_iracp_directions_v2026_07_01.md").read_text()
    assert "84A. A bank shall make an additional specific provision of five percent" in july
