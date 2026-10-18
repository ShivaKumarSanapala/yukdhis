from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types

from ..tools import extract_and_validate_rules, parse_regulatory_document
from ._common import llm_options, static


def skip_when_rules_approved(callback_context: CallbackContext) -> types.Content | None:
    """Human review: the API extracted the rules before the pause and put the approved ones in session state.

    Returning content skips the Reader (no re-extraction); the SequentialAgent continues with the Mapper.
    """
    state = callback_context.state
    if not state.get("rules_approved") or "extraction" not in state:
        return None
    rules = state["extraction"].get("rules", [])
    lines = [f"- {r.get('title')} ({r.get('modality', 'MUST')}, effective {r.get('effective_date')})" for r in rules]
    text = (f"A compliance officer reviewed and approved {len(rules)} extracted rule(s) before analysis, so extraction "
            "was not repeated:\n" + "\n".join(lines))
    return types.Content(role="model", parts=[types.Part(text=text)])


reader_agent = LlmAgent(
    name="reader_agent",
    description="Parses regulatory circulars with Document AI and extracts validated, executable rules.",
    instruction=static(
        "You are the Reader. Call parse_regulatory_document, then extract_and_validate_rules. Summarise the policy "
        "(issuer, reference, effective date) and list each rule with its checks and verbatim citation. Call out "
        "validation warnings and the extraction method (template of record vs Gemini) so reviewers know what to verify. Also "
        "list the analytical_objectives that were suggested for the data analysis. A circular with no executable rule can "
        "still have objectives; say so instead of calling the document empty."
    ),
    tools=[parse_regulatory_document, extract_and_validate_rules],
    output_key="reader_summary",
    before_agent_callback=skip_when_rules_approved,
    **llm_options(reasoning=True),
)
