# Eval set: RBI Expected Credit Loss (ECL), 2023 → 2027

These RBI documents trace one regulatory change from consultation to a repeal that takes effect on 1 April 2027.
They are used to evaluate document classification, non-binding gating, effective-date handling (UPCOMING), and
amendment, supersession and repeal scenarios. Every file is in `data/circulars/` (prefix `rbi_ecl_`), so it can be
picked as a sample in the Analyze tab.

The text comes from the HTML pages on `www.rbi.org.in`, retrieved on 17 Oct 2026. The PDFs on `rbidocs.rbi.org.in`
sit behind a JavaScript bot challenge that the API cannot pass, so the HTML is the reliable source. The final
Directions' Annexes 1–5 (staging indicators, automation requirements, NPA format and others) exist only as PDFs and
are not included.

## Timeline

| # | Date | Document | RBI ref | Type | File |
| --- | --- | --- | --- | --- | --- |
| 1 | 16 Jan 2023 | Discussion Paper: Introduction of ECL Framework for Provisioning by Banks | — | Consultation | `rbi_ecl_2023_01_16_discussion_paper.md` |
| 2 | 16 Jan 2023 | Press release announcing the Discussion Paper | PR 2022-2023/1558 | Press release | `rbi_ecl_2023_01_16_press_release_discussion_paper.md` |
| 3 | 4 Oct 2023 | External Working Group on ECL constituted | PR 2023-2024/1043 | Press release | `rbi_ecl_2023_10_04_press_release_working_group.md` |
| 4 | 1 Oct 2025 | Statement on Developmental and Regulatory Policies (announces the draft ECL Directions) | PR 2025-2026/1218 | Press release | `rbi_ecl_2025_10_01_statement_developmental_regulatory_policies.md` |
| 5 | 7 Oct 2025 | **Draft** SCB Asset Classification, Provisioning and Income Recognition (ACPIR) Directions, 2025 | RBI/2025-26/XX | Draft for comments (until 30 Nov 2025) | `rbi_ecl_2025_10_07_draft_directions_scb.md` |
| 6 | 7 Oct 2025 | Press release: draft Directions issued | PR 2025-2026/1261 | Press release | `rbi_ecl_2025_10_07_press_release_draft_directions.md` |
| 7a | 28 Nov 2025, as updated 1 Jan 2026 | Commercial Banks – Income Recognition, Asset Classification and Provisioning (IRACP) Directions, 2025: the **current incurred-loss regime** | RBI/DOR/2025-26/164 | Master Direction (in force) | `rbi_ecl_2025_11_28_iracp_directions_v2026_01_01.md` |
| 7x | 4 Dec 2025 (effective 1 Jan 2026) | IRACP **Amendment** Directions, 2025: deletes para 117 (provisioning) | RBI/2025-26/128 | Amendment Directions | `rbi_ecl_2025_12_04_iracp_amendment_directions.md` |
| 7y | 29 Apr 2026 (effective **1 Jul 2026**) | IRACP **Amendment** Directions, 2026: deletes 57(4) and 80(6); inserts 62A/62B, B1 (84A–84D) and 139A/139B (resolution plans under the Resolution of Stressed Assets Directions, Chapter VI-A) | RBI/2026-27/45 | Amendment Directions | `rbi_ecl_2026_04_29_iracp_amendment_directions.md` |
| 7b | same Direction, as updated 1 Jul 2026 | IRACP Directions, 2025 after 7y | RBI/DOR/2025-26/164 | Master Direction (in force) | `rbi_ecl_2025_11_28_iracp_directions_v2026_07_01.md` |
| 8 | 27 Apr 2026 (effective **1 Apr 2027**) | **Final** Commercial Banks – ACPIR Directions, 2026 (ECL) | RBI/DOR/2026-27/398 | Master Direction (not yet in force) | `rbi_ecl_2026_04_27_acpir_directions_final.md` |
| 9 | 27 Apr 2026 (effective 1 Apr 2027) | IRACP **Repeal** Directions, 2026 | RBI/DOR/2026-27/36 | Repeal Directions | `rbi_ecl_2026_04_27_iracp_repeal_directions.md` |

