from google.adk.agents import LlmAgent
from google.adk.tools import load_memory

from ..tools import recall_precedents
from ._common import llm_options, static

precedent_agent = LlmAgent(
    name="precedent_agent",
    description="Recalls earlier findings and unresolved remediation for the same policy from long-term memory.",
    instruction=static(
        "You are the Precedent analyst. Call recall_precedents, and use load_memory to search past sessions for the "
        "policy reference and affected products. Report prior runs, still-pending actions and repeat findings in two "
        "or three sentences. Say 'no precedent' if there is none."
    ),
    tools=[recall_precedents, load_memory],
    output_key="precedent_summary",
    **llm_options(),
)
