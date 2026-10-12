from google.adk.agents.llm_agent import Agent

parser_agent = Agent(
    model="gemini-2.5-flash",
    name="parser_agent",
    description="Parses RBI circulars and policy documents, stores versions in the RAG store, and reports what changed.",
    instruction=(
        "Extract the rules, effective dates, affected entities and constraints from the document. "
        "Compare against prior regulations in the RAG store and return structured JSON listing "
        "added, removed and modified rules."
    ),
)
