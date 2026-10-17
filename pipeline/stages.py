"""Pipeline stages over a JSON-serialisable state mapping.

The same functions back the ADK agent tools (state = ``ToolContext.state``,
persisted by ``DatabaseSessionService``) and the deterministic runner (state =
a plain dict).  Agents decide *when* to call a stage and explain its result;
the numbers always come from here.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterator, MutableMapping

from bank_mcp_server.secure_engine import SecureEngine
from core.app_store import get_app_store, new_id, now
from core.config import DATA_DIR, get_settings
from core.models import (Applicability, BlastRadius, CheckResult, ControlDecision, ExtractedPolicy, ImpactReport,
                         ParsedDocument, ProposedAction, Rule, RuleMapping, ValidationIssue)
from ingestion import extractor
from ingestion.cache import EXTRACTOR_VERSION, ExtractionCache
from ingestion.classifier import classify_document
from ingestion.documents import guess_mime, parse_document
from ingestion.extractor import extract_policy
from ingestion.references import announced_instrument, extract_references, pdf_link_uris, self_identifiers
from knowledge_graph.mapper import PolicyMapper
from knowledge_graph.store import get_graph_store
from knowledge_graph.traversal import blast_radius
from pipeline import applicability as gate

log = logging.getLogger(__name__)
State = MutableMapping[str, Any]
SAFE_ID = re.compile(r"^[A-Za-z0-9:._/-]+$")
MANIFEST = DATA_DIR / "bank" / "manifest.json"
_EVENT_SINK: contextvars.ContextVar[Callable[[str, str, dict], None] | None] = contextvars.ContextVar(
    "stage_event_sink", default=None)


@contextlib.contextmanager
def event_sink(emit: Callable[[str, str, dict], None]) -> Iterator[None]:
    """Route stage events (``document_classified``, ``applicability``) to ``emit(stage, kind, payload)`` in this context."""
    token = _EVENT_SINK.set(emit)
    try:
        yield
    finally:
        _EVENT_SINK.reset(token)


def _emit(state: State, stage: str, kind: str, payload: dict) -> None:
    """Record a run event: via the context sink if one is set, else via the run id in state (agentic sessions carry
    ``run_id``). Stage outputs carry the same data, so the runner's ``stage_completed`` event shows it either way."""
    try:
        sink = _EVENT_SINK.get()
        if sink is not None:
            sink(stage, kind, payload)
            return
        run_id = state.get("run_id") or (state.get("source") or {}).get("run_id")
        if run_id:
            get_app_store().add_event(str(run_id), stage, kind, payload)
    except Exception:  # events are observability; never fail a stage on them
        log.warning("Could not record %s event", kind, exc_info=True)


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
    content = _source_bytes(source)
    document = parse_document(content, source["filename"], source.get("gcs_uri") or source.get("display_uri"))
    state["document"] = document.model_dump()
    state["document_sha256"] = hashlib.sha256(content).hexdigest()
    state["source_links"] = pdf_link_uris(content) if guess_mime(source["filename"]) == "application/pdf" else []
    classification = _classify(state, document)
    return {"document_id": document.document_id, "title": document.title, "parser": document.parser,
            "chunks": len(document.chunks), "headings": sorted({h for c in document.chunks for h in c.headings})[:12],
            "document_classification": _classified_payload(classification)}


def _classify(state: State, document: ParsedDocument) -> dict:
    classification = classify_document(document).model_dump()
    state["document_classification"] = classification
    _emit(state, "reader", "document_classified", _classified_payload(classification))
    return classification


def _classified_payload(classification: dict) -> dict:
    return {k: classification.get(k) for k in ("doc_type", "confidence", "reason")}


