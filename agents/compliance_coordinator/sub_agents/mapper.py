from google.adk.agents import LlmAgent

from ..tools import get_graph_ontology, map_rules_to_graph
from ._common import llm_options, static

mapper_agent = LlmAgent(
    name="mapper_agent",
    description="Maps extracted rules onto the bank's BIAN knowledge graph with bitemporal edges.",
    instruction=static(
        "You are the Mapper. Call get_graph_ontology('All') to learn the schema, then map_rules_to_graph. Explain how "
        "policy wording resolved to canonical products, processes and segments (quote the resolution scores and methods), "
        "and flag any unresolved term or newly created control as needing review."
    ),
    tools=[get_graph_ontology, map_rules_to_graph],
    output_key="mapper_summary",
    **llm_options(),
)
