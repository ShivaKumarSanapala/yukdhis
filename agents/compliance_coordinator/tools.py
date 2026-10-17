"""ADK function tools for the compliance pipeline.

Each tool is a thin wrapper over ``pipeline.stages`` that reads and writes
``tool_context.state`` (persisted by the session service), enforces a per-turn
call budget, and returns compact, PII-free results to the model.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable

from google.adk.tools import ToolContext

from bank_mcp_server.secure_engine import SecureEngine
from knowledge_graph.gql_guard import GqlRejected, validate_gql
from knowledge_graph.store import get_graph_store
from pipeline import stages
from pipeline.deterministic import GRAPH_TRAVERSAL_LOCK

log = logging.getLogger(__name__)
MAX_TOOL_CALLS_PER_TURN = 40


def _guarded(fn: Callable[..., dict]) -> Callable[..., dict]:
    @functools.wraps(fn)
    def wrapper(*args: Any, tool_context: ToolContext, **kwargs: Any) -> dict:
        calls = int(tool_context.state.get("temp:tool_calls", 0)) + 1
        tool_context.state["temp:tool_calls"] = calls
        if calls > MAX_TOOL_CALLS_PER_TURN:
            return {"status": "error", "message": "Tool-call budget exhausted for this run; summarise what you have."}
        try:
            return {"status": "success", **fn(*args, tool_context=tool_context, **kwargs)}
        except (KeyError, ValueError, PermissionError, GqlRejected, NotImplementedError) as error:
            return {"status": "error", "message": str(error)}
        except Exception as error:  # surface as tool error so the agent can recover
            log.exception("Tool %s failed", fn.__name__)
            return {"status": "error", "message": f"{type(error).__name__}: {error}"}

    return wrapper


HALT_KEY = "pipeline_halted"


def _halt_on_error(tool_context: ToolContext, run: Callable[[], dict]) -> dict:
    """Reading is a precondition for everything else: record a failure so later agents skip instead of guessing."""
    try:
        result = run()
    except Exception as error:
        tool_context.state[HALT_KEY] = str(error)[:1000] or type(error).__name__
        raise
    tool_context.state[HALT_KEY] = None  # a retry succeeded
    return result


def _need(state, key: str, hint: str) -> None:
    if key not in state:
        raise ValueError(f"'{key}' is not available yet: {hint}")


# Reader ---------------------------------------------------------------------
@_guarded
def parse_regulatory_document(tool_context: ToolContext) -> dict:
    """Parse the uploaded regulatory circular with Document AI (layout-aware semantic chunks).

    Returns the document id, title, parser used, chunk count and section headings.
    """
    _need(tool_context.state, "source", "no document was attached to this run")
    return _halt_on_error(tool_context, lambda: stages.stage_parse(tool_context.state))


@_guarded
def extract_and_validate_rules(tool_context: ToolContext) -> dict:
    """Extract machine-executable rules with Gemini structured output and validate them deterministically.

    Returns the policy, each rule's checks and verbatim citation, and any validation issues.
    """
    _need(tool_context.state, "document", "call parse_regulatory_document first")
    return _halt_on_error(tool_context, lambda: stages.stage_extract(tool_context.state))


# Mapper ---------------------------------------------------------------------
@_guarded
def get_graph_ontology(target_domain: str, tool_context: ToolContext) -> dict:
    """Return the BIAN knowledge-graph schema (node labels, edge types, capabilities, views). No data rows.

    Args:
        target_domain: RetailBanking, CommercialLoans, KYC or All.
    """
    return SecureEngine().get_graph_ontology(target_domain or "All")


@_guarded
def map_rules_to_graph(tool_context: ToolContext) -> dict:
    """Resolve rule wording to canonical BIAN products/processes/segments and upsert bitemporal graph edges.

    Returns per-rule products, processes, segments, controls and entity-resolution evidence.
    """
    _need(tool_context.state, "extraction", "rules have not been extracted")
    return stages.stage_map(tool_context.state)


# GraphRAG ---------------------------------------------------------------------
@_guarded
def traverse_blast_radius(as_of: str, tool_context: ToolContext) -> dict:
    """Walk Rule -> Product -> Process/Control -> Team edges valid at a business date to find the structural impact.

    Args:
        as_of: ISO date for bitemporal traversal; empty string for the analysis reference date.
    """
    _need(tool_context.state, "mappings", "rules are not mapped to the graph yet")
    with GRAPH_TRAVERSAL_LOCK:  # parallel runs share the graph store's query log
        return stages.stage_blast_radius(tool_context.state, as_of or None)


@_guarded
def run_graph_query(gql: str, tool_context: ToolContext) -> dict:
    """Run a read-only GQL query on Spanner Graph 'BankArchitecture'. Must filter on @tenant_id; max 200 rows.

    Args:
        gql: A query starting with 'GRAPH BankArchitecture' using MATCH ... RETURN.
    """
    safe = validate_gql(gql)
    rows = get_graph_store().run_gql(safe, {})
    return {"executed_gql": safe, "rows": rows[:200]}


# Precedent ---------------------------------------------------------------------
@_guarded
def recall_precedents(tool_context: ToolContext) -> dict:
    """Look up earlier impact reports for this policy and remediation actions still awaiting approval."""
    _need(tool_context.state, "extraction", "rules have not been extracted")
    return stages.stage_precedents(tool_context.state)


# Data analyst ---------------------------------------------------------------------
@_guarded
def profile_impacted_portfolio(tool_context: ToolContext) -> dict:
    """Aggregate loan counts and exposure for products in the blast radius (guarded, read-only SQL)."""
    _need(tool_context.state, "blast_radius", "traverse_blast_radius has not run")
    return stages.stage_profile(tool_context.state)


@_guarded
def describe_bank_data(tool_context: ToolContext) -> dict:
    """Catalogue of every queryable sanitized view: typed columns, descriptions, live row counts, aggregate hints.

    Call this before writing SQL. Names, contacts, PAN, Aadhaar and account numbers are in no view.
    """
    return SecureEngine().describe_bank_data()


@_guarded
def run_safe_sql(sql: str, intent: str, tool_context: ToolContext, row_level: bool = False) -> dict:
    """Run a read-only SELECT over any sanitized vw_* view (see describe_bank_data for the list and columns).

    The query is AST-validated: one SELECT, views only, no PII columns, no SELECT *. With row_level=false (default for
    summaries) it must be an aggregate (COUNT/SUM/AVG/MIN/MAX with GROUP BY) and returns at most 100 rows. With
    row_level=true it may return per-loan or per-customer rows keyed by the opaque loan_ref / customer_ref (at most 200).

    Args:
        sql: PostgreSQL-dialect SELECT.
        intent: One sentence on what the query is for (recorded in the audit log).
        row_level: true to return individual rows keyed by opaque refs; false for aggregates only.
    """
    actor = getattr(tool_context, "agent_name", None) or "data_analyst_agent"
    return SecureEngine().run_safe_sql(sql, intent, actor=actor, row_level=bool(row_level))


# Evaluator ---------------------------------------------------------------------
@_guarded
def evaluate_compliance(tool_context: ToolContext) -> dict:
    """Deterministically evaluate every rule check against operational data; returns counts, penalties, worklist ids."""
    _need(tool_context.state, "blast_radius", "traverse_blast_radius has not run")
    return stages.stage_evaluate(tool_context.state)


@_guarded
def fetch_anonymized_samples(worklist_id: str, sample_size: int, tool_context: ToolContext) -> dict:
    """Fetch up to 5 example records from a worklist with session-scoped tokens instead of identifiers.

    Args:
        worklist_id: worklist id returned by evaluate_compliance.
        sample_size: number of samples, at most 5.
    """
    return SecureEngine().fetch_anonymized_samples(worklist_id, sample_size, tool_context.session.id)


# Orchestrator ---------------------------------------------------------------------
@_guarded
def trigger_compliance_workflow(workflow_id: str, rule_id: str, worklist_id: str, generated_notice_text: str,
                                tool_context: ToolContext) -> dict:
    """Propose an internal bank workflow for a worklist. It is queued for human approval, never executed directly.

    Args:
        workflow_id: SEND_BORROWER_NOTICE, BRANCH_MANAGER_ALERT, PRODUCT_CONTROL_CHANGE or COMPENSATION_PAYOUT_REVIEW.
        rule_id: rule the action remediates.
        worklist_id: worklist id from evaluate_compliance (empty string if none).
        generated_notice_text: notice template; use {{CUSTOMER_NAME}}, {{LOAN_REF}}, {{BRANCH}}, {{DELAY_DAYS}},
            {{MANAGER_NAME}} or {{COUNT}} placeholders instead of any personal data.
    """
    return stages.stage_propose(tool_context.state, workflow_id, rule_id, worklist_id or None, generated_notice_text)


@_guarded
def finalize_impact_report(narrative: str, tool_context: ToolContext) -> dict:
    """Persist the impact report with your executive narrative; adds deterministic actions for any uncovered gap.

    Args:
        narrative: Markdown executive summary: what changed, blast radius, gaps with numbers, recommended actions.
    """
    _need(tool_context.state, "results", "evaluate_compliance has not run")
    return stages.stage_finalize(tool_context.state, narrative=narrative, mode="agentic")
