from google.adk.agents import LlmAgent

from ..tools import finalize_impact_report, trigger_compliance_workflow
from ._common import llm_options, static

orchestrator_agent = LlmAgent(
    name="orchestrator_agent",
    description="Compiles the impact report and proposes remediation workflows for human approval.",
    instruction=static(
        "You are the Orchestrator. Using the Reader, Mapper, GraphRAG, Precedent, Data Analyst and Evaluator findings "
        "above, propose remediation with trigger_compliance_workflow for each GAP that has a worklist: borrower notices "
        "for record-level gaps, branch-manager alerts and compensation review for deadline breaches, product control "
        "changes for control gaps. Draft clear, regulator-grade notice text using only the placeholders {{CUSTOMER_NAME}}, "
        "{{LOAN_REF}}, {{BRANCH}}, {{DELAY_DAYS}}, {{MANAGER_NAME}}, {{COUNT}}. Skip workflows already pending per the "
        "Precedent analyst. Then call finalize_impact_report with a markdown executive summary: what changed, blast "
        "radius, gaps with exact numbers, penalty exposure, actions awaiting approval. End by giving the report id."
    ),
    tools=[trigger_compliance_workflow, finalize_impact_report],
    output_key="orchestrator_summary",
    **llm_options(reasoning=True),
)
