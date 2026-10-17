# Eval: RBI/2026-27/293, A.P. (DIR Series) Circular No. 27 (FERR and NDDC hedging)

| | |
| --- | --- |
| Circular | RBI/2026-27/293, A.P. (DIR Series) Circular No. 27, 12 Oct 2026, *Risk Management and Inter-Bank Dealings* |
| Run evaluated | `run-36e5ac6796e2` → report `rpt-028d4c9f2d35` (agentic, Gemini 2.5 Pro, GCP backends, as of 2026-10-17) |
| Earlier run | `run-edc8be94c0d5` → `rpt-642984991387` (same PDF, 04:40 UTC) |
| Verdict | **Fail.** The report is confidently wrong. It maps an FX-derivatives rule onto 161 retail and MSME loans and marks the bank NON_COMPLIANT. The correct output was "applicable to Treasury, which is not onboarded; here are the obligations, owners and the data needed". |

## 1. What the circular actually requires

1. **NDDC purpose restriction (para 2).** AD Category-I banks with an operating IFSC Banking Unit, and standalone PDs that are AD Category-III, could offer INR non-deliverable derivative contracts (NDDCs) to non-residents for any purpose. From now on they may offer them **only for hedging**.
2. **Foreign Exchange Risk Reserve, FERR (para 3).**
   - **Scope:** FX derivative contracts involving INR that the AD enters **to hedge the currency risk of its own assets and liabilities**, and only where the AD **buys foreign currency against INR**.
   - **Amount:** **20% of the INR-equivalent notional** of *each* contract.
   - **Form:** held **as cash in India with RBI**, maintained **daily**, until the contract terminates.
3. **Applicability (para 4).** The rules are **prospective**: they cover contracts undertaken *after* issuance, so existing contracts are not caught.
4. **Legal basis (para 5).** FEMA 1999 s.10(4) and s.11(1), and RBI Act s.45W. The circular sets no specific penalty. Contraventions by an AD fall under FEMA's general penalty provisions (s.11(3)).

## 2. Scorecard

| Dimension | Score (0–5) | Evidence |
| --- | --- | --- |
| Ingestion (Document AI) | 3 | Parsed the PDF into **1 chunk with no headings**. The title is a mix of Hindi header text and the circular number. |
| Rule extraction: obligations | **4** | Both obligations found, with verbatim citations. The FERR rule was split into three sensible checks (maintain, 20% calculation, daily cash deposit). |
| Rule extraction: conditions and parameters | 1 | Missed that the rules are prospective, the entity licence scope (AD Cat-I with IBU, AD Cat-III), the purchase-of-foreign-currency direction filter, the 20% parameter, the daily frequency and "until termination". These survive only as prose, not as executable fields. Every rule has `confidence = 1.0` despite nothing resolving. |
| Extraction stability | **0** | The same PDF gave **5 rules and 5 checks** at 04:40 and **2 rules and 4 checks** at 06:00, with different capability names (`nddc_hedging_purpose_only` vs `nddc_hedging_purpose_check`, `foreign_exchange_risk_reserve_maintenance` vs `maintain_ferr`). |
| Entity resolution and scope | **0** | All product, process and segment terms were `unresolved` (score 0.0). The mapper then **failed open**: no resolved product meant *all 7 products*, and no resolved segment meant the retail and MSME segments. This is the root-cause bug. |
| Blast radius (GraphRAG) | 0 | Seven retail and MSME loan products, no processes, owners Retail Lending, Vehicle Finance and MSME Banking. None are touched by this circular. |
| Quantitative analysis (Data Analyst) | 0 | The guarded SQL was correct and safe but answered the wrong question: loan exposure of ₹26.3 Cr by branch, closed loans included. |
| Gap evaluation (Evaluator) | 1 | Flagged "161/161 violations" for each FX control. That counts **closed** loans too, and contradicts the KPI tile "0 loans with violations". |
| Control graph integrity | 0 | **42 control gaps** (7 products × 6 controls). The earlier run's `REQUIRES` edges were never superseded, so near-duplicate controls from both runs accumulated on the same `rule_id`. |
| Remediation (Orchestrator) | 1 | `PRODUCT_CONTROL_CHANGE` tickets owned by **Retail Lending**. It should be Treasury, ALM and Finance. One was **approved and dispatched** by a human reviewer. |
| Precedent and memory | 4 | Correctly found the earlier report and 10 pending actions, and avoided proposing duplicates. |
| Privacy and safety | 3 | No PII exposure and every query was guarded. A **false positive**: report id `rpt-642984991387` matched the Aadhaar regex, so the model saw `rpt-{{INDIA_AADHAAR_1}}`. |
| Auditability | 5 | The trace, guarded SQL, GQL and validation warnings made this diagnosis possible in minutes. |