def stage_extract(state: State) -> dict:
    """Document-type gate, then rule extraction, served from the extraction cache when the same document bytes were
    extracted before by the same extractor version and model. ``source["refresh_extraction"]`` bypasses the cache."""
    document = ParsedDocument.model_validate(state["document"])
    source = state.get("source") or {}
    classification = state.get("document_classification") or _classify(state, document)
    references = extract_references(document.text, list(state.get("source_links") or []),
                                    self_identifiers(document.title, document.text))
    state["references"] = [r.model_dump() for r in references]
    announced = announced_instrument(references) if classification.get("doc_type") == "press_release" else None
    extractor.ensure_binding(classification, announced.model_dump() if announced else None)

    use_llm = extractor.llm_enabled()
    model = get_settings().reasoning_model if use_llm else "none"
    sha = state.get("document_sha256") or hashlib.sha256(_source_bytes(source)).hexdigest()
    refresh = bool(source.get("refresh_extraction"))
    cache = ExtractionCache()
    hit = cache.get(sha, model) if use_llm and not refresh else None
    if hit:
        extraction = ExtractedPolicy.model_validate(hit["extraction"])
        extraction.policy.source_uri = document.source_uri
        issues = [ValidationIssue.model_validate(i) for i in hit.get("issues", [])]
        provenance = {**hit.get("provenance", {}), "cached": True, "cached_at": hit.get("created_at")}
    else:
        extraction, issues, provenance = extract_policy(document, use_llm=use_llm, classification=classification,
                                                        report=lambda kind, payload: _emit(state, "reader", kind, payload))
        provenance = {**provenance, "cached": False}
        if use_llm:  # only model output is cached; templates are deterministic already
            cache.put(sha, model, {"extraction": extraction.model_dump(), "issues": [i.model_dump() for i in issues],
                                   "provenance": provenance})
    provenance.update(extractor_version=EXTRACTOR_VERSION, document_sha256=sha, refreshed=refresh,
                      document_type=classification.get("doc_type"))
    own = {extraction.policy.reference.lower()}
    references = [r for r in references if r.ref.lower() not in own]
    state["references"] = [r.model_dump() for r in references]
    state["extraction"] = extraction.model_dump()
    state["validation_issues"] = [i.model_dump() for i in issues]
    state["extraction_provenance"] = provenance
    return {"policy": extraction.policy.model_dump(), "provenance": provenance, "cached": provenance["cached"],
            "document_classification": _classified_payload(classification), "references": state["references"],
            "rules": [{"rule_id": r.rule_id, "title": r.title, "effective_date": r.effective_date,
                       "checks": [c.kind for c in r.checks], "citation": r.citation.snippet} for r in extraction.rules],
            "unexecutable": [u.model_dump() for u in extraction.unexecutable],
            "issues": state["validation_issues"]}


# 2. Mapper ---------------------------------------------------------------
def stage_map(state: State) -> dict:
    extraction = ExtractedPolicy.model_validate(state["extraction"])
    mapper = PolicyMapper(get_graph_store())
    supersession = mapper.upsert_policy(extraction.policy)
    mappings = [mapper.map_rule(extraction.policy, rule) for rule in extraction.rules]
    state["mappings"] = [m.model_dump() for m in mappings]
    for mapping in mappings:
        for decision in mapping.control_decisions:
            _emit(state, "mapper", "control_canonicalised", _decision_payload(decision))
    assessments = _assess_applicability(state, extraction)
    for assessment in assessments.values():
        _emit(state, "mapper", "applicability", _applicability_payload(assessment))
    return {"supersedes": supersession, "mappings": [
        {"rule_id": m.rule_id, "products": m.product_ids, "processes": m.process_ids, "segments": m.segment_ids,
         "controls": m.control_ids, "scope_status": m.scope_status, "scope_notes": m.scope_notes, "edges_written": len(m.edges_written),
         "resolutions": [r.model_dump() for r in m.resolutions],
         "control_decisions": [_decision_payload(d) for d in m.control_decisions]} for m in mappings],
        "applicability": [_applicability_payload(a) for a in assessments.values()]}


def _with_controls(state: State, extraction: ExtractedPolicy) -> ExtractedPolicy:
    """Stamp each check's canonical control id from the mapper's decisions. The extraction in state stays exactly as
    extracted (and reviewed); the evaluator and the applicability gate read the canonical controls through this.
    A ``control_id`` that arrived with the extraction is never trusted: only the mapper sets it."""
    by_check = {(d.get("rule_id"), d.get("check_index")): d.get("control_id")
                for m in state.get("mappings", []) for d in m.get("control_decisions", [])}
    for rule in extraction.rules:
        for index, check in enumerate(rule.checks):
            check.control_id = by_check.get((rule.rule_id, index))
    return extraction


def _decision_payload(decision: ControlDecision) -> dict:
    return {"rule_id": decision.rule_id, "check_index": decision.check_index, "term": decision.term,
            "control_id": decision.control_id, "decision": decision.decision, "method": decision.method,
            "confidence": decision.confidence, "needs_review": decision.needs_review, "rationale": decision.rationale[:300],
            "model_decision": decision.model_decision,
            "candidates": [{k: c.get(k) for k in ("id", "lexical", "embedding")} for c in decision.candidates[:3]]}


