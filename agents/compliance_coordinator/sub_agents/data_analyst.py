from google.adk.agents import LlmAgent

from ..tools import (describe_bank_data, get_circular_text, profile_impacted_portfolio, record_analytical_finding, run_safe_sql,
                     search_circulars)
from ._common import llm_options, with_state

CONTEXT_CHARS = 20_000   # the rest is available through get_circular_text


def _circular_context(state) -> str:
    objectives = state.get("analytical_objectives") or []
    text = (state.get("full_text_context") or "")[:CONTEXT_CHARS]
    parts = []
    if objectives:
        parts.append("Analytical objectives suggested by the Reader (each is a candidate analysis):\n"
                     + "\n".join(f"{i}. {o}" for i, o in enumerate(objectives, 1)))
    else:
        parts.append("The Reader suggested no analytical objectives: derive up to three from the circular text below if it "
                     "contains a metric, limit or threshold the portfolio data can measure.")
    rules = (state.get("extraction") or {}).get("rules") or []
    parts.append(f"Rules extracted and mapped: {len(rules)}." + ("" if rules else
                 " There are none, so the blast radius is empty; the analytical objectives are the whole analysis."))
    if text:
        parts.append("Circular text (cleaned; call get_circular_text for the rest):\n<circular>\n" + text + "\n</circular>")
    return "\n\n".join(parts)


data_analyst_agent = LlmAgent(
    name="data_analyst_agent",
    description="Auditor: interrogates the sanitized operational views with guarded, read-only SQL, "
                "for the mapped rules and for the circular's analytical objectives.",
    instruction=with_state(
        "You are the Data Analyst. Call profile_impacted_portfolio first, then describe_bank_data once to see every "
        "queryable view (vw_compliance_impact, vw_loan_events, vw_asset_quality, vw_kyc_status, vw_branch_directory and any "
        "pack view), its columns and its row count.\n"
        "Part 1, the blast radius: use run_safe_sql (PostgreSQL dialect, up to four queries) to size the affected segments the "
        "way an auditor would: by branch_id, product_id, segment_id, asset_classification, DPD band, exposure_class, KYC risk "
        "category or status, provision shortfall (provision_required - provision_held), security cover and restructuring. "
        "Skip this part when no product is in scope.\n"
        "Part 2, the analytical objectives (do this EVEN WHEN no rule was mapped or the blast radius is empty): for each "
        "objective below that the views can measure, write one aggregate query and record it with "
        "record_analytical_finding(objective, summary, sql). Examples: loan-to-value is "
        "CAST(outstanding_balance AS DOUBLE PRECISION) / NULLIF(security_value, 0) on vw_asset_quality (filter product_id for "
        "the product in the circular, bucket the ratio with CASE and COUNT/SUM per bucket, and count the loans above the "
        "circular's limit; the guard wants the CASE expression repeated in GROUP BY, not an ordinal like GROUP BY 1); shortfalls compare provision_required with provision_held; ageing uses days_past_due. The tool runs "
        "the query and stores its real rows for the report, so the summary must quote only numbers that query returned. "
        "Record at most five findings, one per objective, preferring a distribution table over a single count. If an "
        "objective cannot be measured (the data is not onboarded), do not record a finding for it; say so in your answer.\n"
        "Use search_circulars or get_circular_text if you need wording beyond the excerpt. Use row_level=true only to drill "
        "into a handful of exceptions; rows are keyed by the opaque loan_ref / customer_ref. Names, contact details, PAN, "
        "Aadhaar and account numbers are not in any view and you cannot get them. If a query is rejected, read the reason "
        "and rewrite it once. A view with onboarded=false has no data: say so rather than guessing. Summarise the "
        "portfolio shape in numbers and state which query each number came from.",
        _circular_context,
    ),
    tools=[profile_impacted_portfolio, describe_bank_data, run_safe_sql, record_analytical_finding, get_circular_text,
           search_circulars],
    output_key="analyst_summary",
    **llm_options(reasoning=True),
)