What worked: reading the circular, citing it verbatim, the safety rails, the audit trail, and the memory recall.
What didn't: everything from mapping onward. The agents passed the wrong scope downstream without questioning it.

## 3. Root causes (code-level)

| # | Cause | Where | Effect |
| --- | --- | --- | --- |
| R1 | **Fail-open scope.** `candidates = all products if nothing resolved`; segments fall back to every segment eligible for those products | `knowledge_graph/mapper.py` `map_rule` | Retail loans pulled into an FX rule |
| R2 | No **applicability gate**: nothing asks "does this bank hold the licence or business this rule targets, and do we have its data?" | pipeline | NON_COMPLIANT instead of NOT_EVALUATED |
| R3 | **Re-mapping doesn't supersede** edges absent from the new mapping, and rule ids are positional (`:1`, `:2`) | mapper | Stale and duplicate controls, 42 gaps |
| R4 | New capabilities are created verbatim instead of being **resolved against existing controls** | mapper | Control sprawl (`maintain_ferr` ≈ `foreign_exchange_risk_reserve_maintenance`) |
| R5 | **Non-deterministic extraction** with no cache by `document_id` | `ingestion/extractor.py` | 5 rules vs 2 rules for the same PDF |
| R6 | The check vocabulary has no reserve, percentage-of-notional, trade-restriction or prospective-trade-date kinds | `core/models.py`, ontology | Real obligations degrade to generic `control_required` |
| R7 | `control_required` reports loans on the product as "violations", closed loans included | `bank_mcp_server/secure_engine.py` | 161/161 noise |
| R8 | Owner routing uses the most common *product owner* | `pipeline/stages.py` `_owner_for` | Tickets go to Retail Lending |
| R9 | No critic step; LLM agents echo tool output even when the GraphRAG agent reported "no processes identified" | agent design | Confident wrong narrative |
| R10 | The Aadhaar regex has no Verhoeff checksum and fires inside identifiers | `privacy/detectors.py` | Corrupted ids in model context |
| R11 | The HITL UI allows approving actions for rules that are `PENDING_REVIEW` with unresolved scope | `web/`, `api/main.py` | A wrong ticket was dispatched |

## 4. What a bank actually does with this circular (expected answer)

**Applicability triage (day 0):**
- Is the bank an AD Category-I? Does it run an IFSC Banking Unit (GIFT City) that offers INR NDDCs?
- Does it hedge its own foreign-currency assets or liabilities (FCNR(B) deposits, ECBs or foreign-currency bonds, overseas branch funding, investments) with FX derivatives against INR?

**Impacted perimeter:**

| Area | BIAN-style service domain | Owner |
| --- | --- | --- |
| INR NDDCs offered from the IBU to non-residents | Dealer Workbench / Market Trading, Customer Offer | IBU head, Global Markets sales |
| Hedging-purpose evidence and underlying exposure for non-resident users | Customer Onboarding / KYC, Customer Agreement | Global Markets, Compliance |
| Own-book hedges where the bank buys foreign currency against INR (ALM hedges) | Position Keeping, Asset & Liability Management | Treasury / ALM desk, ALCO |
| FERR computation (20% of INR notional per contract, daily) | Market Risk / Regulatory Compliance calculation | Treasury mid-office, Market Risk |
| Daily cash deposit with RBI until the contract terminates | Treasury Administration / Reserve Management (similar to CRR operations) | Treasury back office |
| Liquidity, LCR and funding-cost impact of locked cash | Liquidity Management, Financial Accounting | ALM, Finance |
| Trade booking controls (block non-hedge NDDCs; tag hedge designation and direction) | Trading system configuration | Treasury IT |
| Reporting and audit trail to RBI | Regulatory Reporting | Regulatory reporting, Compliance |

**Quantification the report should have produced:**
1. New INR NDDC trades with non-residents since 12 Oct 2026, by purpose. Any non-hedge trade is a breach.
2. FERR required each day: Σ(INR notional × 20%) over own-book hedges where the bank buys foreign currency against INR, traded on or after 12 Oct 2026 and not yet terminated.
3. FERR shortfall each day: required minus cash held with RBI.
4. Funding cost of the locked cash: required FERR × the bank's marginal funding rate (assuming the deposit earns little or nothing).
5. P&L or pricing impact on hedge economics, and a pipeline view of new hedges to be booked.