# 2b. Applicability gate (between mapping and evaluation) ------------------------
def _assess_applicability(state: State, extraction: ExtractedPolicy | None = None) -> dict[str, Applicability]:
    extraction = extraction or _with_controls(state, ExtractedPolicy.model_validate(state["extraction"]))
    assessments = gate.assess_rules(extraction.rules, state.get("mappings", []))
    state["applicability"] = {a.rule_id: a.model_dump() for a in assessments}
    return {a.rule_id: a for a in assessments}


def _applicability(state: State) -> dict[str, Applicability]:
    """Gate decisions for the current extraction; computed on demand if the mapper stage did not record them."""
    stored = state.get("applicability")
    rule_ids = {r["rule_id"] for r in state.get("extraction", {}).get("rules", [])}
    if not stored or set(stored) != rule_ids:
        return _assess_applicability(state)
    return {rid: Applicability.model_validate(a) for rid, a in stored.items()}


def _applicability_payload(assessment: Applicability) -> dict:
    return {"rule_id": assessment.rule_id, "applicable": assessment.applicable, "reasons": assessment.reasons,
            "missing_data": assessment.missing_data}


# 3a. GraphRAG --------------------------------------------------------------
def stage_blast_radius(state: State, as_of: str | None = None) -> dict:
    as_of = as_of or state.get("as_of") or default_as_of()
    state["as_of"] = as_of
    store = get_graph_store()
    assessments = _applicability(state) if "extraction" in state else {}
    radii = []
    for mapping in state["mappings"]:
        radius = blast_radius(store, mapping["rule_id"], as_of)
        radius.scope_status = mapping.get("scope_status", "RESOLVED")
        radius.scope_notes = list(mapping.get("scope_notes", []))
        radius.applicability = assessments.get(mapping["rule_id"])
        if radius.scope_status == "UNRESOLVED":  # fail closed: no products for a rule whose scope could not be resolved
            radius.products, radius.control_gaps = [], {}
        if gate.is_gated(radius.applicability):  # not applicable here, or not evaluable: nothing of this bank is in scope
            radius.products, radius.control_gaps = [], {}
        radii.append(radius)
    state["blast_radius"] = [r.model_dump() for r in radii]
    return {"as_of": as_of, "query_language": store.query_language, "rules": [
        {"rule_id": r.rule_id, "scope_status": r.scope_status, "scope_notes": r.scope_notes, "products": [p["name"] for p in r.products], "processes": [p["name"] for p in r.processes],
         "control_gaps": r.control_gaps, "owners": sorted({o["team"] for o in r.owners}),
         "applicable": r.applicability.applicable if r.applicability else None} for r in radii]}


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
    extraction = _with_controls(state, ExtractedPolicy.model_validate(state["extraction"]))
    radii = {r["rule_id"]: BlastRadius.model_validate(r) for r in state["blast_radius"]}
    engine = SecureEngine()
    assessments = _applicability(state)
    results: list[CheckResult] = []
    for rule in extraction.rules:
        assessment = assessments.get(rule.rule_id)
        if gate.is_gated(assessment):
            results += _not_evaluated(rule, radii[rule.rule_id], assessment, state["as_of"])
        else:
            results += engine.evaluate_rule(rule, radii[rule.rule_id], state["as_of"])
    state["results"] = [r.model_dump() for r in results]
    return {"results": [{"rule_id": r.rule_id, "check": r.description, "status": r.status, "violations": r.violations,
                         "in_scope": r.in_scope, "penalty_inr": r.penalty_inr, "by_branch": r.by_branch,
                         "worklist_id": r.worklist_id, "evidence": r.evidence, "missing_data": r.missing_data}
                        for r in results]}


def _not_evaluated(rule: Rule, radius: BlastRadius, assessment: Applicability, as_of: str) -> list[CheckResult]:
    """A gated rule runs no query and writes no worklist, so no remediation can follow from it."""
    text = gate.evidence(assessment)
    if radius.scope_status == "UNRESOLVED":
        text = f"Scope could not be resolved: {' '.join(radius.scope_notes) or 'no notes'}. {text}"
    if rule.effective_date > as_of:
        text += f" Effective {rule.effective_date}, after the analysis date {as_of}."
    return [CheckResult(rule_id=rule.rule_id, check_index=index, kind=check.kind, description=check.description,
                        status="NOT_EVALUATED", evidence=text, missing_data=list(assessment.missing_data))
            for index, check in enumerate(rule.checks)]


