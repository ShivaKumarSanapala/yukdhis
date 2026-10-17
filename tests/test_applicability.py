"""Applicability gate (L5): rules the bank cannot be held to, or cannot evaluate, are NOT_EVALUATED with no actions."""

import os
import tempfile

os.environ.update(YUKDHIS_RUNTIME_DIR=tempfile.mkdtemp(prefix="yukdhis-test-"), YUKDHIS_BACKEND="local",
                  AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import pytest  # noqa: E402

from core.app_store import get_app_store  # noqa: E402
from core.models import ComplianceCheck, ExtractedPolicy, Policy, ProductScope, Rule, SourceCitation  # noqa: E402
from pipeline import applicability, stages  # noqa: E402
from pipeline.deterministic import run_pipeline  # noqa: E402

AS_OF = "2026-10-17"
NDDC = ("AD Category-I banks with an operating IFSC Banking Unit may offer INR non-deliverable derivative contracts "
        "(NDDCs) to non-residents only for hedging.")
FERR = ("AD Category-I banks shall maintain a Foreign Exchange Risk Reserve of 20% of the INR-equivalent notional of each "
        "FX derivative contract undertaken to hedge their own assets and liabilities, held as cash with RBI.")
CVA = "Banks shall compute the CVA risk capital charge under BA-CVA and report the resulting RWA and CET1 impact."


def rule(rule_id: str, text: str, scope: dict | None = None, capability: str = "fixed_rate_option",
         effective_date: str = "2026-10-12", **extra) -> Rule:
    return Rule(rule_id=rule_id, title=text[:60], description=text, effective_date=effective_date,
                scope=ProductScope(**(scope or {})), checks=[ComplianceCheck(kind="control_required", description=text[:40],
                                                                             capability=capability)],
                citation=SourceCitation(document_id="doc:test", snippet=text), **extra)


def run(rules: list[Rule]) -> dict:
    policy = Policy(policy_id="policy:gate", title="Gate test", issuer="RBI", reference="GATE/1", effective_date="2026-10-12")
    state: dict = {"extraction": ExtractedPolicy(policy=policy, rules=rules).model_dump(), "as_of": AS_OF}
    events: list[tuple] = []
    with stages.event_sink(lambda *event: events.append(event)):
        state["map_output"] = stages.stage_map(state)
    stages.stage_blast_radius(state)
    stages.stage_profile(state)
    stages.stage_evaluate(state)
    stages.stage_finalize(state)
    state["report"] = get_app_store().get_report(state["report_id"])
    state["events"] = events
    return state


def test_nddc_and_ferr_rules_are_gated_inconclusive_with_no_actions():
    # Product wording that *does* resolve to the loan book: without the gate this would be a 161-loan GAP (eval R1/R2).
    nddc = rule("rule:gate:nddc", NDDC, scope={"product_terms": ["four-wheeler advances"]}, capability="nddc_hedging_purpose_only")
    ferr = rule("rule:gate:ferr", FERR, scope={"product_terms": ["four-wheeler advances"]}, capability="maintain_ferr")
    state = run([nddc, ferr])
    gate = {a["rule_id"]: a for a in state["report"]["applicability"]}
    assert gate["rule:gate:nddc"]["applicable"] is False
    assert any("IFSC Banking Unit" in r for r in gate["rule:gate:nddc"]["reasons"])
    assert gate["rule:gate:ferr"]["applicable"] is None
    assert any(m.startswith("treasury_trades") for m in gate["rule:gate:ferr"]["missing_data"])
    assert any(m.startswith("rbi_reserve_balances") for m in gate["rule:gate:ferr"]["missing_data"])
    results = state["report"]["results"]
    assert {r["status"] for r in results} == {"NOT_EVALUATED"}
    assert all(r["worklist_id"] is None and r["violations"] == 0 for r in results)
    assert next(r for r in results if r["rule_id"] == "rule:gate:nddc")["evidence"].startswith("Not applicable to this bank")
    assert next(r for r in results if r["rule_id"] == "rule:gate:ferr")["missing_data"]
    assert all(r["products"] == [] for r in state["report"]["blast_radius"])
    assert state["data_profile"] == {"rows": []}  # no retail portfolio profiled for an FX rule
    summary = state["report"]["summary"]
    assert summary["overall_status"] == "INCONCLUSIVE" and summary["gaps"] == 0
    assert summary["not_applicable_rules"] == ["rule:gate:nddc"] and "rule:gate:ferr" in summary["rules_missing_data"]
    assert state["report"]["actions"] == []
    kinds = [(stage, kind) for stage, kind, _ in state["events"]]
    assert kinds == [("mapper", "applicability"), ("mapper", "applicability")]
    payload = state["events"][0][2]
    assert set(payload) == {"rule_id", "applicable", "reasons", "missing_data"}


def test_gated_rule_cannot_get_an_action_from_the_orchestrator():
    state = run([rule("rule:gate:ferr2", FERR, scope={"product_terms": ["four-wheeler advances"]}, capability="maintain_ferr")])
    with pytest.raises(ValueError, match="No action can be proposed"):
        stages.stage_propose(state, "PRODUCT_CONTROL_CHANGE", "rule:gate:ferr2", None, "Add FERR control")


def test_cva_capital_rule_reports_missing_capital_data():
    assessment = applicability.assess_rule(rule("rule:gate:cva", CVA, effective_date="2027-04-01"))
    assert assessment.applicable is None
    assert any(m.startswith("capital_rwa") for m in assessment.missing_data)
    state = run([rule("rule:gate:cva", CVA, effective_date="2027-04-01")])
    result = state["report"]["results"][0]
    assert result["status"] == "NOT_EVALUATED" and any(m.startswith("capital_rwa") for m in result["missing_data"])
    assert "2027-04-01" in result["evidence"]
    assert state["report"]["summary"]["overall_status"] == "INCONCLUSIVE" and state["report"]["actions"] == []


def test_loan_rules_and_entity_scope():
    loan = rule("rule:gate:loan", "Lenders must offer borrowers the option to switch to a fixed rate.", scope={"rate_type": "FLOATING"})
    assert applicability.assess_rule(loan).applicable is True
    nbfc = rule("rule:gate:nbfc", "NBFCs shall offer borrowers the option to switch to a fixed rate.", scope={"rate_type": "FLOATING"})
    gated = applicability.assess_rule(nbfc)
    assert gated.applicable is False and "NBFCs" in gated.reasons[0]
    both = rule("rule:gate:both", "Banks and NBFCs shall offer borrowers a fixed-rate option.", scope={"rate_type": "FLOATING"})
    assert applicability.assess_rule(both).applicable is True
    cat2 = rule("rule:gate:ad2", "AD Category-II entities shall report money-changing transactions.", capability="report")
    assert applicability.assess_rule(cat2).applicable is False
    deposits = rule("rule:gate:dep", "Banks shall pay interest on savings bank accounts quarterly.", capability="interest_credit")
    dep = applicability.assess_rule(deposits)
    assert dep.applicable is None and any(m.startswith("deposits") for m in dep.missing_data)


def test_floating_rate_sample_is_still_evaluated_exactly_as_before():
    name = "rbi_2023_08_18_floating_rate_reset_emi_loans.md"
    state = run_pipeline({"path": "data/circulars/" + name, "filename": name})
    report = get_app_store().get_report(state["report_id"])
    assert all(a["applicable"] is True for a in report["applicability"])
    assert "NOT_EVALUATED" not in {r["status"] for r in report["results"]}
    assert report["summary"]["overall_status"] == "NON_COMPLIANT"
    assert report["summary"]["not_applicable_rules"] == [] and report["summary"]["rules_missing_data"] == {}
    fixed = next(r for r in report["blast_radius"] if r["rule_id"].endswith(":fixed-rate-choice"))
    assert fixed["control_gaps"] == {"product:auto-loan-floating": ["control:fixed-rate-option"]}
    assert report["actions"]
