# Showcase circulars for an evaluator

Drop any file from `data/circulars/` into the chat (they are also offered as samples), or paste the RBI link. Reference date
17 Oct 2026. All bank data is synthetic (`docs/evals/bank-baseline.md`); expected numbers below are exact and covered by tests.

| # | Circular (file) | What it shows | What to expect |
| --- | --- | --- | --- |
| 1 | **RBI/2025-26/47 Lending Against Gold and Silver Collateral Directions, 2025** (`rbi_directions_2025_06_06_lending_against_gold_silver.md`, full RBI text, 33k characters) | A long binding Direction with limits that the strict rule checks cannot all test: the analytical objectives path (SQL findings, "Analytical insights") plus the circular text search | Gold loan book: 330 active loans. Tiered LTV caps (85% up to ₹2.5 lakh, 80% up to ₹5 lakh, 75% above): **11 loans exceed their cap** (7 standard, 4 substandard, ₹54.9 lakh outstanding); **4 are above 90% LTV**. Ask the assistant for the LTV distribution and for what the Directions say about auctions or release of collateral |
| 2 | **RBI/2025-26/64 Pre-payment Charges on Loans Directions, 2025** (`rbi_directions_2025_07_02_prepayment_charges.md`, effective 1 Jan 2026) | A prohibition checked against event data | Event `PREPAYMENT_CHARGE_LEVIED` on closed floating-rate retail loans: **9 after the effective date (violations) and 6 before it (allowed)**; the effective date matters |
| 3 | **RBI/2023-24/60 Release of property documents** (`rbi_2023_09_13_release_of_property_documents.md`) | Deadline rule with a statutory penalty, template of record cross-checked against Gemini | 12 violations, 7 at Koramangala; penalty ₹5,000 per day of delay |
| 4 | **RBI/2023-24/55 Reset of floating interest rate** (`rbi_2023_08_18_floating_rate_reset_emi_loans.md`) | Record flags and control gaps on 1,410 floating EMI loans | 42 unnotified, 9 without EMI or tenor choice, 18 without a statement; one product lost its fixed-rate switch |
| 5 | **IRACP Directions, as updated 1 Jul 2026** (`rbi_ecl_2025_11_28_iracp_directions_v2026_07_01.md`) | Large master direction against provisioning and classification data | 14 accounts over 90 days past due still standard, 6 SMA mis-tags, 16 NPA provision shortfalls (₹41.7 lakh), 5 re-restructured accounts on accrual |
| 6 | **ACPIR (ECL) Directions, final, effective 1 Apr 2027** (`rbi_ecl_2026_04_27_acpir_directions_final.md`) | Readiness: rules that take effect later and need data the bank does not have | UPCOMING and "not machine-checkable" obligations; no PD, LGD or EAD exist, so the report is never COMPLIANT |
| 7 | **Draft Directions and press releases** (`rbi_ecl_2025_10_07_draft_directions_scb.md`, `..._press_release_draft_directions.md`) | The document-type gate | Refused as non-binding with the reason; no obligations are invented |

Suggested order for a ten-minute demo: 7 (the gate), 3 (a clean deadline rule), 1 (analytical insights on a circular with
limits), 2 (event data), 6 (what the bank is not ready for).

## Reproducing the numbers

```sql
-- 1: LTV distribution of the active gold book
SELECT CASE WHEN CAST(outstanding_balance AS DOUBLE PRECISION) / NULLIF(security_value, 0) > 0.9 THEN 'above 90%'
            WHEN CAST(outstanding_balance AS DOUBLE PRECISION) / NULLIF(security_value, 0) > 0.8 THEN '80% to 90%'
            ELSE 'up to 80%' END AS ltv_band, COUNT(loan_ref) AS loans, SUM(outstanding_balance) AS outstanding
FROM vw_asset_quality WHERE product_id = 'product:gold-loan'
GROUP BY CASE WHEN CAST(outstanding_balance AS DOUBLE PRECISION) / NULLIF(security_value, 0) > 0.9 THEN 'above 90%'
              WHEN CAST(outstanding_balance AS DOUBLE PRECISION) / NULLIF(security_value, 0) > 0.8 THEN '80% to 90%'
              ELSE 'up to 80%' END;

-- 2: pre-payment charges by product and period
SELECT product_id, COUNT(loan_ref) AS charges FROM vw_loan_events
WHERE event_type = 'PREPAYMENT_CHARGE_LEVIED' AND occurred_on >= '2026-01-01' GROUP BY product_id;
```

## How the seed was added

`scripts/generate_synthetic_bank.py` has a new `plant_prepayment_charges` step on its own random stream, so no other loan,
customer or event changed (the loans file is byte-identical; only `loan_events.csv` gained 15 rows and was renumbered, and
`manifest.json` gained `planted.rbi_2025_26_64_prepayment_charges`). The gold LTV breaches were already in the generated book. Both
new circulars are the official RBI text fetched from rbi.org.in (notification ids 12859 and 12878); the older samples are
summaries. Files named `rbi_<digit>…` are treated by the tests as approved templates of record, which is why these two use `rbi_directions_…`.
