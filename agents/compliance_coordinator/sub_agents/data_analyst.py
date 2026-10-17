from google.adk.agents import LlmAgent

from ..tools import describe_bank_data, profile_impacted_portfolio, run_safe_sql
from ._common import llm_options, static

data_analyst_agent = LlmAgent(
    name="data_analyst_agent",
    description="Auditor: interrogates the sanitized operational views with guarded, read-only SQL.",
    instruction=static(
        "You are the Data Analyst. Call profile_impacted_portfolio first. Then call describe_bank_data once to see every "
        "queryable view (vw_compliance_impact, vw_loan_events, vw_asset_quality, vw_kyc_status, vw_branch_directory and any "
        "pack view), its columns and its row count, and use run_safe_sql (PostgreSQL dialect, up to four queries) to size the "
        "affected segments the way an auditor would: by branch_id, product_id, segment_id, asset_classification, DPD band, "
        "exposure_class, KYC risk category or status, provision shortfall (provision_required - provision_held), security "
        "cover and restructuring. Prefer aggregates (COUNT/SUM/AVG/MIN/MAX with GROUP BY). Use row_level=true only to "
        "drill into a handful of exceptions; rows are keyed by the opaque loan_ref / customer_ref and you can quote those "
        "refs. Names, contact details, PAN, Aadhaar and account numbers are not in any view and you cannot get them. If a "
        "query is rejected, read the reason and rewrite it once. A view with onboarded=false has no data: say so rather "
        "than guessing. Summarise the portfolio shape in numbers and state which query each number came from."
    ),
    tools=[profile_impacted_portfolio, describe_bank_data, run_safe_sql],
    output_key="analyst_summary",
    **llm_options(),
)
