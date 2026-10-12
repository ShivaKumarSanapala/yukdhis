from google.adk.agents.llm_agent import Agent

from .service import ImpactAnalyser
from ..mapper.service import PolicyMapper
from ingestion.circular_parser import parse_floating_rate_reset_circular


def analyse_circular_impact(circular_path: str) -> dict:
    """Read-only impact analysis for the circular using mapper and bank repositories."""
    mappings = [PolicyMapper().map_rule(rule) for rule in parse_floating_rate_reset_circular(circular_path)]
    return ImpactAnalyser().analyse(mappings).to_dict()

analyzer_agent = Agent(
    model="gemini-2.5-flash",
    name="analyzer_agent",
    description="Maps regulatory changes to bank policies, products and processes, then queries bank data for impacted customers.",
    instruction=(
        "Accept the mapper's graph evidence, then use analyse_circular_impact for the deterministic read-only "
        "join to operational loan data. Never modify bank data. Return the supplied compliance gaps, blast radius, "
        "and limitations; do not claim that a remediation was executed."
    ),
    tools=[analyse_circular_impact],
)
