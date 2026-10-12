from google.adk.agents.llm_agent import Agent

from .sub_agents.analyzer.agent import analyzer_agent
from .sub_agents.executor.agent import executor_agent
from .sub_agents.mapper.agent import mapper_agent
from .sub_agents.parser.agent import parser_agent

root_agent = Agent(
    model="gemini-2.5-flash",
    name="compliance_coordinator",
    description="Coordinates regulatory circular ingestion, impact analysis, and follow-up actions for the bank.",
    instruction=(
        "You coordinate the bank's regulatory compliance workflow. "
        "Send new circulars or uploaded documents to parser_agent. "
        "Hand parsed policy changes to mapper_agent first. The mapper identifies relevant bank products, processes, "
        "and controls in the knowledge graph. Hand its mapping evidence to analyzer_agent, which computes the "
        "read-only blast radius from bank data. Send the analysis to executor_agent to draft notifications or reports. "
        "Answer user Q&A directly when no delegation is needed."
    ),
    sub_agents=[parser_agent, mapper_agent, analyzer_agent, executor_agent],
)
