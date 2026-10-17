# Synthetic banking demo data

Everything here is fictional and regenerated deterministically by `python scripts/generate_synthetic_bank.py`
(seed 2026, reference date 2026-10-17; running it twice gives identical bytes). `docs/evals/bank-baseline.md` describes the bank,
the planted exceptions and the eval suite.

- `architecture.json`: BIAN topology for the knowledge graph (12 products, 15 processes, 18 teams, 20 branches, 2 segments,
  53 controls, 13 internal policies) with bitemporal edges. It includes `Policy` nodes of `kind: internal`, `ENFORCES`
  (internal policy to control) and `IMPLEMENTS` (internal policy to a regulatory rule). It contains no customers.
- `controls.json`, `policies.json`: the control library (implementation status, owner, evidence, last test, data check) and the
  internal policies with their clauses, mapped to RBI instruments and paragraphs and to controls. `architecture.json` is built from them.
  The ECL controls (staging, SICR criteria, PD/LGD/EAD, EIR, macro scenarios, validation, floors) are absent or planned on purpose.
- `operational/*.csv`: the core-banking extract loaded into Cloud SQL / SQLite. 20 branches, 2,500 customers, 3,998 loans (30 columns,
  including days past due, asset classification, provisions and interest basis), about 16,000 loan events. `customers.csv` and
  `branches.csv` hold **synthetic PII** (names, `example.com` emails, `+91-90000…` phones, fake PANs) so the privacy controls have
  something to protect. Agents only ever see the `vw_*` views, which exclude these columns. There is no PD, LGD or EAD.
- `entity_profile.json`: the regulated-entity profile used by the applicability gate. `asset_quality` and `kyc` are onboarded data
  domains; `ecl_inputs` are not.
- `manifest.json`: row counts, the asset-class mix, and the **planted exceptions with exact counts**: 12 document-release violations (7 at
  Koramangala), the rate-reset gaps, and the IRACP exceptions (14 accounts more than 90 days past due still standard, 6 SMA mis-tags, 16 NPA
  provision shortfalls at Pune Kothrud and Lucknow Hazratganj, 5 re-restructured accounts on accrual, 22 customers past their re-KYC date).

The ontology pack that declares the new views, record flags, events and capabilities is `data/ontology/packs/provisioning_iracp.json`,
written by the same generator (`scripts/bank_pack.py`).
