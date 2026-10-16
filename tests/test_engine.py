"""Core guarantees: deterministic blast radius, bitemporal graph, SQL guard, privacy, API + HITL flow."""

import os
import tempfile
import time

os.environ.update(YUKDHIS_RUNTIME_DIR=tempfile.mkdtemp(prefix="yukdhis-test-"), YUKDHIS_BACKEND="local",
                  AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import pytest  # noqa: E402

from bank_mcp_server.sql_guard import SqlRejected, validate_sql  # noqa: E402
from core.app_store import get_app_store  # noqa: E402
from knowledge_graph.store import get_graph_store  # noqa: E402
from knowledge_graph.traversal import blast_radius  # noqa: E402
from pipeline.deterministic import run_pipeline  # noqa: E402
from privacy.sanitizer import Sanitizer  # noqa: E402

CIRCULARS = "data/circulars/"
FLOATING = "rbi_2023_08_18_floating_rate_reset_emi_loans.md"
DOCS = "rbi_2023_09_13_release_of_property_documents.md"
FIXED_RATE_RULE = "rule:DOR.MCS.REC.32/01.01.003/2023-24:fixed-rate-choice"


def run(name):
    state = run_pipeline({"path": CIRCULARS + name, "filename": name})
    return get_app_store().get_report(state["report_id"])


def test_document_release_finds_exactly_the_planted_violations():
    report = run(DOCS)
    deadline = next(r for r in report["results"] if r["kind"] == "event_deadline")
    assert deadline["violations"] == 12
    assert deadline["by_branch"]["Koramangala, Bengaluru"] == 7
    assert deadline["penalty_inr"] > 0 and deadline["penalty_inr"] % 5000 == 0
    products = {p["id"] for p in report["blast_radius"][0]["products"]}
    assert "product:msme-term-loan" not in products and "product:personal-loan-floating" not in products  # unsecured/MSME out


def test_floating_rate_gap_and_bitemporal_time_travel():
    report = run(FLOATING)
    radius = next(r for r in report["blast_radius"] if r["rule_id"] == FIXED_RATE_RULE)
    assert radius["control_gaps"] == {"product:auto-loan-floating": ["control:fixed-rate-option"]}
    past = blast_radius(get_graph_store(), FIXED_RATE_RULE, "2024-01-15")  # control edge valid until 2024-07-01
    assert past.control_gaps == {}


@pytest.mark.parametrize("sql", [
    "DELETE FROM vw_compliance_impact",
    "SELECT COUNT(*) FROM customers",
    "SELECT loan_ref, COUNT(*) FROM vw_compliance_impact GROUP BY loan_ref",
    "SELECT * FROM vw_compliance_impact",
    "SELECT full_name FROM vw_compliance_impact",
    "SELECT 1; DROP TABLE loans",
])
def test_sql_guard_rejects_unsafe_queries(sql):
    with pytest.raises(SqlRejected):
        validate_sql(sql)


def test_sql_guard_allows_aggregates_and_caps_rows():
    guarded = validate_sql("SELECT branch_id, COUNT(*) AS n FROM vw_compliance_impact GROUP BY branch_id LIMIT 5000")
    assert guarded.sql.endswith("LIMIT 100")


def test_pii_is_tokenized_and_reversible():
    sanitizer, text = Sanitizer(), "Customer Rahul Verma, PAN ABCPV1234K, rahul@example.com, +91 98450 12345"
    masked = sanitizer.sanitize(text, "s1")
    assert all(v not in masked.text for v in ("Rahul", "ABCPV1234K", "rahul@example.com", "98450"))
    assert sanitizer.restore(masked.text, "s1") == text


def test_api_run_and_human_approval_dispatches_masked_notices():
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        run_id = client.post("/api/runs", data={"sample_id": DOCS, "mode": "deterministic"}).json()["run_id"]
        for _ in range(100):
            run_info = client.get(f"/api/runs/{run_id}").json()["run"]
            if run_info["status"] in {"SUCCEEDED", "FAILED"}:
                break
            time.sleep(0.1)
        assert run_info["status"] == "SUCCEEDED", run_info["error"]
        report = client.get(f"/api/reports/{run_info['report_id']}").json()
        alert = next(a for a in report["actions"] if a["workflow_id"] == "BRANCH_MANAGER_ALERT")
        decision = client.post(f"/api/actions/{alert['action_id']}/decision", json={"decision": "APPROVED"}).json()
        assert decision["dispatch"]["status"] == "dispatched"
        outbox = client.get(f"/api/actions/{alert['action_id']}/outbox").json()
        assert outbox and all("***@" in o["recipient"] for o in outbox)
        assert client.post(f"/api/actions/{alert['action_id']}/decision", json={"decision": "APPROVED"}).status_code == 409
