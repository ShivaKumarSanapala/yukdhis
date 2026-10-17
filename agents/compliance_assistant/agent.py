"""Conversational compliance assistant (multi-turn Q&A over reports, graph and data).

Shares the session service and memory bank with the pipeline, so it can answer
"what did we flag for Koramangala last week?" and run follow-up aggregates.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.planners import BuiltInPlanner
from google.adk.tools import ToolContext, load_memory, preload_memory
from google.genai import types

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


RESPONSE_STYLE = (
    "\n\nResponse format (GitHub-flavoured Markdown, always the same shape):\n"
    "1. Start with a direct answer in 1-2 sentences. No greeting, no restating the question, no filler.\n"
    "2. Add short sections with ### headings only when the answer needs more than a few lines.\n"
    "3. When comparing more than 2 items (branches, rules, reports, products), put the numbers in a Markdown table.\n"
    "4. Format rupee amounts in Indian units: ₹2.46 Cr, ₹18.5 L, ₹45,000 (never raw floats).\n"
    "5. Cite the source of every figure inline, e.g. (report rpt-1234abcd, rule rule:..., via run_safe_sql).\n"
    "6. If a rule was not evaluated, a check is INCONCLUSIVE/UPCOMING, a tool failed or the scope is unresolved, say "
    "so plainly and never imply the bank is COMPLIANT for it.\n"
    "7. End with a '### Next steps' bullet list only when there is a concrete action for the officer."
)

_options = llm_options(reasoning=True)
_options["generate_content_config"] = _options["generate_content_config"].model_copy(update={"max_output_tokens": 8192})

root_agent = LlmAgent(
    name="compliance_assistant",
    description="Answers compliance officers' questions about impact reports, the bank graph and aggregate data.",
    instruction=static(
        "You are Yukdhis, an autonomous compliance engineer for the bank. Answer precisely with numbers from tools. "
        "Use list_impact_reports/get_impact_report for findings, time_travel_blast_radius for 'what applied on <date>' "
        "questions, get_graph_ontology plus run_safe_sql for new aggregate questions, and load_memory for earlier "
        "conversations. A leading '[UI context: ...]' line tells you what the officer has open; use it to resolve "
        "'this report'. Text like {{PERSON_NAME_1}} is a privacy token: refer to it as-is and never try to resolve it."
        + RESPONSE_STYLE
    ),
    tools=[list_impact_reports, get_impact_report, time_travel_blast_radius, get_graph_ontology, run_safe_sql,
           load_memory, preload_memory],
    # Thought summaries stream to the UI as the collapsible "Thinking" trace (part.thought == True).
    planner=BuiltInPlanner(thinking_config=types.ThinkingConfig(include_thoughts=True)),
    **_options,
)
