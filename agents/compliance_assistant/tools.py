"""Read-only introspection tools for the compliance assistant: runs, reports, graph, governance.

Every tool is tenant-scoped (the app store and graph store are bound to the configured tenant and every lookup goes through
them), size-capped, and audited with ``get_app_store().audit(...)``. Direct personal identifiers never leave these tools:
PII keys are stripped from every result and the SQL/GQL guards refuse PII columns. Opaque ids (run, report, rule, loan_ref,
customer_ref) are returned as they are; the model-bound masking in ``privacy`` leaves them alone.

Each tool returns ``{"status": "success", ...}`` or ``{"status": "error", "message": ...}`` so the model can recover.
"""

from __future__ import annotations

import functools
import json
import logging
import re
from datetime import date
from typing import Any, Callable

from bank_mcp_server import control_library
from core.app_store import get_app_store
from knowledge_graph import governance
from knowledge_graph.gql_guard import GqlRejected, validate_gql
from knowledge_graph.ontology import PII_COLUMNS
from knowledge_graph.store import get_graph_store
from knowledge_graph.traversal import blast_radius

log = logging.getLogger(__name__)
ACTOR = "compliance_assistant"
MAX_RESULT_CHARS = 16_000
MAX_LIST = 100
MAX_EVENTS = 200
EVENT_PAYLOAD_CHARS = 1_500
# Keys that identify a person: never returned by any assistant tool, whatever the source row held.
STRIPPED_KEYS = frozenset(PII_COLUMNS | {"owner_id", "created_by", "decided_by", "user_id", "requester", "by"}) - {"name"}
GQL_PII = re.compile(r"\.\s*(" + "|".join(sorted(re.escape(c) for c in PII_COLUMNS - {"name"})) + r")\b", re.IGNORECASE)


class ToolError(ValueError):
    """A user-correctable problem (unknown id, bad argument); returned to the model as status=error."""


# plumbing --------------------------------------------------------------------------------------------------------
def _strip(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if str(k).lower() not in STRIPPED_KEYS}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _size(value: Any) -> int:
    return len(json.dumps(value, default=str))


