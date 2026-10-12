# Deployment: local demo and Spanner Graph

The prototype runs locally with SQLite so that its demo result is repeatable.
The architecture graph has the same `Nodes`/`Edges` topology in the Spanner
DDL at `infrastructure/spanner_graph/schema.ddl`; operational customer/loan
data remains outside that graph.

## Local demo

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/run_demo.py --output artifacts/floating-rate-impact.json
```

Expected synthetic blast radius: **10 active floating-rate EMI loans**, with 6
borrowers needing notification, 3 loans missing statements, and 3 loans
without a fixed-rate conversion option. No message, ticket, or bank-record
change is made by this command.

## Google Cloud configuration

Create a regional Spanner instance and database under the same project that
runs the ADK service. Then set `SPANNER_INSTANCE` and `SPANNER_DATABASE`, and
run `bash infrastructure/spanner_graph/deploy.sh`, then
`python scripts/seed_spanner_graph.py` for the synthetic topology. The service account needs
`roles/spanner.databaseUser` for runtime reads; use a separate, tightly scoped
migration identity for DDL deployment. Configure `BANK_GRAPH_BACKEND=spanner`,
`SPANNER_INSTANCE`, and `SPANNER_DATABASE` in the deployment environment.

Before production, replace the synthetic CSV repository with a read-only
parameterized data adapter, enforce row-level access and audit logs, and have
Compliance approve rule-extraction confidence thresholds. Do not place PII in
the graph attributes or use an LLM as the final eligibility decision maker.
