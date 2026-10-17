"""The bank-baseline eval suite (scripts/run_eval_suite.py): the deterministic subset must pass, the cases must be well formed and
consistent with the manifest of planted exceptions, and the suite must fail loudly when the bank or the engine drifts.

No network and no LLM: ``--llm`` cases are skipped here.
"""

import copy
import json
import os
import tempfile
from pathlib import Path

os.environ.update(YUKDHIS_RUNTIME_DIR=tempfile.mkdtemp(prefix="yukdhis-test-"), YUKDHIS_BACKEND="local",
                  AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import pytest  # noqa: E402

from scripts import run_eval_suite as suite  # noqa: E402

CASES = suite.load_cases()
MANIFEST = json.loads(Path("data/bank/manifest.json").read_text())
TIMELINE = {p.name for p in Path("data/circulars").glob("rbi_ecl_*.md")}


def test_deterministic_suite_passes():
    results = suite.run_suite(CASES)
    failures = [(r.case_id, r.error, [(a.name, a.expected, a.actual) for a in r.failures]) for r in results if not r.passed and not r.skipped]
    assert not failures, suite.scorecard(results)
    assert len([r for r in results if not r.skipped]) == len(CASES["cases"]) + len(CASES["control_checks"])
    assert sum(len(r.assertions) for r in results) > 150


def test_every_timeline_document_and_the_four_samples_have_a_case():
    documents = {c["document"] for c in CASES["cases"] if c.get("document")}
    assert TIMELINE <= documents
    assert {"rbi_2023_08_18_floating_rate_reset_emi_loans.md", "rbi_2023_09_13_release_of_property_documents.md"} <= documents
    presets = {c["preset"] for c in CASES["cases"] if c.get("preset")}
    assert presets == {"data/evals/presets/ferr_nddc_ap_dir_27.json", "data/evals/presets/cva_directions.json"}
    assert all(Path(p).exists() for p in presets) and all((Path("data/circulars") / d).exists() for d in documents)


def test_cases_are_well_formed_and_explain_themselves():
    ids = [c["id"] for c in CASES["cases"]] + [c["id"] for c in CASES["control_checks"]]
    assert len(ids) == len(set(ids))
    for case in CASES["cases"]:
        assert case["rationale"].strip() and len(case["rationale"]) > 30, case["id"]
        assert case["as_of"] >= "2025-01-01" and set(case["modes"]) <= {"deterministic", "llm"}
        assert "document" in case or "preset" in case
        expected = case["expected"]
        if expected.get("outcome") == "blocked":
            assert expected["blocked_reason_contains"]
        assert set(expected) <= {"doc_type", "outcome", "blocked_reason_contains", "overall_status", "unexecutable_expected", "unexecutable_count",
                                 "rule_count", "rule_suffixes_exactly", "planted_findings", "compliant_rules", "upcoming_rules",
                                 "not_evaluated_rules", "gated", "missing_data", "readiness_phase", "readiness_gaps", "readiness_ready",
                                 "readiness_gap_status", "no_actions", "actions_workflows"}, case["id"]
        for finding in expected.get("planted_findings", []):
            assert {"rule_hint", "violations"} <= set(finding)
    for item in CASES["control_checks"]:
        assert item["rationale"].strip() and item["control"].startswith("control:")


def test_case_counts_agree_with_the_manifest_of_planted_exceptions():
    planted = MANIFEST["planted"]
    july = next(c for c in CASES["cases"] if c["id"] == "ecl-07b-iracp-jul-2026")["expected"]
    by_hint = {f["rule_hint"]: f for f in july["planted_findings"]}
    assert by_hint["npa-classification-90-days"]["violations"] == planted["dpd_over_90_still_standard"]["count"]
    assert by_hint["npa-classification-90-days"]["branches"] == {"Boring Road, Patna": 6, "Adajan, Surat": 5, "C-Scheme, Jaipur": 3}
    assert by_hint["restructured-again-income-cash-basis-139b"]["violations"] == planted["repeat_restructured_on_accrual"]["count"]
    failing = planted["npa_provision_shortfall"]["failing_checks"]
    for rule, violations in failing.items():
        assert by_hint[rule]["violations"] == violations, rule
    sma = [f for f in july["planted_findings"] if f["rule_hint"] == "sma-reporting-bands"]
    assert sum(f["violations"] for f in sma) == planted["sma_band_mistags"]["count"]
    release = next(c for c in CASES["cases"] if c["id"] == "sample-document-release")["expected"]["planted_findings"][0]
    assert release["violations"] == planted["rbi_2023_24_60_document_release_violations"]
    assert release["branches_include"]["Koramangala, Bengaluru"] == planted["of_which_koramangala"]
    reset = planted["rbi_2023_24_55_rate_reset"]
    floating = {f["rule_hint"]: f for f in next(c for c in CASES["cases"] if c["id"] == "sample-floating-rate-reset")["expected"]["planted_findings"]}
    assert floating["notify-rate-reset"]["violations"] == reset["borrowers_not_notified_of_reset"]
    assert floating["fixed-rate-choice"]["violations"] == reset["auto_floating_loans_without_fixed_rate_option"]
    assert floating["reset-statement"]["violations"] == reset["loans_without_reset_statement"]
    kyc = next(i for i in CASES["control_checks"] if i["control"] == "control:kyc-periodic-updation")
    assert kyc["expected_violations"] == planted["kyc_periodic_updation_overdue"]["count"]


def test_amendment_as_of_variations():
    by_id = {c["id"]: c for c in CASES["cases"]}
    early, late = by_id["ecl-07y-iracp-amendment-apr-2026-asof-2026-06-01"], by_id["ecl-07y-iracp-amendment-apr-2026"]
    assert early["as_of"] == "2026-06-01" and early["expected"]["overall_status"] == "UPCOMING"
    assert late["as_of"] == "2026-10-17" and late["expected"]["overall_status"] == "NON_COMPLIANT"
    assert sorted(late["expected"]["rule_suffixes_exactly"]) == sorted(early["expected"]["upcoming_rules"])  # only the amended obligations


def test_the_suite_fails_loudly_when_an_expectation_is_wrong():
    broken = copy.deepcopy(CASES)
    for case in broken["cases"]:
        if case["id"] == "sample-document-release":
            case["expected"]["planted_findings"][0]["violations"] = 11
            case["expected"]["overall_status"] = "COMPLIANT"
    broken["control_checks"][0]["expected_violations"] = 0
    results = suite.run_suite(broken, only="sample-document-release")
    assert not results[0].passed and {a.name for a in results[0].failures} >= {"overall_status", "finding release-within-30-days[check 0]: violations"}
    card = suite.scorecard(results)
    assert "FAIL  sample-document-release" in card and "expected: 11" in card and "actual:   12" in card
    kyc_broken = suite.run_suite(broken, only="control-check-day-end-npa-classification")
    assert not kyc_broken[0].passed and "expected: 0" in suite.scorecard(kyc_broken)


def test_cli_exit_codes(tmp_path, capsys):
    assert suite.main(["--list"]) == 0 and "ecl-07b-iracp-jul-2026" in capsys.readouterr().out
    assert suite.main(["--only", "sample-document-release"]) == 0
    broken = copy.deepcopy(CASES)
    broken["cases"][0]["expected"]["doc_type"] = "circular"
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(broken))
    assert suite.main(["--cases", str(path), "--only", "ecl-01"]) == 1
    assert "FAIL  ecl-01-discussion-paper" in capsys.readouterr().out


def test_llm_only_expectations_are_skipped_without_the_flag():
    acpir = next(c for c in CASES["cases"] if c["id"] == "ecl-08-acpir-final-asof-2026-10-17")
    assert acpir["llm_expected"]["overall_status"] == "UPCOMING" and acpir["llm_expected"]["no_actions"] is True
    only_llm = {"cases": [{**acpir, "modes": ["llm"]}], "control_checks": []}
    assert suite.run_suite(only_llm)[0].skipped == "agentic only"