Lineage expected in the graph (`AMENDS` / `SUPERSEDES` / `REFERS_TO`):

- 5 `REFERS_TO` 1, since the draft follows the Discussion Paper. The SDRP (4) and its press release (6) announce 5.
- 8 `SUPERSEDES` 5, as the final version of the draft.
- 8 `SUPERSEDES` 7 from 1 Apr 2027. Para 3 of 8 says banks stay under 7 until then.
- 9 repeals 7 from 1 Apr 2027.
- 7x `AMENDS` 7 from 1 Jan 2026 (para 117 deleted). This is already reflected in 7a.
- 7y `AMENDS` 7 from 1 Jul 2026, and 7b is the consolidated result. 7a → 7y → 7b is the core amendment scenario:
  - 7y deletes paras 57(4) and 80(6);
  - it inserts 62A and 62B (classification after a resolution plan);
  - it inserts B1 / 84A–84D (a 5% additional specific provision, with write-back after 20% repayment);
  - it inserts 139A and 139B (interest on a cash basis for re-restructured accounts).
- Not in this set: the 13 Amendment Directions issued with 8 on 27 Apr 2026 (Resolution of Stressed Assets,
  Concentration Risk, Investment Portfolio, Credit Risk Management, Cards, ALM, Wilful Defaulters, Capital Adequacy
  4th amendment and others). Their HTML pages can be added later for the cross-direction amendment scenario.

## Expected engine behaviour (as of 17 Oct 2026)

| Doc | Expected |
| --- | --- |
| 1, 5 | Classified `draft_or_consultation`. **No obligations.** The run stops at extraction with "No binding obligations were found…". The deterministic classifier already gets these right (`tests/test_ecl_eval_set.py`). |
| 2, 3, 4, 6 | Classified `press_release`. No obligations. 6 should point to the draft (5) as the announced instrument, and 4 to the upcoming draft. |
| 7x / 7y | Binding amendments. A run as of 17 Oct 2026 should extract only the changed obligations, e.g. the 5% additional provision (84A) and its write-back (84C). Ideally it links them to 7 (`AMENDS`) and does not re-extract the whole Direction. Running 7y as of 1 Jun 2026 must make its new obligations **UPCOMING**. |
| 7a / 7b | Binding and in force, under the incurred-loss regime. Obligations include NPA classification (90 days), standard-asset provisioning percentages, and NPA provisioning with ageing. The applicability gate should pass for a scheduled commercial bank. Checks needing data we don't hold (security valuations, NPA ageing buckets, restructuring flags) should be **unexecutable** or `NOT_EVALUATED`, never COMPLIANT. Running 7a and then 7b should show the amendment through run comparison: new rules for paras 62A/62B and B1, and removal of the deleted provisions. |
| 8 | Binding but effective 1 Apr 2027, so it rolls up to **UPCOMING**. Most obligations need ECL inputs we don't hold (staging and SICR, PD/LGD/EAD, EIR, macro scenarios, prudential floors), so expect unexecutable obligations with `missing_data`. No remediation actions should be proposed yet. |
| 9 | Binding, effective 1 Apr 2027, so **UPCOMING**. There are no checks to run. The repeal of 7 should be recorded as lineage, not as obligations. |

Known gaps this set exposes (to check in the eval):

- **Repeal Directions classified as `master_direction`** (confidence 0.62). There is no `repeal` document type yet.
- **Final Directions classified at low confidence (0.62):** the deterministic classifier is unsure about long Master Directions.
- **No supersession dates on lineage yet:** lineage edges (`AMENDS` / `SUPERSEDES`) are surfaced as references but not yet written to the graph with `valid_from` dates (roadmap P1).
- **No ECL data domain:** no staging, PD/LGD or EIR inputs are onboarded, so doc 8 can only be assessed for readiness, not compliance.

## How to run

In the UI, open Analyze, tick the samples in timeline order (or a pair such as 7a and 7b), choose agentic mode, and
set the analysis date. Use **Compare** on the Reports tab for 7a against 7b, and for 5 against 8.

Offline checks:

```bash
YUKDHIS_RUNTIME_DIR=$(mktemp -d) venv/bin/python -m pytest -q tests/test_ecl_eval_set.py
```
