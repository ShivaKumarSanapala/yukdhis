from google.adk.agents import LlmAgent

from ..tools import extract_and_validate_rules, parse_regulatory_document
from ._common import llm_options, static

reader_agent = LlmAgent(
    name="reader_agent",
    description="Parses regulatory circulars with Document AI and extracts validated, executable rules.",
    instruction=static(
        "You are the Reader. Call parse_regulatory_document, then extract_and_validate_rules. Summarise the policy "
        "(issuer, reference, effective date) and list each rule with its checks and verbatim citation. Call out "
        "validation warnings and the extraction method (template of record vs Gemini) so reviewers know what to verify."
    ),
    tools=[parse_regulatory_document, extract_and_validate_rules],
    output_key="reader_summary",
    **llm_options(reasoning=True),
)
