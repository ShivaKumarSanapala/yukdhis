from google.adk.agents.llm_agent import Agent

from .service import PolicyMapper
from ingestion.circular_parser import parse_floating_rate_reset_circular


def map_circular_to_bank_architecture(circular_path: str) -> dict:
    """Parse a supported normalized circular and return auditable graph mappings."""
    mapper = PolicyMapper()
    return {"mappings": [mapper.map_rule(rule).to_dict() for rule in parse_floating_rate_reset_circular(circular_path)]}


mapper_agent = Agent(
    model="gemini-2.5-flash",
    name="mapper_agent",
    description="Maps extracted regulatory requirements to bank product, process and control topology.",
    instruction=(
        "Use map_circular_to_bank_architecture for a normalized circular. Return its paths, affected products, "
        "and missing control capabilities exactly as supplied. Do not invent graph relationships. Hand the mapping "
        "to analyzer_agent for customer/loan impact analysis."
    ),
    tools=[map_circular_to_bank_architecture],
)
