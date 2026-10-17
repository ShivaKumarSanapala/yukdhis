"""Secure Execution Engine - the only code that touches operational data.

Runs inside the bank VPC (Cloud Run with VPC egress to private Cloud SQL).
Agents call it through tools/MCP and receive only aggregates, opaque
``worklist_id`` handles and session-scoped tokens.  Every call is written to a
dual-entry audit log: the LLM's intent and the exact query executed.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from typing import Any

from sqlalchemy import bindparam, text

from bank_mcp_server.operational_store import analyst_engine
from bank_mcp_server.sql_guard import SqlRejected, validate_sql
from core.app_store import AppStore, get_app_store, new_id
from core.config import get_settings
from core.models import BlastRadius, CheckResult, ComplianceCheck, ProposedAction, Rule
from knowledge_graph.ontology import CAPABILITIES, PII_COLUMNS, RECORD_FLAGS, SANITIZED_VIEWS, WORKFLOWS, ontology_summary
from privacy.detectors import structured_findings
from privacy.vault import VAULT

MAX_SAMPLES = 5


def _d(value: Any) -> date | None:
    return date.fromisoformat(str(value)[:10]) if value not in (None, "") else None


class SecureEngine:
    def __init__(self, store: AppStore | None = None) -> None:
        self.store = store or get_app_store()
        self.settings = get_settings()
        self.dialect = "postgres" if self.settings.operational_backend == "gcp" else "sqlite"
        self._branch_names: dict[str, str] | None = None

    # Tool 1: schema discovery ------------------------------------------
    def get_graph_ontology(self, target_domain: str = "All") -> dict:
        return {**ontology_summary(target_domain), "sanitized_views": SANITIZED_VIEWS}

    # Guarded ad-hoc SQL ---------------------------------------------------
    def run_safe_sql(self, sql: str, intent: str, actor: str = "data_analyst_agent") -> dict:
        try:
            guarded = validate_sql(sql, dialect=self.dialect)
        except SqlRejected as error:
            self.store.audit(actor, "run_safe_sql", {"intent": intent, "sql": sql}, "REJECTED", str(error))
            return {"status": "rejected", "reason": str(error),
                    "hint": "Use COUNT/SUM/AVG with GROUP BY over vw_* views only; no row-level identifiers."}
        with analyst_engine().connect() as conn:
            result = conn.execute(text(guarded.sql))
            columns = list(result.keys())
            rows = [dict(zip(columns, (_plain(v) for v in row))) for row in result.fetchall()]
        leaked = [c for c in columns if c.lower() in PII_COLUMNS]
        if leaked:
            raise RuntimeError(f"Guard bypass detected for columns {leaked}")
        rows = [{k: (_scrub(v) if isinstance(v, str) else v) for k, v in row.items()} for row in rows]
        self.store.audit(actor, "run_safe_sql", {"intent": intent, "sql": sql}, guarded.sql, {"rows": len(rows)})
        return {"status": "success", "executed_sql": guarded.sql, "row_count": len(rows), "rows": rows}

    # Tool 2: aggregate impact (deterministic compliance checks) ---------
    def evaluate_rule(self, rule: Rule, radius: BlastRadius, as_of: str) -> list[CheckResult]:
        results = [self._evaluate(rule, index, check, radius, as_of) for index, check in enumerate(rule.checks)]
        self.store.audit("evaluator_agent", "analyze_segment_impact", {"rule_id": rule.rule_id, "as_of": as_of},
                         f"{len(rule.checks)} parameterised check queries over {sorted({'vw_compliance_impact', 'vw_loan_events'})}",
                         [{"check": r.check_index, "status": r.status, "violations": r.violations} for r in results])
        return results

    def _evaluate(self, rule: Rule, index: int, check: ComplianceCheck, radius: BlastRadius, as_of: str) -> CheckResult:
        product_ids = [p["id"] for p in radius.products]
        base = CheckResult(rule_id=rule.rule_id, check_index=index, kind=check.kind, description=check.description,
                           status="NOT_EVALUATED")
        effective, cutoff = _d(rule.effective_date), _d(as_of)
        if effective and cutoff and effective > cutoff:
            return base.model_copy(update={"status": "UPCOMING", "evidence": f"Effective {rule.effective_date}, after analysis "
                                                                           f"date {as_of}; readiness not assessed"})
        if rule.modality == "MAY":
            return base.model_copy(update={"evidence": "Permission for the bank, not an obligation"})
        if radius.scope_status == "UNRESOLVED":
            return base.model_copy(update={"evidence": f"Scope could not be resolved: {' '.join(radius.scope_notes) or 'no notes'}"})
        if not product_ids:
            return base.model_copy(update={"evidence": "No products governed by this rule at the analysis date."})
        status = rule.scope.record_status
        if status is None and not any(c.kind == "event_deadline" and c.trigger_event == "LOAN_CLOSED" for c in rule.checks):
            status = "ACTIVE"  # control gaps and flags concern the live book unless the rule targets closures
        if check.kind == "record_flag":
            if check.record_field not in RECORD_FLAGS:
                return base.model_copy(update={"evidence": f"Unsupported field {check.record_field!r}."})
            rows = self._loans(product_ids, status, extra=check.record_field)
            bad = [r for r in rows if bool(r[check.record_field]) != bool(check.expected)]
            return self._finish(base, rule, rows, bad, f"{check.record_field} != {check.expected} on vw_compliance_impact")
        if check.kind == "control_required":
            # The mapper stamps the canonical control id; older extractions fall back to the capability's own id.
            control = check.control_id or CAPABILITIES.get(check.capability or "", f"control:{(check.capability or '').replace('_', '-')}")
            missing = sorted(pid for pid, gaps in radius.control_gaps.items() if control in gaps)
            rows = self._loans(product_ids, status)
            bad = [r for r in rows if r["product_id"] in missing]
            result = self._finish(base, rule, rows, bad, f"graph: products without PROVIDES->{control} as of {as_of}")
            return result.model_copy(update={"products_missing_control": missing,
                                             "status": "GAP" if missing else "COMPLIANT"})
        if check.kind == "event_deadline":
            return self._event_deadline(base, rule, check, product_ids, as_of)
        return base

    def _loans(self, product_ids: list[str], status: str | None, extra: str | None = None) -> list[dict]:
        columns = "loan_ref, customer_ref, product_id, branch_id, outstanding_balance" + (f", {extra}" if extra else "")
        sql = f"SELECT {columns} FROM vw_compliance_impact WHERE product_id IN :pids" + (" AND status = :status" if status else "")
        query = text(sql).bindparams(bindparam("pids", expanding=True))
        params: dict[str, Any] = {"pids": product_ids}
        if status:
            params["status"] = status
        with analyst_engine().connect() as conn:
            return [dict(row._mapping) for row in conn.execute(query, params)]

    def _event_deadline(self, base: CheckResult, rule: Rule, check: ComplianceCheck, product_ids: list[str], as_of: str) -> CheckResult:
        query = text("SELECT loan_ref, customer_ref, product_id, branch_id, event_type, occurred_on FROM vw_loan_events "
                     "WHERE product_id IN :pids AND event_type IN :events").bindparams(
            bindparam("pids", expanding=True), bindparam("events", expanding=True))
        with analyst_engine().connect() as conn:
            rows = [dict(r._mapping) for r in conn.execute(query, {"pids": product_ids,
                                                                    "events": [check.trigger_event, check.required_event]})]
        effective, cutoff = _d(rule.effective_date), _d(as_of)
        loans: dict[str, dict] = {}
        for row in sorted(rows, key=lambda r: str(r["occurred_on"])):
            loan = loans.setdefault(row["loan_ref"], {k: row[k] for k in ("loan_ref", "customer_ref", "product_id", "branch_id")})
            when = _d(row["occurred_on"])
            if row["event_type"] == check.trigger_event and "trigger" not in loan:
                loan["trigger"] = when
            elif row["event_type"] == check.required_event and "trigger" in loan and "done" not in loan and when >= loan["trigger"]:
                loan["done"] = when
        in_scope, bad, pending = [], [], 0
        for loan in loans.values():
            trigger = loan.get("trigger")
            if not trigger or trigger < effective or trigger > cutoff:
                continue
            in_scope.append(loan)
            done = loan.get("done") if loan.get("done") and loan["done"] <= cutoff else None
            elapsed = ((done or cutoff) - trigger).days
            if elapsed > check.deadline_days:
                loan["delay_days"] = elapsed - check.deadline_days
                loan["open"] = done is None
                bad.append(loan)
            elif done is None:
                pending += 1
        penalty = sum(l["delay_days"] for l in bad) * (check.penalty_per_day_inr or 0)
        evidence = (f"{check.trigger_event} on/after {rule.effective_date} without {check.required_event} within "
                    f"{check.deadline_days} days as of {as_of}; {sum(l['open'] for l in bad)} still open, {pending} within window.")
        result = self._finish(base, rule, in_scope, bad, evidence, worklist_fields=("delay_days", "open"))
        return result.model_copy(update={"penalty_inr": float(penalty)})

    def _finish(self, base: CheckResult, rule: Rule, in_scope: list[dict], bad: list[dict], evidence: str,
                worklist_fields: tuple[str, ...] = ()) -> CheckResult:
        names = self.branch_names()
        worklist_id = None
        if bad:
            worklist_id = self.store.save_worklist(rule.rule_id, [
                {k: item.get(k) for k in ("loan_ref", "customer_ref", "product_id", "branch_id", *worklist_fields)} for item in bad])
        return base.model_copy(update={
            "status": "GAP" if bad else "COMPLIANT", "in_scope": len(in_scope), "violations": len(bad),
            "exposure_inr": float(sum(item.get("outstanding_balance") or 0 for item in bad)),
            "by_branch": dict(Counter(names.get(i["branch_id"], i["branch_id"]) for i in bad).most_common()),
            "by_product": dict(Counter(i["product_id"] for i in bad).most_common()),
            "worklist_id": worklist_id, "evidence": evidence,
        })

    def branch_names(self) -> dict[str, str]:
        if self._branch_names is None:
            with analyst_engine().connect() as conn:
                self._branch_names = {r[0]: f"{r[1]}, {r[2]}" for r in conn.execute(
                    text("SELECT branch_id, branch_name, city FROM vw_branch_directory"))}
        return self._branch_names

    # Tool 3: tokenized samples ------------------------------------------
    def fetch_anonymized_samples(self, worklist_id: str, sample_size: int, session_id: str) -> dict:
        items = self.store.get_worklist(worklist_id)[: max(1, min(sample_size, MAX_SAMPLES))]
        samples = []
        for item in items:
            sample = {k: v for k, v in item.items() if k not in {"loan_ref", "customer_ref"}}
            sample["loan_token"] = VAULT.tokenize(session_id, item["loan_ref"], "LOAN")
            sample["customer_token"] = VAULT.tokenize(session_id, item["customer_ref"], "CUSTOMER")
            sample["branch"] = self.branch_names().get(item["branch_id"], item["branch_id"])
            samples.append(sample)
        self.store.audit("data_analyst_agent", "fetch_anonymized_samples", {"worklist_id": worklist_id, "n": sample_size},
                         f"worklist lookup {worklist_id}", {"returned": len(samples)})
        return {"worklist_id": worklist_id, "samples": samples, "note": "Tokens are session-scoped; no PII is included."}

    # Tool 4: remediation router (proposal only) --------------------------
    def propose_workflow(self, workflow_id: str, rule_id: str, worklist_id: str | None, target_count: int,
                         notice_template: str, owner: str) -> ProposedAction:
        if workflow_id not in WORKFLOWS:
            raise ValueError(f"workflow_id must be one of {sorted(WORKFLOWS)}")
        action = ProposedAction(action_id=new_id("act"), workflow_id=workflow_id, rule_id=rule_id, worklist_id=worklist_id,
                                target_count=target_count, notice_template=notice_template.strip()[:4000], owner=owner)
        self.store.audit("orchestrator_agent", "trigger_compliance_workflow", action.model_dump(), "PROPOSED (human approval required)",
                         {"action_id": action.action_id})
        return action


def _plain(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "__float__") and not isinstance(value, (int, float, bool)):
        return float(value)
    return value


def _scrub(value: str) -> str:
    for finding in structured_findings(value):
        value = value.replace(finding.text, f"[{finding.entity_type}]")
    return re.sub(r"\s+", " ", value)
