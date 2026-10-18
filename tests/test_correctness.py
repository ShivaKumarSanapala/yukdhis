"""Fail-closed scope, UPCOMING/MAY handling and active-book defaults, driven through pipeline.stages (no LLM)."""

import os
import tempfile

os.environ.update(YUKDHIS_RUNTIME_DIR=tempfile.mkdtemp(prefix="yukdhis-test-"), YUKDHIS_BACKEND="local",
                  AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

from sqlalchemy import bindparam, text  # noqa: E402

from bank_mcp_server.operational_store import analyst_engine  # noqa: E402
from core.models import ComplianceCheck, ExtractedPolicy, Policy, ProductScope, Rule, SourceCitation  # noqa: E402
from pipeline import stages  # noqa: E402

AS_OF = "2026-10-17"


def run(rule_overrides: dict, scope: dict | None = None, check: ComplianceCheck | None = None) -> dict:
    rule_overrides = {"effective_date": "2023-09-01", **rule_overrides}
    check = check or ComplianceCheck(kind="control_required", description="Fixed rate option", capability="fixed_rate_option")
    rule = Rule(rule_id="rule:test:r1", title="Test rule", description="Test rule",
                scope=ProductScope(**(scope or {})), checks=[check],
                citation=SourceCitation(document_id="doc:test", snippet="test"), **rule_overrides)
    policy = Policy(policy_id="policy:test", title="Test", issuer="RBI", reference="TEST/1", effective_date=rule.effective_date)
    state: dict = {"extraction": ExtractedPolicy(policy=policy, rules=[rule]).model_dump(), "as_of": AS_OF}
    stages.stage_map(state)
    stages.stage_blast_radius(state)
    stages.stage_evaluate(state)
    stages.stage_finalize(state)
    state["summary"] = stages.get_app_store().get_report(state["report_id"])["summary"]
    return state


def test_unresolved_product_term_fails_closed():
    state = run({"segments": ["commercial banks"]}, scope={"product_terms": ["FX derivative contracts"]})
    radius, result = state["blast_radius"][0], state["results"][0]
    assert radius["products"] == [] and radius["scope_status"] == "UNRESOLVED" and "FX derivative" in radius["scope_notes"][0]
    assert state["mappings"][0]["segment_ids"] == []
    assert result["status"] == "NOT_EVALUATED" and result["evidence"].startswith("Scope could not be resolved")
    assert state["summary"]["overall_status"] == "INCONCLUSIVE" and state["summary"]["unresolved_rules"] == ["rule:test:r1"]
    assert stages._owner_for(state, "rule:test:r1") == "Compliance"


def test_future_dated_rule_is_upcoming_not_compliant():
    state = run({"effective_date": "2027-04-01"}, scope={"rate_type": "FLOATING"})
    assert {r["status"] for r in state["results"]} == {"UPCOMING"}
    assert "readiness not assessed" in state["results"][0]["evidence"]
    assert state["summary"]["overall_status"] == "UPCOMING"


def test_permissive_modality_is_not_evaluated():
    state = run({"modality": "MAY"}, scope={"rate_type": "FLOATING"})
    assert state["results"][0]["status"] == "NOT_EVALUATED" and "not an obligation" in state["results"][0]["evidence"]
    assert state["summary"]["overall_status"] == "INCONCLUSIVE"


def test_closed_loans_are_not_counted_against_a_control_gap():
    state = run({}, scope={"product_terms": ["four-wheeler advances"]})
    gap = state["results"][0]
    assert gap["status"] == "GAP" and gap["violations"] > 0
    with analyst_engine().connect() as conn:
        query = text("SELECT status, COUNT(*) FROM vw_compliance_impact WHERE product_id IN :p GROUP BY status")
        counts = dict(conn.execute(query.bindparams(bindparam("p", expanding=True)), {"p": gap["products_missing_control"]}).fetchall())
    assert counts.get("CLOSED", 0) > 0, "fixture must contain closed loans for this test to mean anything"
    assert gap["violations"] == counts["ACTIVE"]


def test_proposed_control_pending_review_is_not_a_gap():
    state = run({}, check=ComplianceCheck(kind="control_required", description="Brand new capability", capability="brand_new_capability"))
    store = stages.get_graph_store()
    required = state["blast_radius"][0]["controls_required"]
    assert required
    for control_id in required:  # what the mapper does for a control it has just proposed
        node = store.node(control_id)
        store.upsert_node(control_id, node["type"], node["name"], {**node["attributes"], "needs_review": True})
    stages.stage_blast_radius(state)
    stages.stage_evaluate(state)
    stages.stage_finalize(state)
    result = state["results"][0]
    assert result["status"] == "NOT_EVALUATED" and "awaiting review" in result["evidence"]
    summary = stages.get_app_store().get_report(state["report_id"])["summary"]
    assert set(required) <= set(summary["controls_pending_review"]) and not summary["control_gaps"]
    assert summary["overall_status"] == "INCONCLUSIVE" and summary["gaps"] == 0
