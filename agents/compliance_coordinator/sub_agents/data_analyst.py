from google.adk.agents import LlmAgent

from ..tools import profile_impacted_portfolio, run_safe_sql
from ._common import llm_options, static

data_analyst_agent = LlmAgent(
    name="data_analyst_agent",
    description="Auditor: interrogates sanitized operational views with guarded, aggregate-only SQL.",
    instruction=static(
        "You are the Data Analyst. Call profile_impacted_portfolio first. You may then write up to two additional "
        "aggregate SELECT queries with run_safe_sql over vw_compliance_impact or vw_loan_events (PostgreSQL dialect, "
        "COUNT/SUM with GROUP BY, never row-level identifiers) to size the affected segments, e.g. by branch_id or status. "
        "If a query is rejected, read the reason and rewrite it once. Summarise the portfolio shape in numbers."
    ),
    tools=[profile_impacted_portfolio, run_safe_sql],
    output_key="analyst_summary",
    **llm_options(),
)
