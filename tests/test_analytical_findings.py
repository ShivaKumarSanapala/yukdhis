"""Circulars without executable rules still yield analytical insights: objectives -> guarded SQL findings -> report."""

import os
import tempfile

os.environ.update(YUKDHIS_RUNTIME_DIR=tempfile.mkdtemp(prefix="yukdhis-test-"), YUKDHIS_BACKEND="local",
                  AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import pytest  # noqa: E402

from core.models import CheckResult, ExtractedPolicy, Policy  # noqa: E402
from ingestion.validator import validate_extraction  # noqa: E402
from pipeline import stages  # noqa: E402

AS_OF = "2026-10-17"
LTV_BAND = ("CASE WHEN CAST(outstanding_balance AS DOUBLE PRECISION) / NULLIF(security_value, 0) > 0.9 THEN 'above 90%' "
            "ELSE 'up to 90%' END")
LTV_SQL = (f"SELECT {LTV_BAND} AS ltv_band, COUNT(loan_ref) AS loans, SUM(outstanding_balance) AS exposure "
           f"FROM vw_asset_quality WHERE secured = 1 GROUP BY {LTV_BAND}")


def policy():
    return Policy(policy_id="policy:t", title="Gold loan LTV", issuer="RBI", reference="T/1", effective_date="2026-04-01")


def zero_rule_state(objectives=("Analyse the secured loan portfolio for loan-to-value above 90%",)):
    extraction = ExtractedPolicy(policy=policy(), rules=[], analytical_objectives=list(objectives))
    state = {"extraction": extraction.model_dump(), "as_of": AS_OF, "analytical_objectives": list(objectives)}
    stages.stage_map(state)
    stages.stage_blast_radius(state)
    stages.stage_evaluate(state)
    return state


def test_extraction_with_only_objectives_is_valid_and_empty_extraction_is_not():
    with_objectives = ExtractedPolicy(policy=policy(), rules=[], analytical_objectives=["Analyse LTV"])
    assert not [i for i in validate_extraction(with_objectives) if i.severity == "ERROR"]
    empty = ExtractedPolicy(policy=policy(), rules=[])
    assert any(i.message == "No rules extracted." for i in validate_extraction(empty))


def test_overall_status_is_insights_only_without_checks_and_with_findings():
    gap = CheckResult(rule_id="r", check_index=0, kind="control_required", description="d", status="GAP")
    assert stages.overall_status([], [], 0, 2) == "ANALYTICAL_INSIGHTS"
    assert stages.overall_status([], [], 0, 0) == "INCONCLUSIVE"
    assert stages.overall_status([gap], [], 0, 2) == "NON_COMPLIANT"


def test_finding_stores_real_rows_and_the_report_shows_insights_with_no_rules():
    state = zero_rule_state()
    assert state["results"] == [] and state["blast_radius"] == []
    recorded = stages.stage_record_finding(state, state["analytical_objectives"][0], "Some loans exceed 90% LTV.", LTV_SQL)
    assert recorded["row_count"] >= 1 and recorded["columns"] == ["ltv_band", "loans", "exposure"]
    stages.stage_finalize(state, narrative="## Analytical insights")
    report = stages.get_app_store().get_report(state["report_id"])
    assert report["summary"]["overall_status"] == "ANALYTICAL_INSIGHTS" and report["summary"]["analytical_findings"] == 1
    finding = report["analytical_findings"][0]
    assert finding["rows"] and finding["sql"].lstrip().upper().startswith("SELECT") and finding["objective"].startswith("Analyse")
    assert report["analytical_objectives"] == state["analytical_objectives"]


def test_finding_rejects_unsafe_sql_and_caps_the_number_of_findings():
    state = zero_rule_state()
    with pytest.raises(ValueError):
        stages.stage_record_finding(state, "o", "s", "SELECT customer_name FROM vw_asset_quality")
    with pytest.raises(ValueError):
        stages.stage_record_finding(state, "o", "s", "DELETE FROM loans")
    for _ in range(stages.MAX_FINDINGS):
        stages.stage_record_finding(state, "o", "s", "SELECT COUNT(loan_ref) AS n FROM vw_asset_quality")
    with pytest.raises(ValueError):
        stages.stage_record_finding(state, "o", "s", "SELECT COUNT(loan_ref) AS n FROM vw_asset_quality")


def test_circular_context_is_cleaned_and_capped():
    from core.models import DocumentChunk, ParsedDocument

    chunks = [DocumentChunk(chunk_id="c1", content="LTV   limit is 90%.\n\n\n\nMail rahul@example.com", headings=["Gold loans"])]
    text = stages.circular_context(ParsedDocument(document_id="d", source_uri="u", mime_type="text/markdown", title="t", parser="p", chunks=chunks))
    assert "Gold loans" in text and "LTV limit is 90%." in text and "rahul@example.com" not in text and "\n\n\n" not in text


def test_agents_expose_the_new_tools_and_state_driven_prompts():
    from types import SimpleNamespace

    from agents.compliance_coordinator.sub_agents import data_analyst_agent, evaluator_agent, orchestrator_agent

    names = {getattr(t, "__name__", "") for t in data_analyst_agent.tools}
    assert {"record_analytical_finding", "run_safe_sql", "get_circular_text", "search_circulars"} <= names
    assert {"run_safe_sql", "describe_bank_data"} <= {getattr(t, "__name__", "") for t in evaluator_agent.tools}
    state = {"analytical_objectives": ["Analyse LTV above 90%"], "full_text_context": "LTV must not exceed 90%.", "extraction": {"rules": []},
             "analytical_findings": [{"objective": "LTV", "summary": "15 loans above 90%", "row_count": 2}]}
    prompt = data_analyst_agent.instruction(SimpleNamespace(state=state))
    assert "Analyse LTV above 90%" in prompt and "LTV must not exceed 90%." in prompt and "EVEN WHEN no rule" in prompt
    assert "Rules mapped: 0" in orchestrator_agent.instruction(SimpleNamespace(state=state))
    assert "15 loans above 90%" in orchestrator_agent.instruction(SimpleNamespace(state=state))
