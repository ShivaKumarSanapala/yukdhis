"""Autonomous Compliance Engineer - ADK root agent.

    reader -> mapper -> [graphrag || precedent] -> data_analyst -> evaluator -> orchestrator

A SequentialAgent enforces the hand-off order; the ParallelAgent runs the
structural graph traversal and the long-term-memory precedent search at the
same time.  Agents share structured results through session state (persisted
by DatabaseSessionService on Cloud SQL), not by parsing each other's prose.

Run locally:  adk web agents      (or)      adk run agents/compliance_coordinator
"""

from google.adk.agents import ParallelAgent, SequentialAgent

from .sub_agents import (data_analyst_agent, evaluator_agent, graphrag_agent, mapper_agent, orchestrator_agent,
                         precedent_agent, reader_agent)

impact_investigation = ParallelAgent(
    name="impact_investigation",
    description="Runs the GraphRAG blast-radius traversal and the precedent memory search concurrently.",
    sub_agents=[graphrag_agent, precedent_agent],
)

root_agent = SequentialAgent(
    name="compliance_pipeline",
    description="Turns a regulatory circular into an auditable impact report with remediation awaiting approval.",
    sub_agents=[reader_agent, mapper_agent, impact_investigation, data_analyst_agent, evaluator_agent, orchestrator_agent],
)