def _cap(result: dict, limit: int = MAX_RESULT_CHARS) -> dict:
    """Keep a result under ``limit`` characters by halving its longest list; as a last resort return a text preview."""
    if _size(result) <= limit:
        return result
    result = dict(result)
    for _ in range(40):
        lists = [(k, v) for k, v in result.items() if isinstance(v, list) and len(v) > 1]
        if not lists:
            break
        key, items = max(lists, key=lambda kv: _size(kv[1]))
        result[key] = items[: max(1, len(items) // 2)]
        result["truncated"] = True
        result.setdefault("truncated_lists", {})[key] = f"showing {len(result[key])} of {len(items)}"
        if _size(result) <= limit:
            return result
    return {"status": result.get("status", "success"), "truncated": True, "chars": _size(result),
            "preview": json.dumps(result, default=str)[:limit]}


def _tool(fn: Callable[..., dict]) -> Callable[..., dict]:
    """Status envelope, PII-key stripping, size cap and an audit entry for every call (also failures)."""
    name = fn.__name__

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict:
        intent = {**dict(zip(fn.__code__.co_varnames, args)), **kwargs}
        try:
            result = _cap(_strip({"status": "success", **fn(*args, **kwargs)}))
            summary = {"keys": sorted(result)[:12], "chars": _size(result), **({"truncated": True} if result.get("truncated") else {})}
        except (ToolError, KeyError, ValueError, PermissionError, GqlRejected, NotImplementedError) as error:
            result = {"status": "error", "message": str(error).strip("'\"")}
            summary = {"error": result["message"][:300]}
        except Exception as error:  # surface as a tool error so the agent can recover
            log.exception("Assistant tool %s failed", name)
            result = {"status": "error", "message": f"{type(error).__name__}: {error}"}
            summary = {"error": result["message"][:300]}
        try:
            get_app_store().audit(ACTOR, name, intent, f"assistant tool {name}", summary)
        except Exception:  # an audit outage must not hide the answer, but it is logged loudly
            log.exception("Could not audit assistant tool %s", name)
        return result

    return wrapper


def _limit(value: int, default: int, ceiling: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(number, ceiling))


def _csv(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _iso(value: str | None, default: str | None = None) -> str:
    text = (value or "").strip() or (default or date.today().isoformat())
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        raise ToolError(f"'{value}' is not an ISO date (YYYY-MM-DD).") from None


def _clip(value: Any, limit: int) -> Any:
    text = json.dumps(value, default=str)
    return value if len(text) <= limit else {"truncated": True, "chars": len(text), "preview": text[:limit]}


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _run_or_raise(run_id: str) -> dict:
    run = get_app_store().get_run(run_id)  # tenant-scoped lookup
    if not run:
        raise ToolError(f"Unknown run {run_id}")
    return run


def _report_or_raise(report_id: str) -> dict:
    report = get_app_store().get_report(report_id)  # tenant-scoped lookup
    if not report:
        raise ToolError(f"Unknown report {report_id}")
    return report


def _run_view(run: dict) -> dict:
    document = run.get("document") or {}
    return {
        "run_id": run["run_id"], "status": run["status"], "mode": run["mode"], "current_stage": run.get("current_stage"),
        "report_id": run.get("report_id"), "error": (run.get("error") or "")[:500] or None, "created_at": run["created_at"],
        "started_at": run.get("started_at"), "finished_at": run.get("finished_at"), "updated_at": run.get("updated_at"),
        "as_of": run.get("as_of"), "source_kind": run.get("source_kind"), "filename": run.get("filename"),
        "document": {k: v for k, v in document.items() if isinstance(v, (str, int, float, bool)) and len(str(v)) <= 200},
        "rerun_of": run.get("rerun_of"), "review_rules": run.get("review_rules"), "rule_count": len(run.get("rules") or []),
        "previous_report_id": run.get("previous_report_id"),
    }


# runs ------------------------------------------------------------------------------------------------------------
@_tool
def list_runs(status: str = "", limit: int = 20) -> dict:
    """List analysis runs newest first (tenant-wide), with status, stage, report id and error.

    Args:
        status: optional comma-separated statuses, e.g. FAILED or QUEUED,RUNNING (empty for all).
        limit: how many runs, at most 50.
    """
    statuses = [s.upper() for s in _csv(status)]
    runs = get_app_store().list_runs(_limit(limit, 20, 50), statuses or None)
    return {"count": len(runs), "runs": [_run_view(r) for r in runs]}


@_tool
def get_run(run_id: str) -> dict:
    """Details of one analysis run: status, current stage, per-stage timings, error, report id, source kind and document.

    Args:
        run_id: id such as run-1234abcd5678.
    """
    run = _run_or_raise(run_id)
    stages: dict[str, dict] = {}
    for event in get_app_store().events(run_id):
        if event["kind"] not in {"stage_started", "stage_completed", "stage_skipped", "run_failed", "run_cancelled"}:
            continue
        stage = stages.setdefault(event["stage"], {"stage": event["stage"], "started": None, "finished": None, "state": None})
        if event["kind"] == "stage_started":
            stage["started"] = stage["started"] or event["ts"]
            stage["state"] = "running"
        elif event["kind"] == "stage_completed":
            stage["finished"], stage["state"] = event["ts"], "completed"
        else:
            stage["state"] = event["kind"].removeprefix("stage_").removeprefix("run_")
    return {"run": _run_view(run), "stages": list(stages.values())}


@_tool
def get_run_events(run_id: str, kinds: str = "", limit: int = 100) -> dict:
    """Events of a run in order: stage changes, agent messages, tool calls (inputs and outputs truncated), errors.

    The model's thinking is excluded unless 'thinking' is named in kinds.

    Args:
        run_id: id such as run-1234abcd5678.
        kinds: optional comma-separated kinds, e.g. tool_call,tool_result,run_failed (empty for all but thinking).
        limit: how many events, at most 200.
    """
    _run_or_raise(run_id)  # tenant check before the events table, which is keyed by run id only
    wanted = set(_csv(kinds))
    events = [e for e in get_app_store().events(run_id) if (e["kind"] in wanted if wanted else e["kind"] != "thinking")]
    shown = events[: _limit(limit, 100, MAX_EVENTS)]
    return {"run_id": run_id, "total_matching": len(events), "events": [
        {"seq": e["seq"], "ts": e["ts"], "stage": e["stage"], "kind": e["kind"], "payload": _clip(e["payload"], EVENT_PAYLOAD_CHARS)}
        for e in shown]}


def _checks_by_key(report: dict) -> tuple[dict[str, str], dict[str, dict]]:
    titles = {r["rule_id"]: r.get("title", r["rule_id"]) for r in report.get("rules", [])}
    keyed: dict[str, dict] = {}
    for check in report.get("results", []):
        key = f"{_norm(titles.get(check['rule_id'], check['rule_id']))}||{_norm(check['description'])}"
        while key in keyed:
            key += "+"
        keyed[key] = {"rule": titles.get(check["rule_id"], check["rule_id"]), "description": check["description"],
                      **{k: check.get(k) for k in ("status", "violations", "in_scope")}}
    return {_norm(t): t for t in titles.values()}, keyed


@_tool
def compare_runs(run_a: str, run_b: str) -> dict:
    """Compare the reports of two runs (A earlier, B later): rules added/removed, per-check status changes, headline deltas.

    Rules are matched by title, checks by rule title plus description, as in the UI's compare view.

    Args:
        run_a: the earlier run id.
        run_b: the later run id.
    """
    runs = {"a": _run_or_raise(run_a), "b": _run_or_raise(run_b)}
    reports = {}
    for side, run in runs.items():
        if not run.get("report_id"):
            raise ToolError(f"Run {run['run_id']} has no report (status {run['status']}); nothing to compare.")
        reports[side] = _report_or_raise(run["report_id"])
    titles_a, checks_a = _checks_by_key(reports["a"])
    titles_b, checks_b = _checks_by_key(reports["b"])
    changes = []
    for key in sorted(set(checks_a) | set(checks_b)):
        a, b = checks_a.get(key), checks_b.get(key)
        if a is None or b is None or any(a[f] != b[f] for f in ("status", "violations", "in_scope")):
            changes.append({"rule": (a or b)["rule"], "description": (a or b)["description"],
                            "run_a": {k: a[k] for k in ("status", "violations", "in_scope")} if a else "not present",
                            "run_b": {k: b[k] for k in ("status", "violations", "in_scope")} if b else "not present"})
    summary_a, summary_b = reports["a"]["summary"], reports["b"]["summary"]
    deltas = {k: {"run_a": summary_a.get(k), "run_b": summary_b.get(k)} for k in sorted(set(summary_a) | set(summary_b))
              if isinstance(summary_a.get(k, 0), (int, float)) and isinstance(summary_b.get(k, 0), (int, float))
              and summary_a.get(k) != summary_b.get(k)}
    return {"run_a": {"run_id": run_a, "report_id": runs["a"]["report_id"], "overall": summary_a.get("overall_status")},
            "run_b": {"run_id": run_b, "report_id": runs["b"]["report_id"], "overall": summary_b.get("overall_status")},
            "rules_added_in_b": [titles_b[k] for k in titles_b if k not in titles_a],
            "rules_removed_from_a": [titles_a[k] for k in titles_a if k not in titles_b],
            "checks_total": len(set(checks_a) | set(checks_b)), "checks_changed": len(changes), "changes": changes[:MAX_LIST],
            "summary_changes": deltas}


# reports ---------------------------------------------------------------------------------------------------------
@_tool
def list_impact_reports() -> dict:
    """List recent impact reports (newest first) with their headline numbers."""
    return {"reports": get_app_store().list_reports(limit=20)}


@_tool
def get_impact_report(report_id: str) -> dict:
    """A report's summary, narrative, per-check results, scope notes, unexecutable obligations, control decisions,
    document classification, applicability and actions (no per-loan rows; use get_report_checks and run_safe_sql for those).

    Args:
        report_id: id such as rpt-1234abcd5678.
    """
    report = _report_or_raise(report_id)
    titles = {r["rule_id"]: r.get("title") for r in report.get("rules", [])}
    return {
        "report_id": report_id, "policy": report["policy"], "as_of": report["as_of"], "mode": report.get("mode"),
        "generated_at": report.get("generated_at"), "summary": report["summary"], "narrative": report.get("narrative"),
        "results": [{**{k: r.get(k) for k in ("rule_id", "description", "status", "violations", "in_scope", "exposure_inr",
                                              "penalty_inr", "by_branch", "evidence")}, "rule_title": titles.get(r["rule_id"])}
                    for r in report["results"]],
        "scope": [{"rule_id": b["rule_id"], "scope_status": b.get("scope_status"), "scope_notes": b.get("scope_notes", []),
                   "products": [p.get("name") or p.get("id") for p in b.get("products", [])],
                   "control_gaps": b.get("control_gaps", {})} for b in report.get("blast_radius", [])],
        "unexecutable": report.get("unexecutable", []), "control_decisions": report.get("control_decisions", []),
        "document_classification": report.get("document_classification"), "applicability": report.get("applicability", []),
        "validation_issues": report.get("validation_issues", []), "limitations": report.get("limitations", []),
        "actions": [{k: a.get(k) for k in ("action_id", "workflow_id", "rule_id", "target_count", "status")}
                    for a in report.get("actions", [])],
    }


@_tool
def get_report_checks(report_id: str, rule: str = "") -> dict:
    """Per-check detail of a report: status, in-scope and violation counts, exposure, penalty, by-branch and by-product
    splits, evidence text, missing data and worklist id.

    Args:
        report_id: id such as rpt-1234abcd5678.
        rule: optional rule id or part of a rule title to filter on.
    """
    report = _report_or_raise(report_id)
    titles = {r["rule_id"]: r.get("title", "") for r in report.get("rules", [])}
    needle = _norm(rule)
    checks = [c for c in report["results"] if not needle or needle == _norm(c["rule_id"]) or needle in _norm(c["rule_id"])
              or needle in _norm(titles.get(c["rule_id"], ""))]
    keys = ("rule_id", "check_index", "kind", "description", "status", "in_scope", "violations", "exposure_inr", "penalty_inr",
            "by_branch", "by_product", "products_missing_control", "worklist_id", "evidence", "missing_data")
    return {"report_id": report_id, "total_matching": len(checks), "checks": [
        {**{k: c.get(k) for k in keys}, "rule_title": titles.get(c["rule_id"])} for c in checks[:MAX_LIST]]}


@_tool
def list_report_actions(report_id: str) -> dict:
    """Remediation actions proposed for a report, with workflow, target count, approval status and notice template.

    Args:
        report_id: id such as rpt-1234abcd5678.
    """
    _report_or_raise(report_id)  # tenant check; the actions table is keyed by report id only
    actions = get_app_store().list_actions(report_id)
    return {"report_id": report_id, "count": len(actions), "actions": [
        {**{k: a.get(k) for k in ("action_id", "workflow_id", "rule_id", "worklist_id", "target_count", "status", "decided_at", "owner")},
         "notice_template": (a.get("notice_template") or "")[:600]} for a in actions[:MAX_LIST]]}


@_tool
def time_travel_blast_radius(rule_id: str, as_of: str) -> dict:
    """Recompute a rule's blast radius using graph edges valid on a past or future business date.

    Args:
        rule_id: rule node id, e.g. rule:DOR.MCS.REC.32/01.01.003/2023-24:fixed-rate-choice
        as_of: ISO date, e.g. 2024-01-15
    """
    radius = blast_radius(get_graph_store(), rule_id, _iso(as_of))
    return {"as_of": as_of, "products": [p["name"] for p in radius.products], "control_gaps": radius.control_gaps,
            "scope_status": radius.scope_status, "scope_notes": radius.scope_notes}


# graph -----------------------------------------------------------------------------------------------------------
@_tool
def query_graph(gql: str) -> dict:
    """Run a read-only GQL query on the knowledge graph (Spanner Graph 'BankArchitecture'); at most 200 rows.

    The query must start with GRAPH BankArchitecture, be MATCH ... RETURN, and filter on @tenant_id. Writes are rejected.
    Personal-data properties cannot be returned. Locally (no Spanner) raw GQL is unavailable: use get_node and neighbours.

    Args:
        gql: e.g. GRAPH BankArchitecture MATCH (r:Rule)-[:GOVERNS]->(p:Product) WHERE r.tenant_id = @tenant_id RETURN r.id, p.name
    """
    if GQL_PII.search(gql or ""):
        raise ToolError("The query reads a personal-data property; those are not available to the assistant.")
    safe = validate_gql(gql)
    rows = get_graph_store().run_gql(safe, {})
    return {"executed_gql": safe, "row_count": len(rows[:200]), "rows": rows[:200]}


@_tool
def get_node(node_id: str) -> dict:
    """One graph node (type, name, attributes) with counts of its current edges by type and direction.

    Args:
        node_id: e.g. product:home-loan-floating or rule:DOR.MCS.REC.32/01.01.003/2023-24:fixed-rate-choice
    """
    store = get_graph_store()
    node = store.node(node_id)
    if not node:
        raise ToolError(f"Unknown node {node_id}")
    counts: dict[str, dict[str, int]] = {}
    for direction in ("out", "in"):
        for edge in store.neighbours([node_id], None, governance.LATEST, direction):
            bucket = counts.setdefault(direction, {})
            bucket[edge["edge_type"]] = bucket.get(edge["edge_type"], 0) + 1
    return {"node": node, "edges_now": counts}


@_tool
def neighbours(node_id: str, edge_types: str = "", as_of: str = "", direction: str = "out") -> dict:
    """Nodes connected to a graph node by edges valid on a business date.

    Args:
        node_id: graph node id.
        edge_types: optional comma-separated edge types, e.g. GOVERNS,REQUIRES (empty for all).
        as_of: ISO date (default today); edges are bitemporal.
        direction: out (default), in or both.
    """
    if direction not in {"out", "in", "both"}:
        raise ToolError("direction must be out, in or both")
    store = get_graph_store()
    if not store.node(node_id):
        raise ToolError(f"Unknown node {node_id}")
    day, types, edges = _iso(as_of), _csv(edge_types) or None, []
    for way in ("out", "in") if direction == "both" else (direction,):
        for edge in store.neighbours([node_id], types, day, way):
            edges.append({"direction": way, "edge_type": edge["edge_type"], "source": edge["source_id"], "target": edge["target_id"],
                          "valid_from": edge["valid_from"], "valid_to": edge["valid_to"],
                          "node": {k: edge["node"][k] for k in ("id", "type", "name")}})
    return {"node_id": node_id, "as_of": day, "count": len(edges), "edges": edges[:MAX_LIST]}


# circular text (RAG) ---------------------------------------------------------------------------------------------
@_tool
def search_circulars(query: str, top_k: int = 5, document_id: str = "") -> dict:
    """Search the full text of the circulars that were ingested, by meaning. Use it for questions about what a circular
    says (wording, thresholds, exceptions, dates, paragraph numbers) that the extracted rules and reports do not answer.
    Returns passages with the document title, headings, pages, a similarity score and the chunk id to cite.

    Args:
        query: what to look for, in natural language.
        top_k: number of passages (1-10).
        document_id: restrict to one document (doc-...), from a previous result or a report; empty searches all.
    """
    from core import circular_index

    if not query.strip():
        raise ToolError("query is required")
    if not circular_index.enabled():
        raise NotImplementedError("Circular search needs the GCP backend (Vertex AI embeddings).")
    hits = circular_index.search(query.strip()[:1000], top_k, document_id.strip())
    for hit in hits:
        hit["text"] = _clip(hit["text"], 1500)
    return {"query": query.strip()[:200], "count": len(hits), "passages": hits,
            **({} if hits else {"note": "No indexed circular matches; the document may not have been indexed yet."})}


# governance ------------------------------------------------------------------------------------------------------
def _policy(ref: str) -> dict:
    for policy in control_library.policies():
        if ref in (policy["policy_id"], policy["code"]) or ref == policy["policy_id"].removeprefix("policy:"):
            return policy
    raise ToolError(f"Unknown policy {ref}")


@_tool
def list_policies(filter: str = "") -> dict:
    """The bank's internal policies: id, title, kind, owner, version, status, effective date, next review and clause count.

    Args:
        filter: optional text matched against id, code, title, kind, owner and status.
    """
    needle = _norm(filter)
    found = [p for p in control_library.policies()
             if not needle or needle in _norm(" ".join(str(p.get(k, "")) for k in ("policy_id", "code", "title", "kind", "owner_team", "status")))]
    return {"count": len(found), "policies": [
        {**{k: p.get(k) for k in ("policy_id", "code", "title", "kind", "owner_team", "version", "status", "effective_date", "next_review")},
         "clauses": len(p["clauses"])} for p in found]}


@_tool
def get_policy(policy_id: str) -> dict:
    """One internal policy with every clause, the regulatory paragraphs it maps to and the controls that enforce it.

    Args:
        policy_id: e.g. policy:POL-CRD-001 or POL-CRD-001.
    """
    policy = _policy(policy_id)
    names = {c["id"]: c["name"] for c in control_library.controls()}
    instruments = control_library.instruments()
    clauses = [{**{k: c.get(k) for k in ("clause_id", "title", "summary", "owner_team", "effective_from", "status")},
                "maps_to": [{"instrument": m["instrument"], "reference": instruments.get(m["instrument"], {}).get("reference"),
                             "paragraphs": m.get("paragraphs", []), "rules": m.get("rules", [])} for m in c.get("maps_to", [])],
                "controls": [{"id": cid, "name": names.get(cid)} for cid in c.get("controls", [])]} for c in policy["clauses"]]
    return {"policy": {k: v for k, v in policy.items() if k != "clauses"}, "clauses": clauses}


@_tool
def list_controls(status: str = "") -> dict:
    """The bank's control library with implementation status (implemented, partial, planned, absent) and last test result.

    Args:
        status: optional implementation status to filter on.
    """
    wanted = status.strip().lower()
    found = [c for c in control_library.controls() if not wanted or c["implementation_status"] == wanted]
    return {"count": len(found), "controls": [
        {k: c.get(k) for k in ("id", "name", "kind", "owner_team", "implementation_status", "implemented_from", "planned_go_live",
                               "last_tested", "last_test_result")} for c in found]}


@_tool
def get_control(control_id: str) -> dict:
    """One control: description, owner, status today, the regulatory paragraphs it satisfies, policies that enforce it, evidence.

    Args:
        control_id: e.g. control:reset-statement
    """
    for control in control_library.controls():
        if control_id in (control["id"], control["id"].removeprefix("control:")):
            return {"control": control, "status_today": control_library.status_on(control, date.today().isoformat()),
                    "policies": control_library.policy_for_control(control["id"])}
    raise ToolError(f"Unknown control {control_id}")


@_tool
def readiness_gaps(as_of: str, instrument: str = "") -> dict:
    """Controls not yet implemented on a date, per regulatory instrument: readiness gaps if the instrument is not yet in
    force on that date, control gaps if it is.

    Args:
        as_of: ISO date.
        instrument: optional library key (e.g. iracp) or RBI reference; empty for every instrument.
    """
    day = _iso(as_of)
    keys = [instrument] if instrument else sorted(control_library.instruments())
    reports = []
    for key in keys:
        try:
            ready = control_library.readiness(key, day)
        except KeyError:
            raise ToolError(f"Unknown instrument {key}") from None
        reports.append({k: ready[k] for k in ("instrument", "reference", "effective_date", "phase", "ready", "gaps")})
    return {"as_of": day, "instruments": reports}


@_tool
def get_ontology() -> dict:
    """The active ontology: enabled packs and versions, vocabulary (capabilities, record flags, events, views,
    workflows), and capability proposals pending review."""
    return governance.describe_ontology(get_graph_store())