# 6. Orchestrator ---------------------------------------------------------------
def stage_propose(state: State, workflow_id: str, rule_id: str, worklist_id: str | None, notice_template: str,
                  owner: str | None = None) -> dict:
    results = [CheckResult.model_validate(r) for r in state.get("results", [])]
    match = next((r for r in results if r.rule_id == rule_id and (worklist_id is None or r.worklist_id == worklist_id)), None)
    if match is None:
        raise ValueError("rule_id/worklist_id must come from an evaluated result.")
    assessment = _applicability(state).get(rule_id) if "extraction" in state else None
    if gate.is_gated(assessment):
        raise ValueError(f"No action can be proposed for {rule_id}. {gate.evidence(assessment)}")
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
        elif result.kind == "record_assertion":
            plan.append(("BRANCH_MANAGER_ALERT", "Dear {{MANAGER_NAME}}, {{COUNT}} account(s) at {{BRANCH}} do not meet an RBI requirement: "
                         f"{result.description.lower()}. Review each account, correct it in the core banking system and record the reason today."))
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
    if not radius or radius.get("scope_status") == "UNRESOLVED":
        return "Compliance"
    for prefix in ("process:", "product:"):  # the team running the rule's processes owns remediation before product owners
        owners = Counter(o["team"] for o in radius.get("owners", []) if o["owner_of"].startswith(prefix))
        if owners:
            return owners.most_common(1)[0][0]
    return "Compliance"


def overall_status(results: list[CheckResult], unresolved_rules: list[str], unexecutable: int = 0) -> str:
    """Obligations that cannot be machine-checked (``unexecutable``) mean the report can never be COMPLIANT."""
    if any(r.status == "GAP" for r in results):
        return "NON_COMPLIANT"
    if unexecutable:
        return "INCONCLUSIVE"
    if any(r.status == "UPCOMING" for r in results):
        return "UPCOMING"
    if not results or unresolved_rules or any(r.status == "NOT_EVALUATED" for r in results):
        return "INCONCLUSIVE"
    return "COMPLIANT"


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
                target.setdefault(item.get("loan_ref") or item.get("customer_ref"), item)  # customer-level views carry no loan_ref
    radii = [BlastRadius.model_validate(r) for r in state["blast_radius"]]
    branch_names = SecureEngine().branch_names()
    unresolved = sorted(r.rule_id for r in radii if r.scope_status == "UNRESOLVED")
    assessments = _applicability(state)
    gated = {rid for rid, a in assessments.items() if gate.is_gated(a)}
    actions = [a for a in actions if a.rule_id not in gated]  # belt and braces: gated rules never carry actions
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
        "overall_status": overall_status(results, unresolved, len(extraction.unexecutable)),
        "unresolved_rules": unresolved,
        "unexecutable_obligations": len(extraction.unexecutable),
        "not_applicable_rules": sorted(rid for rid, a in assessments.items() if a.applicable is False),
        "rules_missing_data": {rid: a.missing_data for rid, a in sorted(assessments.items())
                               if a.applicable is not True and a.missing_data},
        "document_type": (state.get("document_classification") or {}).get("doc_type"),
        "privacy_redactions": int(state.get("privacy_redactions", 0)),
    }
    report = ImpactReport(
        report_id=state.get("report_id") or new_id("rpt"), tenant_id=get_settings().tenant_id, policy=extraction.policy, rules=extraction.rules,
        generated_at=now(), as_of=state["as_of"], mode=mode, blast_radius=radii, results=results, summary=summary,
        actions=actions, narrative=narrative.strip(),
        validation_issues=[ValidationIssue.model_validate(i) for i in state.get("validation_issues", [])],
        unexecutable=extraction.unexecutable,
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
    body["document_classification"] = state.get("document_classification")
    body["references"] = state.get("references", [])
    body["applicability"] = [a.model_dump() for a in assessments.values()]
    body["control_decisions"] = [_decision_payload(ControlDecision.model_validate(d))
                                 for m in state.get("mappings", []) for d in m.get("control_decisions", [])]
    store.save_report(body)
    state["report_id"] = report.report_id
    state["actions"] = [a.model_dump() for a in actions]
    return {"report_id": report.report_id, "summary": summary, "actions": len(actions)}
