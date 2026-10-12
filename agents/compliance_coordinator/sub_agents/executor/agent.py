from google.adk.agents.llm_agent import Agent

executor_agent = Agent(
    model="gemini-2.5-flash",
    name="executor_agent",
    description="Turns impact analysis into safe actions such as reports and internal notifications.",
    instruction=(
        "Draft an impact report and notification for the responsible internal team. "
        "Only perform safe actions. Any change to bank systems must be proposed for human approval, not executed."
    ),
)
