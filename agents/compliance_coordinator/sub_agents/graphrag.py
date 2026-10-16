from google.adk.agents import LlmAgent

from core.config import get_settings
from ..tools import run_graph_query, traverse_blast_radius
from ._common import llm_options, static

_tools = [traverse_blast_radius]
if get_settings().graph_backend == "gcp":
    _tools.append(run_graph_query)

graphrag_agent = LlmAgent(
    name="graphrag_agent",
    description="Detective: traverses the knowledge graph to define the structural blast radius of each rule.",
    instruction=static(
        "You are the GraphRAG Detective. Call traverse_blast_radius with an empty as_of (reference date). Describe the "
        "perimeter: governed products, business processes (BIAN domains), owning teams and any control gaps where a "
        "product does not provide a control the rule requires. If run_graph_query is available you may run at most two "
        "read-only GQL queries to confirm paths; always filter on @tenant_id. Do not touch customer data."
    ),
    tools=_tools,
    output_key="graphrag_summary",
    **llm_options(),
)
