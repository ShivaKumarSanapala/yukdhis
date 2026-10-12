from google.adk.agents.llm_agent import Agent

analyzer_agent = Agent(
    model="gemini-2.5-flash",
    name="analyzer_agent",
    description="Maps regulatory changes to bank policies, products and processes, then queries bank data for impacted customers.",
    instruction=(
        "Use the knowledge graph to find internal policies, products and processes touched by the change. "
        "Use read-only bank tools to count and list impacted customers. Never modify bank data. "
        "Return the compliance gaps and blast radius."
    ),
)
