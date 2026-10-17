"""PII checksum validation and the UNRESOLVED-scope approval guard (no LLM calls)."""

import os
import tempfile

os.environ.update(YUKDHIS_RUNTIME_DIR=tempfile.mkdtemp(prefix="yukdhis-test-"), YUKDHIS_BACKEND="local",
                  AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

from privacy.detectors import _VERHOEFF_D, _VERHOEFF_P, structured_findings, verhoeff_valid  # noqa: E402


def _aadhaar(prefix: str) -> str:
    """Append the Verhoeff check digit to an 11-digit prefix."""
    check = 0
    for i, digit in enumerate(reversed(prefix + "0")):
        check = _VERHOEFF_D[check][_VERHOEFF_P[i % 8][int(digit)]]
    inverse = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]
    return prefix + str(inverse[check])


def _types(text: str) -> set[str]:
    return {f.entity_type for f in structured_findings(text)}


def test_aadhaar_needs_checksum_and_ids_do_not_match_and_card_needs_luhn():
    valid = _aadhaar("23456789012")
    assert verhoeff_valid(valid)
    assert "INDIA_AADHAAR" in _types(f"Aadhaar {valid}")
    bad = valid[:-1] + str((int(valid[-1]) + 1) % 10)
    assert "INDIA_AADHAAR" not in _types(f"Aadhaar {bad}")
    assert "INDIA_AADHAAR" not in _types(f"see rpt-{valid} and run-{valid}")  # hyphen-prefixed ids are not Aadhaar
    assert "CREDIT_CARD_NUMBER" in _types("card 4111 1111 1111 1111")
    assert "CREDIT_CARD_NUMBER" not in _types("card 4111 1111 1111 1112")


def test_approval_blocked_when_rule_scope_unresolved_but_reject_allowed():
    from fastapi.testclient import TestClient

    from api.main import app
    from core.app_store import get_app_store

    def seed(report_id: str, scope_status: str) -> str:
        action_id = f"act-{report_id}"
        get_app_store().save_report({
            "report_id": report_id, "policy": {"policy_id": "pol-guard", "title": "Guard"}, "summary": {}, "results": [],
            "blast_radius": [{"rule_id": "rule:guard", "scope_status": scope_status}],
            "actions": [{"action_id": action_id, "rule_id": "rule:guard", "workflow_id": "BRANCH_MANAGER_ALERT",
                         "status": "PENDING_APPROVAL"}]})
        return action_id

    blocked, rejected = seed("rpt-guard-1", "UNRESOLVED"), seed("rpt-guard-2", "UNRESOLVED")
    with TestClient(app) as client:
        response = client.post(f"/api/actions/{blocked}/decision", json={"decision": "APPROVED"})
        assert response.status_code == 409 and "scope" in response.json()["detail"]
        assert get_app_store().get_action(blocked)["status"] == "PENDING_APPROVAL"  # guard must not consume the action
        assert client.post(f"/api/actions/{rejected}/decision", json={"decision": "REJECTED"}).status_code == 200
        assert response.headers["x-request-id"]
