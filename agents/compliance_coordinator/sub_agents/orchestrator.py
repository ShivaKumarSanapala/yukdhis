from google.adk.agents import LlmAgent

from ..tools import finalize_impact_report, trigger_compliance_workflow
from ._common import llm_options, with_state


def _orchestrator_context(state) -> str:
    rules = (state.get("extraction") or {}).get("rules") or []
    findings = state.get("analytical_findings") or []
    lines = [f"Rules mapped: {len(rules)}. Analytical findings recorded: {len(findings)}."]
    for f in findings:
        lines.append(f"- {f.get('objective')}: {f.get('summary')} ({f.get('row_count')} result rows)")
    return "\n".join(lines)


orchestrator_agent = LlmAgent(
    name="orchestrator_agent",
    description="Compiles the impact report and proposes remediation workflows for human approval.",
    instruction=with_state(
        "You are the Orchestrator. Using the Reader, Mapper, GraphRAG, Precedent, Data Analyst and Evaluator findings "
        "above, propose remediation with trigger_compliance_workflow for each GAP that has a worklist: borrower notices "
        "for record-level gaps, branch-manager alerts and compensation review for deadline breaches, product control "
        "changes for control gaps. Draft clear, regulator-grade notice text using only the placeholders {{CUSTOMER_NAME}}, "
        "{{LOAN_REF}}, {{BRANCH}}, {{DELAY_DAYS}}, {{MANAGER_NAME}}, {{COUNT}}. Skip workflows already pending per the "
        "Precedent analyst. Then call finalize_impact_report with a markdown executive summary: what changed, blast "
        "radius, gaps with exact numbers, penalty exposure, actions awaiting approval. End by giving the report id."
        "\n\nWork plan, in this order and without revisiting it: (1) decide once which gaps already have a pending action "
        "per the Precedent analyst; (2) call trigger_compliance_workflow once for each remaining gap, all in the same "
        "turn; (3) call finalize_impact_report exactly once. You have only these two tools. There is no code interpreter "
        "or python tool: do not call one, and do not recompute anything, because every number you need is already in the "
        "findings above. Do not call finalize_impact_report before you have decided the actions, and never stop without "
        "calling it.\n\n"
        "Analytical findings: the Data Analyst may have recorded SQL findings (listed below). Include them in the executive "
        "summary under '### Analytical insights' with their numbers, and never restate a number that is not in a finding or "
        "an earlier result. When no rule was mapped (rules mapped: 0) there is nothing to remediate: do not call "
        "trigger_compliance_workflow, and say in the summary that the report shows analytical insights from the circular's "
        "text and the portfolio data, not a compliance verdict. Propose workflows only for GAP checks that have a worklist."
    ,
        _orchestrator_context,
    ),
    tools=[trigger_compliance_workflow, finalize_impact_report],
    output_key="orchestrator_summary",
    **llm_options(reasoning=True),
)