**Remediation plan:**
- Immediate dealer advisory and a **booking block** on non-hedge NDDCs.
- Hedging-purpose declarations and evidence templates for non-resident clients, plus client notices.
- FERR engine with a daily run and EOD cash-funding workflow with RBI, with reconciliations.
- Update the ALCO and Board treasury policy, the liquidity forecast and FTP/pricing.
- Breach monitoring and RBI reporting.

There's **no statutory penalty to compute**. The risk is supervisory action under FEMA, so penalty exposure should read "not specified (FEMA s.11)", not "₹0".

## 5. Data gaps in our system

| Gap | Needed for | Add |
| --- | --- | --- |
| **No Treasury / derivatives domain** in the ontology or synthetic data | Everything in §4 | Products `fx-forward`, `fx-swap`, `fx-option`, `inr-nddc`, `cross-currency-swap`; processes from §4; teams Treasury, ALM, Market Risk, Treasury Ops, IBU |
| **Bank / legal-entity profile** (licences: AD Cat-I/II/III, IBU operating, PD status) | Applicability gate (R2) | `Entity` node with licence attributes and `HOLDS_LICENCE` edges |
| **Trade-level operational data** | Restriction and reserve checks | `vw_fx_derivative_trades`: trade_ref, trade_date, maturity / termination_date, product, currency_pair, **direction (buys/sells FCY vs INR)**, notional_fcy, notional_inr, counterparty_ref, **counterparty_residency**, **purpose (hedge / non-hedge)**, **hedge_designation (own assets / liabilities)**, booking_unit (IBU / onshore), underlying_exposure_ref |
| **Reserve balances held with RBI** | Daily FERR shortfall | `vw_rbi_reserve_balances`: date, reserve_type, balance_inr |
| **Counterparty classification** (resident / non-resident, user type) | NDDC rule | Counterparty dimension (tokenized) |
| **Regulatory corpus links** (the Master Direction on Risk Management and Inter-Bank Dealings, 2016, para 2.3(iii)) | `AMENDS` / `SUPERSEDES` lineage | Policy nodes for master directions and an `AMENDS` edge |
| **Check vocabulary** | Executable rules (R6) | New kinds: `applicability_gate`, `transaction_restriction(predicate, from_trade_date)`, `reserve_requirement(base, pct, frequency, form, holder)`, `reporting_obligation` |
| **Liquidity and funding rates** | Cost of locked cash | Funding-curve reference data |

## 6. Recommended fixes (priority order)

**P0, correctness. Wrong answers must become "I can't evaluate this":**
1. Make mapping fail closed. Unresolved product terms produce zero products and an `OUT_OF_ONTOLOGY` finding. No segment fallback when segment terms were given (R1).
2. Add an applicability gate plus an `INCONCLUSIVE` / `NOT_EVALUATED` overall status, with "data domain not onboarded" reasons (R2).
3. Make re-mapping idempotent. Use stable content-hash rule ids, supersede edges missing from the new mapping, and resolve capabilities against existing `Control` nodes before creating new ones (R3, R4).
4. Block approval of actions whose rule is `PENDING_REVIEW` or has unresolved scope, and surface the warnings beside the Approve button (R11).
5. Add a Verhoeff checksum and identifier-context guard to the Aadhaar and card detectors (R10).

**P1, quality:**
6. Cache extraction by `document_id` with a fixed seed; on re-runs, diff against the cached version instead of re-extracting (R5).
7. Extend the check vocabulary and the extraction schema with applicability conditions and numeric parameters, and calibrate confidence downward when terms don't resolve (R6).
8. Add a critic `LoopAgent` between Evaluator and Orchestrator that rejects reports when scope and rule domain disagree (R9).
9. Route owners by rule domain (Treasury, ALM, Finance, Compliance), with Compliance as the fallback (R8).
10. Report control gaps as "products missing a control (exposure)", not as violations, and exclude closed records unless the rule targets closures (R7).

**P2, coverage:** onboard the Treasury domain with synthetic FX trade data, so this circular becomes a third demo scenario with a real FERR number.

## 7. Clean-up of the live environment from this run

- One **approved and dispatched** `PRODUCT_CONTROL_CHANGE` ticket to Retail Lending should be withdrawn.
- The **12 pending actions** across both reports should be rejected.
- Rules `rule:A.P. (DIR Series) Circular No. 27:*` should be **rejected** in the UI. Rejecting ends their graph edges, which clears the 42 stale gaps.
