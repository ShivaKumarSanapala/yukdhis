from google.adk.agents import LlmAgent

from ..tools import evaluate_compliance, fetch_anonymized_samples
from ._common import llm_options, static

evaluator_agent = LlmAgent(
    name="evaluator_agent",
    description="Compares operational data against extracted rules to flag exact violations and penalty exposure.",
    instruction=static(
        "You are the Evaluator. Call evaluate_compliance. For the most severe gap you may call fetch_anonymized_samples "
        "(max 3 samples) to understand a typical failing case. State each rule's result: status, violations out of "
        "in-scope population, statutory penalty, and the worst-affected branches. Keep worklist ids for the Orchestrator."
    ),
    tools=[evaluate_compliance, fetch_anonymized_samples],
    output_key="evaluator_summary",
    **llm_options(reasoning=True),
)
