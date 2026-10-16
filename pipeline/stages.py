"""Pipeline stages over a JSON-serialisable state mapping.

The same functions back the ADK agent tools (state = ``ToolContext.state``,
persisted by ``DatabaseSessionService``) and the deterministic runner (state =
a plain dict).  Agents decide *when* to call a stage and explain its result;
the numbers always come from here.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, MutableMapping

from bank_mcp_server.secure_engine import SecureEngine
from core.app_store import get_app_store, new_id, now
from core.config import DATA_DIR, get_settings
from core.models import (BlastRadius, CheckResult, ExtractedPolicy, ImpactReport, ParsedDocument, ProposedAction,
                         RuleMapping, ValidationIssue)
from ingestion.documents import parse_document
from ingestion.extractor import extract_policy
from knowledge_graph.mapper import PolicyMapper
from knowledge_graph.store import get_graph_store
from knowledge_graph.traversal import blast_radius

State = MutableMapping[str, Any]
SAFE_ID = re.compile(r"^[A-Za-z0-9:._/-]+$")
MANIFEST = DATA_DIR / "bank" / "manifest.json"


def default_as_of() -> str:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())["reference_date"]
    return date.today().isoformat()


def _source_bytes(source: dict) -> bytes:
    if source.get("gcs_uri"):
        from google.cloud import storage

        bucket, _, name = source["gcs_uri"].removeprefix("gs://").partition("/")
        return storage.Client(project=get_settings().project).bucket(bucket).blob(name).download_as_bytes()
    path = Path(source["path"]).resolve()
    allowed = (DATA_DIR.resolve(), get_settings().runtime_dir.resolve())
    if not any(path.is_relative_to(root) for root in allowed):
        raise PermissionError("Document path is outside the allowed directories.")
    return path.read_bytes()


# 1. Reader ---------------------------------------------------------------
def stage_parse(state: State) -> dict:
    source = state["source"]
    document = parse_document(_source_bytes(source), source["filename"], source.get("gcs_uri") or source.get("display_uri"))
    state["document"] = document.model_dump()
    return {"document_id": document.document_id, "title": document.title, "parser": document.parser,
            "chunks": len(document.chunks), "headings": sorted({h for c in document.chunks for h in c.headings})[:12]}


def stage_extract(state: State) -> dict:
    document = ParsedDocument.model_validate(state["document"])
    extraction, issues, provenance = extract_policy(document)
    state["extraction"] = extraction.model_dump()
    state["validation_issues"] = [i.model_dump() for i in issues]
    state["extraction_provenance"] = provenance
    return {"policy": extraction.policy.model_dump(), "provenance": provenance,
            "rules": [{"rule_id": r.rule_id, "title": r.title, "effective_date": r.effective_date,
                       "checks": [c.kind for c in r.checks], "citation": r.citation.snippet} for r in extraction.rules],
            "issues": state["validation_issues"]}


# 2. Mapper ---------------------------------------------------------------
def stage_map(state: State) -> dict:
    extraction = ExtractedPolicy.model_validate(state["extraction"])
    mapper = PolicyMapper(get_graph_store())
    supersession = mapper.upsert_policy(extraction.policy)
    mappings = [mapper.map_rule(extraction.policy, rule) for rule in extraction.rules]
    state["mappings"] = [m.model_dump() for m in mappings]
    return {"supersedes": supersession, "mappings": [
        {"rule_id": m.rule_id, "products": m.product_ids, "processes": m.process_ids, "segments": m.segment_ids,
         "controls": m.control_ids, "edges_written": len(m.edges_written),
         "resolutions": [r.model_dump() for r in m.resolutions]} for m in mappings]}


# 3a. GraphRAG --------------------------------------------------------------
def stage_blast_radius(state: State, as_of: str | None = None) -> dict:
    as_of = as_of or state.get("as_of") or default_as_of()
    state["as_of"] = as_of
    store = get_graph_store()
    radii = [blast_radius(store, m["rule_id"], as_of) for m in state["mappings"]]
    state["blast_radius"] = [r.model_dump() for r in radii]
    return {"as_of": as_of, "query_language": store.query_language, "rules": [
        {"rule_id": r.rule_id, "products": [p["name"] for p in r.products], "processes": [p["name"] for p in r.processes],
         "control_gaps": r.control_gaps, "owners": sorted({o["team"] for o in r.owners})} for r in radii]}


# 3b. Precedents (long-term memory over prior reports and open actions) ------
def stage_precedents(state: State) -> dict:
    store = get_app_store()
    policy_id = state["extraction"]["policy"]["policy_id"]
    prior = store.list_reports(limit=5, policy_id=policy_id)
    open_actions = [a for a in store.list_actions(status="PENDING_APPROVAL") if a["rule_id"].startswith(f"rule:{policy_id.removeprefix('policy:')}")]
    precedents = {"prior_reports": prior, "open_actions": [
        {"action_id": a["action_id"], "workflow_id": a["workflow_id"], "rule_id": a["rule_id"], "target_count": a["target_count"]}
        for a in open_actions]}
    state["precedents"] = precedents
    return precedents


# 4. Data analyst -------------------------------------------------------------
def stage_profile(state: State) -> dict:
    products = sorted({p["id"] for r in state["blast_radius"] for p in r["products"] if SAFE_ID.match(p["id"])})
    if not products:
        state["data_profile"] = {"rows": []}
        return state["data_profile"]
    in_list = ", ".join(f"'{p}'" for p in products)
    sql = ("SELECT product_id, status, COUNT(*) AS loans, SUM(outstanding_balance) AS exposure_inr "
           f"FROM vw_compliance_impact WHERE product_id IN ({in_list}) GROUP BY product_id, status ORDER BY product_id, status")
    result = SecureEngine().run_safe_sql(sql, intent="Portfolio profile of products in the blast radius")
    state["data_profile"] = result
    return result


# 5. Evaluator ----------------------------------------------------------------
def stage_evaluate(state: State) -> dict:
    extraction = ExtractedPolicy.model_validate(state["extraction"])
    radii = {r["rule_id"]: BlastRadius.model_validate(r) for r in state["blast_radius"]}
    engine = SecureEngine()
    results = [res for rule in extraction.rules for res in engine.evaluate_rule(rule, radii[rule.rule_id], state["as_of"])]
    state["results"] = [r.model_dump() for r in results]
    return {"results": [{"rule_id": r.rule_id, "check": r.description, "status": r.status, "violations": r.violations,
                         "in_scope": r.in_scope, "penalty_inr": r.penalty_inr, "by_branch": r.by_branch,
                         "worklist_id": r.worklist_id} for r in results]}


# 6. Orchestrator ---------------------------------------------------------------
def stage_propose(state: State, workflow_id: str, rule_id: str, worklist_id: str | None, notice_template: str,
                  owner: str | None = None) -> dict:
    results = [CheckResult.model_validate(r) for r in state.get("results", [])]
    match = next((r for r in results if r.rule_id == rule_id and (worklist_id is None or r.worklist_id == worklist_id)), None)
    if match is None:
        raise ValueError("rule_id/worklist_id must come from an evaluated result.")
    owner = owner or _owner_for(state, rule_id)
    action = SecureEngine().propose_workflow(workflow_id, rule_id, worklist_id, match.violations, notice_template, owner)
    state["actions"] = [*state.get("actions", []), action.model_dump()]
    return {"action_id": action.action_id, "status": action.status, "target_count": action.target_count}


def default_actions(state: State) -> list[ProposedAction]:
    """Deterministic remediation plan for gaps the Orchestrator did not cover."""
    covered = {(a["rule_id"], a["workflow_id"]) for a in state.get("actions", [])}
    engine = SecureEngine()
    proposals = []
    for raw in state.get("results", []):
        result = CheckResult.model_validate(raw)
        if result.status != "GAP":
            continue
        plan: list[tuple[str, str]] = []
        if result.kind == "record_flag":
            plan.append(("SEND_BORROWER_NOTICE", "Dear {{CUSTOMER_NAME}}, regarding loan {{LOAN_REF}} serviced at {{BRANCH}}: "
                         f"{result.description.lower()} is required under the latest RBI direction. Your relationship "
                         "manager will contact you with the details and your options."))
        elif result.kind == "event_deadline":
            plan.append(("BRANCH_MANAGER_ALERT", "Dear {{MANAGER_NAME}}, {{COUNT}} closed loan(s) at {{BRANCH}} have original property "
                         "documents overdue beyond the 30-day RBI limit. Release them and record the reason for delay today."))
            if result.penalty_inr:
                plan.append(("COMPENSATION_PAYOUT_REVIEW", f"Review statutory delay compensation of INR {result.penalty_inr:,.0f} "
                             "for {{COUNT}} borrower(s) at INR 5,000 per day of delay."))
        elif result.kind == "control_required":
            plan.append(("PRODUCT_CONTROL_CHANGE", f"Add control '{result.description}' to "
                         f"{', '.join(result.products_missing_control)}; {{{{COUNT}}}} loan(s) are affected."))
        for workflow_id, template in plan:
            if (result.rule_id, workflow_id) in covered:
                continue
            covered.add((result.rule_id, workflow_id))
            proposals.append(engine.propose_workflow(workflow_id, result.rule_id, result.worklist_id, result.violations, template,
                                                     _owner_for(state, result.rule_id, workflow_id)))
    return proposals


def _owner_for(state: State, rule_id: str, workflow_id: str = "") -> str:
    radius = next((r for r in state.get("blast_radius", []) if r["rule_id"] == rule_id), None)
    if workflow_id == "SEND_BORROWER_NOTICE":
        return "Customer Communications"
    if workflow_id == "COMPENSATION_PAYOUT_REVIEW":
        return "Finance"
    owners = Counter(o["team"] for o in (radius or {}).get("owners", []) if o["owner_of"].startswith("product:"))
    return owners.most_common(1)[0][0] if owners else "Compliance"


def stage_finalize(state: State, narrative: str = "", mode: str = "agentic") -> dict:
    store = get_app_store()
    extras = default_actions(state)
    actions = [ProposedAction.model_validate(a) for a in state.get("actions", [])] + extras
    results = [CheckResult.model_validate(r) for r in state.get("results", [])]
    extraction = ExtractedPolicy.model_validate(state["extraction"])
    loans: dict[str, dict] = {}       # record-level violations (flags, deadlines)
    exposed: dict[str, dict] = {}     # loans on products missing a required control
    for result in results:
        if result.worklist_id:
            target = exposed if result.kind == "control_required" else loans
            for item in store.get_worklist(result.worklist_id):
                target.setdefault(item["loan_ref"], item)
    radii = [BlastRadius.model_validate(r) for r in state["blast_radius"]]
    branch_names = SecureEngine().branch_names()
    summary = {
        "rules": len(extraction.rules),
        "checks": len(results),
        "gaps": sum(r.status == "GAP" for r in results),
        "products_in_scope": len({p["id"] for r in radii for p in r.products}),
        "loans_with_violations": len(loans),
        "customers_with_violations": len({l["customer_ref"] for l in loans.values()}),
        "loans_exposed_to_control_gaps": len(exposed),
        "statutory_penalty_inr": sum(r.penalty_inr for r in results),
        "control_gaps": sorted({f"{pid}: {c}" for r in radii for pid, gaps in r.control_gaps.items() for c in gaps}),
        "by_branch": dict(Counter(branch_names.get(l["branch_id"], l["branch_id"]) for l in loans.values()).most_common()),
        "overall_status": "NON_COMPLIANT" if any(r.status == "GAP" for r in results) else "COMPLIANT",
        "privacy_redactions": int(state.get("privacy_redactions", 0)),
    }
    report = ImpactReport(
        report_id=new_id("rpt"), tenant_id=get_settings().tenant_id, policy=extraction.policy, rules=extraction.rules,
        generated_at=now(), as_of=state["as_of"], mode=mode, blast_radius=radii, results=results, summary=summary,
        actions=actions, narrative=narrative.strip(),
        validation_issues=[ValidationIssue.model_validate(i) for i in state.get("validation_issues", [])],
        limitations=[
            "Synthetic data: every customer, loan and branch is fictional.",
            "Decision support only: actions stay PENDING_APPROVAL until a compliance officer approves them.",
            "Rules marked PENDING_REVIEW must be approved before the graph edges are treated as authoritative.",
        ],
    )
    body = report.model_dump()
    body["extraction_provenance"] = state.get("extraction_provenance", {})
    body["data_profile"] = state.get("data_profile", {})
    body["precedents"] = state.get("precedents", {})
    store.save_report(body)
    state["report_id"] = report.report_id
    state["actions"] = [a.model_dump() for a in actions]
    return {"report_id": report.report_id, "summary": summary, "actions": len(actions)}
