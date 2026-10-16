# Synthetic banking demo data

Everything here is fictional and regenerated deterministically by `python scripts/generate_synthetic_bank.py`
(seed 2026, reference date 2026-10-17).

- `architecture.json`: BIAN topology for the knowledge graph (products, processes, controls, teams, segments,
  branches) with bitemporal edges. It contains no customers.
- `operational/*.csv`: the core-banking extract loaded into Cloud SQL / SQLite. `customers.csv` and `branches.csv`
  hold **synthetic PII** (names, `example.com` emails, `+91-90000…` phones, fake PANs) so the privacy controls have
  something to protect. Agents only ever see the `vw_*` views, which exclude these columns.
- `manifest.json`: row counts and the planted scenario (12 document-release violations, 7 at Koramangala).
