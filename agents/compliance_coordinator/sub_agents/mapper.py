from google.adk.agents import LlmAgent

from ..tools import get_graph_ontology, map_rules_to_graph, search_circulars
from ._common import llm_options, static

mapper_agent = LlmAgent(
    name="mapper_agent",
    description="Maps extracted rules onto the bank's BIAN knowledge graph with bitemporal edges.",
    instruction=static(
        "You are the Mapper. Call get_graph_ontology('All') to learn the schema, then map_rules_to_graph. Explain how "
        "policy wording resolved to canonical products, processes and segments (quote the resolution scores and methods), "
        "say how each required capability was canonicalised onto a control (control_decisions: decision, method, "
        "confidence), and flag any unresolved term or control with needs_review as needing review. If the Reader found no rules, "
        "say so and still call map_rules_to_graph once (it returns nothing); the analytical objectives continue without rules. "
        "Use search_circulars only to check how a term is used in the circular text when a resolution is ambiguous."
    ),
    tools=[get_graph_ontology, map_rules_to_graph, search_circulars],
    output_key="mapper_summary",
    **llm_options(),
)
