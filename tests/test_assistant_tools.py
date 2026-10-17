"""Assistant read tools: tenant scoping, size caps, audit, PII refusal, guards, masking of opaque ids. No network, no LLM."""

import json
import os
import tempfile
from types import SimpleNamespace

os.environ.update(YUKDHIS_RUNTIME_DIR=tempfile.mkdtemp(prefix="yukdhis-test-"), YUKDHIS_BACKEND="local",
                  AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import pytest  # noqa: E402

from agents.compliance_assistant import tools as T  # noqa: E402
from agents.compliance_assistant.agent import ASSISTANT_TOOLS, root_agent  # noqa: E402
from agents.compliance_coordinator import tools as coordinator  # noqa: E402
from agents.compliance_coordinator.sub_agents.data_analyst import data_analyst_agent  # noqa: E402
from bank_mcp_server.sql_guard import SqlRejected, validate_sql  # noqa: E402
from core.app_store import AppStore, set_app_store  # noqa: E402
from privacy import callbacks  # noqa: E402
from privacy.detectors import Finding  # noqa: E402
from privacy.sanitizer import Sanitizer  # noqa: E402

CTX = SimpleNamespace(state={}, agent_name="compliance_assistant")
REPORT_ID = "rpt-bbbbbbbbbbbb"


def report_body(report_id=REPORT_ID, gaps=1, rule_title="Notify borrowers"):
    return {
        "report_id": report_id, "policy": {"policy_id": "pol-1", "title": "Reset policy"}, "as_of": "2026-10-17", "mode": "deterministic",
        "generated_at": "2026-10-17T10:00:00+00:00", "narrative": "n", "summary": {"rules": 1, "gaps": gaps, "overall_status": "GAP"},
        "rules": [{"rule_id": "rule:r1", "title": rule_title}],
        "results": [{"rule_id": "rule:r1", "check_index": 0, "kind": "record_flag", "description": "notified", "status": "GAP",
                     "in_scope": 10, "violations": gaps, "exposure_inr": 5.0, "penalty_inr": 0.0, "by_branch": {"Pune": gaps},
                     "by_product": {}, "worklist_id": "wl-1", "evidence": "e", "missing_data": ["x"]}],
        "blast_radius": [{"rule_id": "rule:r1", "scope_status": "RESOLVED", "scope_notes": ["note"], "products": [{"id": "p", "name": "Home"}],
                          "control_gaps": {}}],
        "unexecutable": [{"citation": "c", "reason": "r", "missing_data": []}], "control_decisions": [{"control_id": "control:x"}],
        "document_classification": {"doc_type": "circular"}, "applicability": [{"rule_id": "rule:r1", "applicable": True}],
        "limitations": ["Synthetic data"], "validation_issues": [],
        "actions": [{"action_id": "act-1", "workflow_id": "BRANCH_MANAGER_ALERT", "rule_id": "rule:r1", "worklist_id": "wl-1",
                     "target_count": 3, "status": "PENDING_APPROVAL", "notice_template": "Dear {{CUSTOMER_NAME}}", "owner": "team:x"}],
    }


@pytest.fixture()
def store(tmp_path):
    app_store = AppStore(url=f"sqlite:///{tmp_path / 'app.db'}")
    set_app_store(app_store)
    yield app_store
    set_app_store(None)


@pytest.fixture()
def seeded(store):
    run_id = store.create_run("deterministic", {"filename": "c.pdf", "user_id": "u1"},
                              {"owner_id": "u1", "created_by": "alice@bank.test", "source_kind": "upload", "filename": "c.pdf"})
    store.add_event(run_id, "reader", "stage_started", {"label": "Read"})
    store.add_event(run_id, "reader", "thinking", {"text": "secret reasoning"})
    store.add_event(run_id, "reader", "tool_call", {"tool": "x", "args": {"blob": "z" * 5000}})
    store.add_event(run_id, "reader", "stage_completed", {"label": "Read"})
    store.save_report(report_body())
    store.update_run(run_id, report_id=REPORT_ID, status="COMPLETED")
    return run_id


def audited(store, tool):
    return [e for e in store.audit_entries(100) if e["tool"] == tool and e["actor"] == "compliance_assistant"]


# tool list ---------------------------------------------------------------------------------------------------------
def test_assistant_tool_list_has_the_new_tools():
    names = {getattr(t, "__name__", None) or getattr(t, "name", "") for t in root_agent.tools}
    expected = {"list_runs", "get_run", "get_run_events", "compare_runs", "list_impact_reports", "get_impact_report",
                "get_report_checks", "list_report_actions", "query_graph", "get_node", "neighbours", "time_travel_blast_radius",
                "get_graph_ontology", "get_ontology", "describe_bank_data", "run_safe_sql", "list_policies", "get_policy",
                "list_controls", "get_control", "readiness_gaps", "load_memory", "preload_memory"}
    assert expected <= names
    assert len(ASSISTANT_TOOLS) == len({t.__name__ for t in ASSISTANT_TOOLS})


def test_data_analyst_has_the_catalogue_and_wide_sql():
    names = {t.__name__ for t in data_analyst_agent.tools}
    assert {"describe_bank_data", "run_safe_sql", "profile_impacted_portfolio"} <= names
    instruction = data_analyst_agent.instruction(None)
    assert "vw_asset_quality" in instruction and "vw_kyc_status" in instruction


def test_instruction_names_tools_and_keeps_the_pii_line():
    text = root_agent.instruction(None)
    for name in ("get_run_events", "compare_runs", "query_graph", "describe_bank_data", "readiness_gaps", "get_report_checks"):
        assert name in text
    assert "PAN" in text and "not available to the assistant" in text


# runs --------------------------------------------------------------------------------------------------------------
def test_runs_tools_and_audit(store, seeded):
    listed = T.list_runs()
    assert listed["status"] == "success" and listed["runs"][0]["run_id"] == seeded
    assert "alice@bank.test" not in json.dumps(listed)
    assert T.list_runs(status="FAILED")["runs"] == []
    run = T.get_run(seeded)
    assert run["run"]["report_id"] == REPORT_ID and run["stages"][0]["stage"] == "reader" and run["stages"][0]["state"] == "completed"
    assert audited(store, "list_runs") and audited(store, "get_run")


def test_run_events_exclude_thinking_and_truncate_tool_payloads(store, seeded):
    events = T.get_run_events(seeded)["events"]
    assert "thinking" not in {e["kind"] for e in events}
    call = next(e for e in events if e["kind"] == "tool_call")
    assert call["payload"]["truncated"] is True and len(json.dumps(call["payload"])) < 2000
    assert [e["kind"] for e in T.get_run_events(seeded, kinds="thinking")["events"]] == ["thinking"]
    assert len(T.get_run_events(seeded, limit=1)["events"]) == 1
    assert audited(store, "get_run_events")


def test_run_tools_are_tenant_scoped(store, seeded):
    other = AppStore(url=str(store.engine.url))
    other.tenant_id = "other-bank"
    set_app_store(other)
    assert T.get_run(seeded)["status"] == "error"
    assert T.get_run_events(seeded)["status"] == "error"
    assert T.list_runs()["runs"] == []
    assert T.get_impact_report(REPORT_ID)["status"] == "error"
    assert T.list_report_actions(REPORT_ID)["status"] == "error"
    assert T.get_report_checks(REPORT_ID)["status"] == "error"
    assert audited(other, "get_run")  # failures are audited too


def test_events_are_size_capped(store, seeded):
    for i in range(400):
        store.add_event(seeded, "mapper", "agent_message", {"text": f"{i} " + "y" * 900})
    out = T.get_run_events(seeded, limit=200)
    assert out["status"] == "success" and out["truncated"] is True
    assert len(json.dumps(out)) <= T.MAX_RESULT_CHARS + 500


def test_compare_runs(store, seeded):
    second = store.create_run("deterministic", {"filename": "d.pdf"}, {"owner_id": "u1"})
    later = report_body("rpt-cccccccccccc", gaps=4, rule_title="Notify borrowers promptly")
    later["actions"][0]["action_id"] = "act-2"
    store.save_report(later)
    store.update_run(second, report_id="rpt-cccccccccccc", status="COMPLETED")
    out = T.compare_runs(seeded, second)
    assert out["status"] == "success" and out["rules_added_in_b"] == ["Notify borrowers promptly"]
    assert out["rules_removed_from_a"] == ["Notify borrowers"]
    unfinished = store.create_run("deterministic", {}, {})
    assert "no report" in T.compare_runs(seeded, unfinished)["message"]
    assert audited(store, "compare_runs")


# reports -----------------------------------------------------------------------------------------------------------
def test_report_tools(store, seeded):
    report = T.get_impact_report(REPORT_ID)
    for key in ("unexecutable", "control_decisions", "document_classification", "applicability", "scope", "limitations"):
        assert report[key], key
    assert report["scope"][0]["scope_notes"] == ["note"]
    checks = T.get_report_checks(REPORT_ID, rule="notify")
    assert checks["total_matching"] == 1 and checks["checks"][0]["evidence"] == "e" and checks["checks"][0]["missing_data"] == ["x"]
    assert T.get_report_checks(REPORT_ID, rule="nothing-like-this")["checks"] == []
    actions = T.list_report_actions(REPORT_ID)
    assert actions["count"] == 1 and "decided_by" not in json.dumps(actions)
    assert T.get_impact_report("rpt-missing")["status"] == "error"
    assert T.list_impact_reports()["reports"][0]["report_id"] == REPORT_ID
    for tool in ("get_impact_report", "get_report_checks", "list_report_actions", "list_impact_reports"):
        assert audited(store, tool), tool


def test_pii_keys_are_stripped_from_results():
    assert T._strip({"a": {"email": "x@y.com", "full_name": "N", "name": "Branch A", "loan_ref": "LR-1"}}) == {
        "a": {"name": "Branch A", "loan_ref": "LR-1"}}


# graph -------------------------------------------------------------------------------------------------------------
class FakeGraph:
    tenant_id = "demo-bank"

    def __init__(self, rows=None):
        self.rows, self.seen = rows or [], []

    def run_gql(self, gql, params):
        self.seen.append(gql)
        return self.rows


def test_query_graph_guard_and_caps(store, monkeypatch):
    fake = FakeGraph([{"n": i} for i in range(500)])
    monkeypatch.setattr(T, "get_graph_store", lambda: fake)
    ok = T.query_graph("GRAPH BankArchitecture MATCH (n:Rule) WHERE n.tenant_id = @tenant_id RETURN n.id LIMIT 900")
    assert ok["status"] == "success" and ok["row_count"] == 200 and "LIMIT 200" in fake.seen[0]
    for bad in ("GRAPH BankArchitecture MATCH (n) WHERE n.tenant_id = @tenant_id DELETE n RETURN n",
                "GRAPH BankArchitecture MATCH (n) WHERE n.tenant_id = @tenant_id SET n.x = 1 RETURN n",
                "GRAPH BankArchitecture MATCH (n:Rule) RETURN n.id",
                "MATCH (n) RETURN n"):
        assert T.query_graph(bad)["status"] == "error"
    pii = T.query_graph("GRAPH BankArchitecture MATCH (n:Customer) WHERE n.tenant_id = @tenant_id RETURN n.email")
    assert pii["status"] == "error" and "personal" in pii["message"]
    assert len(fake.seen) == 1
    assert len(audited(store, "query_graph")) == 6


def test_node_and_neighbours_on_the_real_local_graph(store):
    node = T.get_node("product:home-loan-floating")
    assert node["status"] == "success" and node["node"]["type"] == "Product"
    assert T.get_node("product:nope")["status"] == "error"
    near = T.neighbours("product:home-loan-floating", direction="both", as_of="2026-10-17")
    assert near["status"] == "success" and near["count"] > 0
    assert all(set(e["node"]) == {"id", "type", "name"} for e in near["edges"])
    assert T.neighbours("product:home-loan-floating", as_of="not-a-date")["status"] == "error"
    assert T.neighbours("product:home-loan-floating", direction="sideways")["status"] == "error"
    assert audited(store, "get_node") and audited(store, "neighbours")


# bank data ---------------------------------------------------------------------------------------------------------
def test_describe_bank_data_catalogue(store, monkeypatch):
    out = coordinator.describe_bank_data(tool_context=CTX)
    views = {v["view"]: v for v in out["views"]}
    assert {"vw_asset_quality", "vw_kyc_status", "vw_compliance_impact", "vw_loan_events", "vw_branch_directory"} <= set(views)
    assert views["vw_asset_quality"]["onboarded"] and views["vw_asset_quality"]["row_count"] > 0
    columns = {c["name"] for v in views.values() for c in v["columns"]}
    assert not columns & {"full_name", "email", "phone", "pan", "customer_id", "manager_name"}
    assert out["planted_exception_counts"] and all("count" in v for v in out["planted_exception_counts"].values())
    assert "loan_ref" in out["opaque_keys"]
    import bank_mcp_server.secure_engine as engine
    original, original_columns = engine.sanitized_views, engine.view_columns
    ghost_spec = {"description": "pack view without data", "columns": {"x": "int: x"}}
    monkeypatch.setattr(engine, "sanitized_views", lambda: {**original(), "vw_ghost": ghost_spec})
    monkeypatch.setattr(engine, "view_columns", lambda name: ({"x": engine.parse_column("x", "int: x")} if name == "vw_ghost"
                                                              else original_columns(name)))
    ghost = {v["view"]: v for v in coordinator.describe_bank_data(tool_context=CTX)["views"]}["vw_ghost"]
    assert ghost["onboarded"] is False and ghost["row_count"] is None


def sql(query, row_level=True, intent="test"):
    return coordinator.run_safe_sql(query, intent, tool_context=CTX, row_level=row_level)


def test_row_level_sql_over_every_view(store):
    rows = sql("SELECT loan_ref, customer_ref, branch_id, days_past_due, asset_classification, provision_held, provision_required "
               "FROM vw_asset_quality WHERE days_past_due > 90 ORDER BY days_past_due DESC")
    assert rows["status"] == "success" and 0 < rows["row_count"] <= 200
    assert rows["rows"][0]["loan_ref"].startswith("LR-")
    big = sql("SELECT loan_ref FROM vw_compliance_impact")
    assert big["row_count"] == 200 and "LIMIT 200" in big["executed_sql"].upper()
    assert sql("SELECT customer_ref, kyc_risk_category, kyc_status FROM vw_kyc_status LIMIT 5")["row_count"] == 5
    agg = sql("SELECT asset_classification, COUNT(*) AS n, SUM(outstanding_balance) AS exposure FROM vw_asset_quality "
              "GROUP BY asset_classification", row_level=False)
    assert agg["status"] == "success" and agg["row_count"] >= 5
    assert audited(store, "run_safe_sql")


@pytest.mark.parametrize("query", [
    "SELECT full_name FROM vw_kyc_status",
    "SELECT customer_ref, email FROM vw_kyc_status",
    "SELECT pan, COUNT(*) FROM vw_kyc_status GROUP BY pan",
    "SELECT manager_name FROM vw_branch_directory",
    "SELECT customer_id FROM vw_compliance_impact",
    "SELECT phone FROM vw_kyc_status",
])
def test_sql_refuses_pii_columns(store, query):
    for row_level in (True, False):
        out = sql(query, row_level=row_level)
        assert out["status"] == "rejected" and "PII" in out["reason"]
    assert [e for e in audited(store, "run_safe_sql") if e["executed_query"] == "REJECTED"]


@pytest.mark.parametrize("query", [
    "SELECT * FROM customers", "SELECT loan_ref FROM loans", "SELECT full_name FROM customers",
    "DELETE FROM vw_kyc_status", "UPDATE vw_kyc_status SET kyc_status = 'X'", "DROP VIEW vw_kyc_status",
    "SELECT 1; SELECT 2", "SELECT * FROM vw_kyc_status", "SELECT customer_ref FROM vw_kyc_status UNION SELECT loan_ref FROM loans",
])
def test_sql_refuses_non_views_writes_and_star(store, query):
    assert sql(query)["status"] == "rejected"


def test_aggregate_mode_still_refuses_row_level(store):
    assert sql("SELECT loan_ref FROM vw_asset_quality", row_level=False)["status"] == "rejected"
    with pytest.raises(SqlRejected):
        validate_sql("SELECT loan_ref FROM vw_asset_quality")
    assert validate_sql("SELECT loan_ref FROM vw_asset_quality", row_level=True).sql


def test_sql_unknown_column_is_an_error_not_a_crash(store):
    out = sql("SELECT nonexistent_col FROM vw_kyc_status")
    assert out["status"] == "error" and "describe_bank_data" in out["message"]


# governance --------------------------------------------------------------------------------------------------------
def test_governance_tools(store):
    policies = T.list_policies()
    assert policies["count"] >= 10 and T.list_policies(filter="credit")["count"] >= 1
    first = policies["policies"][0]["policy_id"]
    policy = T.get_policy(first)
    assert policy["status"] == "success" and policy["clauses"][0]["maps_to"] is not None
    assert T.get_policy("policy:nope")["status"] == "error"
    controls = T.list_controls()
    assert controls["count"] > 20 and T.list_controls(status="planned")["count"] < controls["count"]
    control = T.get_control(controls["controls"][0]["id"])
    assert control["control"]["id"] == controls["controls"][0]["id"] and "policies" in control
    assert T.get_control("control:nope")["status"] == "error"
    gaps = T.readiness_gaps("2026-10-17")
    assert gaps["instruments"] and {"phase", "gaps"} <= set(gaps["instruments"][0])
    assert T.readiness_gaps("2026-10-17", instrument="nope")["status"] == "error"
    assert T.readiness_gaps("yesterday")["status"] == "error"
    ontology = T.get_ontology()
    assert ontology["status"] == "success" and ontology["packs"] and "pending_proposals" in ontology
    for tool in ("list_policies", "get_policy", "list_controls", "get_control", "readiness_gaps", "get_ontology"):
        assert audited(store, tool), tool


def test_results_are_size_capped():
    big = {"status": "success", "rows": [{"v": "x" * 200} for _ in range(500)], "other": [1, 2, 3]}
    capped = T._cap(big)
    assert capped["truncated"] and len(json.dumps(capped)) <= T.MAX_RESULT_CHARS and capped["other"] == [1, 2, 3]
    assert T._cap({"status": "success", "blob": "z" * 50_000})["truncated"]


# masking of safe identifiers -----------------------------------------------------------------------------------------
OPAQUE = ["run-523456789012", "rpt-412345678906", "act-6789012345ab", "wl-4123567890ab", "LR-54F773C", "CUST-0B80F3",
          "rule:DOR.MCS.REC.32/01.01.003/2023-24:fixed-rate-choice", "product:home-loan-floating", "branch:pune-kothrud"]


class Overeager:
    """Stands in for a fuzzy detector (Cloud DLP or Gemma) that flags digit runs and ref fragments inside ids."""

    def find(self, text):
        import re
        found = [Finding(m, "PHONE_NUMBER", "fake") for m in re.findall(r"\d{4,}|54F773C|0B80F3|pune", text)]
        return found + [Finding("Rahul Sharma", "PERSON_NAME", "fake")] * ("Rahul Sharma" in text)


def test_opaque_ids_survive_masking_while_pii_is_masked():
    sanitizer = Sanitizer()
    sanitizer.gemma = Overeager()
    text = "Check " + ", ".join(OPAQUE) + ". Customer Rahul Sharma, PAN ABCPV1234K, mail rahul@example.com."
    out = sanitizer.sanitize(text, "s1").text
    for ident in OPAQUE:
        assert ident in out, ident
    assert "Rahul" not in out and "ABCPV1234K" not in out and "rahul@example.com" not in out


def test_masking_callback_keeps_ids_in_text_and_tool_responses():
    from google.genai import types

    request = SimpleNamespace(contents=[
        SimpleNamespace(parts=[types.Part(text=f"Explain {OPAQUE[0]} and {OPAQUE[1]} for loan {OPAQUE[4]}; officer mail a@b.com")]),
        SimpleNamespace(parts=[types.Part(function_response=types.FunctionResponse(
            name="get_run", response={"run_id": OPAQUE[0], "report_id": OPAQUE[1], "rows": [{"loan_ref": OPAQUE[4], "customer_ref": OPAQUE[5]}],
                                      "note": "contact a@b.com"}))]),
    ])
    callbacks.mask_pii_before_model(SimpleNamespace(session=SimpleNamespace(id="s2"), state={}), request)
    text = request.contents[0].parts[0].text
    assert OPAQUE[0] in text and OPAQUE[1] in text and OPAQUE[4] in text and "a@b.com" not in text
    response = request.contents[1].parts[0].function_response.response
    assert response["run_id"] == OPAQUE[0] and response["rows"][0]["customer_ref"] == OPAQUE[5] and "a@b.com" not in response["note"]
