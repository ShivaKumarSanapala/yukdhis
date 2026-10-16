"""Conversational compliance assistant (multi-turn Q&A over reports, graph and data).

Shares the session service and memory bank with the pipeline, so it can answer
"what did we flag for Koramangala last week?" and run follow-up aggregates.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.tools import ToolContext, load_memory, preload_memory

from agents.compliance_coordinator.sub_agents._common import llm_options, static
from agents.compliance_coordinator.tools import get_graph_ontology, run_safe_sql
from core.app_store import get_app_store
from knowledge_graph.store import get_graph_store
from knowledge_graph.traversal import blast_radius


def list_impact_reports(tool_context: ToolContext) -> dict:
    """List recent impact reports with their headline numbers."""
    return {"reports": get_app_store().list_reports(limit=10)}


def get_impact_report(report_id: str, tool_context: ToolContext) -> dict:
    """Return a report's summary, per-check results and pending actions (no row-level data).

    Args:
        report_id: id such as rpt-1234abcd.
    """
    report = get_app_store().get_report(report_id)
    if not report:
        return {"status": "error", "message": f"Unknown report {report_id}"}
    return {
        "policy": report["policy"], "as_of": report["as_of"], "summary": report["summary"], "narrative": report["narrative"],
        "results": [{k: r[k] for k in ("rule_id", "description", "status", "violations", "in_scope", "penalty_inr", "by_branch")}
                    for r in report["results"]],
        "actions": [{k: a[k] for k in ("action_id", "workflow_id", "rule_id", "target_count", "status")} for a in report["actions"]],
    }


def time_travel_blast_radius(rule_id: str, as_of: str, tool_context: ToolContext) -> dict:
    """Recompute a rule's blast radius using graph edges valid on a past or future business date.

    Args:
        rule_id: rule node id, e.g. rule:DOR.MCS.REC.32/01.01.003/2023-24:fixed-rate-choice
        as_of: ISO date, e.g. 2024-01-15
    """
    radius = blast_radius(get_graph_store(), rule_id, as_of)
    return {"as_of": as_of, "products": [p["name"] for p in radius.products], "control_gaps": radius.control_gaps}


root_agent = LlmAgent(
    name="compliance_assistant",
    description="Answers compliance officers' questions about impact reports, the bank graph and aggregate data.",
    instruction=static(
        "You are Yukdhis, an autonomous compliance engineer for the bank. Answer precisely with numbers from tools. "
        "Use list_impact_reports/get_impact_report for findings, time_travel_blast_radius for 'what applied on <date>' "
        "questions, get_graph_ontology plus run_safe_sql for new aggregate questions, and load_memory for earlier "
        "conversations. Text like {{PERSON_NAME_1}} is a privacy token: refer to it as-is and never try to resolve it."
    ),
    tools=[list_impact_reports, get_impact_report, time_travel_blast_radius, get_graph_ontology, run_safe_sql,
           load_memory, preload_memory],
    **llm_options(reasoning=True),
)
