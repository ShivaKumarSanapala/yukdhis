from google.adk.agents import LlmAgent

from ..tools import describe_bank_data, evaluate_compliance, fetch_anonymized_samples, run_safe_sql
from ._common import llm_options, static

evaluator_agent = LlmAgent(
    name="evaluator_agent",
    description="Compares operational data against extracted rules to flag exact violations and penalty exposure.",
    instruction=static(
        "You are the Evaluator. Call evaluate_compliance. For the most severe gap you may call fetch_anonymized_samples "
        "(max 3 samples) to understand a typical failing case. State each rule's result: status, violations out of "
        "in-scope population, statutory penalty, and the worst-affected branches. Keep worklist ids for the Orchestrator. If a "
        "result looks surprising (all or none of the population failing) you may cross-check it with at most two read-only "
        "run_safe_sql aggregates (call describe_bank_data first for the columns) and say whether they agree. With no rules, "
        "evaluate_compliance returns no results: say so, do not invent any."
    ),
    tools=[evaluate_compliance, fetch_anonymized_samples, describe_bank_data, run_safe_sql],
    output_key="evaluator_summary",
    **llm_options(reasoning=True),
)
