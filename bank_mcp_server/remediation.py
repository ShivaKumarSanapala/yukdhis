"""Action Engine: downstream hydration of *human-approved* actions.

The LLM only ever proposed ``{workflow_id, worklist_id, notice_template}``.
After a compliance officer approves it, this non-LLM service:

1. loads the worklist (opaque refs),
2. hydrates recipients from the core-banking PII tables with its own
   privileged connection,
3. renders the notice and hands it to delivery.

gcp:   the API publishes ``{action_id}`` to Pub/Sub; a push subscription invokes
       this engine on a separate Cloud Run service whose service account is the
       only identity with access to customer contact columns.
local: dispatched in-process.
The outbox keeps masked recipients only; real delivery goes to the bank's
notification gateway.
"""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy import bindparam, text

from bank_mcp_server.operational_store import hydrator_engine
from core.app_store import AppStore, get_app_store
from core.config import get_settings

log = logging.getLogger(__name__)
PLACEHOLDER = re.compile(r"\{\{(CUSTOMER_NAME|LOAN_REF|BRANCH|DELAY_DAYS|MANAGER_NAME|COUNT)\}\}")
BRANCH_WORKFLOWS = {"BRANCH_MANAGER_ALERT"}
TEAM_WORKFLOWS = {"PRODUCT_CONTROL_CHANGE", "COMPENSATION_PAYOUT_REVIEW"}


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def mask_name(name: str) -> str:
    return " ".join(part[:1] + "." for part in name.split())


def request_dispatch(action_id: str, store: AppStore | None = None) -> dict:
    """Called by the API after approval."""
    settings = get_settings()
    if settings.dispatch_backend == "gcp":
        settings.require("pubsub_topic")
        from google.cloud import pubsub_v1

        future = pubsub_v1.PublisherClient().publish(settings.pubsub_topic, json.dumps({"action_id": action_id}).encode(),
                                                     tenant_id=settings.tenant_id)
        return {"status": "queued", "message_id": future.result(timeout=30)}
    return dispatch(action_id, store)


def dispatch(action_id: str, store: AppStore | None = None) -> dict:
    store = store or get_app_store()
    action = store.get_action(action_id)
    if not action:
        raise KeyError(action_id)
    if action["status"] != "APPROVED":
        raise PermissionError(f"Action {action_id} is {action['status']}; only APPROVED actions are dispatched.")
    items = store.get_worklist(action["worklist_id"]) if action.get("worklist_id") else []
    template = action["notice_template"]
    delivered = 0
    with hydrator_engine().connect() as conn:
        if action["workflow_id"] in BRANCH_WORKFLOWS:
            per_branch: dict[str, list[dict]] = {}
            for item in items:
                per_branch.setdefault(item["branch_id"], []).append(item)
            rows = conn.execute(text("SELECT branch_id, branch_name, manager_name, manager_email FROM branches "
                                     "WHERE branch_id IN :ids").bindparams(bindparam("ids", expanding=True)),
                                {"ids": list(per_branch) or [""]}).mappings().all()
            for row in rows:
                body = _render(template, MANAGER_NAME=row["manager_name"], BRANCH=row["branch_name"],
                               COUNT=str(len(per_branch[row["branch_id"]])))
                store.add_outbox(action_id, "email", mask_email(row["manager_email"]), _mask_body(body, row["manager_name"]))
                delivered += 1
        elif action["workflow_id"] in TEAM_WORKFLOWS:
            body = _render(template, COUNT=str(action.get("target_count", len(items))))
            store.add_outbox(action_id, "ticket", action["owner"], body)
            delivered = 1
        else:
            refs = [item["loan_ref"] for item in items] or [""]
            rows = conn.execute(text(
                "SELECT l.loan_ref, c.full_name, c.email, b.branch_name FROM loans l "
                "JOIN customers c ON c.customer_id = l.customer_id JOIN branches b ON b.branch_id = c.branch_id "
                "WHERE l.loan_ref IN :refs").bindparams(bindparam("refs", expanding=True)), {"refs": refs}).mappings().all()
            delays = {item["loan_ref"]: item.get("delay_days") for item in items}
            for row in rows:
                body = _render(template, CUSTOMER_NAME=row["full_name"], LOAN_REF=row["loan_ref"], BRANCH=row["branch_name"],
                               DELAY_DAYS=str(delays.get(row["loan_ref"]) or ""))
                store.add_outbox(action_id, "email", mask_email(row["email"]), _mask_body(body, row["full_name"]))
                delivered += 1
    store.decide_action(action_id, "DISPATCHED", action.get("decided_by") or "action-engine", expected="APPROVED")
    store.audit("action-engine", "dispatch", {"action_id": action_id}, f"hydrated {len(items)} worklist items",
                {"delivered": delivered})
    return {"status": "dispatched", "delivered": delivered}


def _render(template: str, **values: str) -> str:
    return PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def _mask_body(body: str, name: str) -> str:
    return body.replace(name, mask_name(name)) if name else body
