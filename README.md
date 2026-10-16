# Yukdhis: Autonomous Compliance Engine (AI Builder Cup 2026, BFSI)

"Claude Code for banking compliance." A regulatory circular goes in. The engine reads it, wires its rules into a
BIAN knowledge graph, computes the blast radius across products, processes, people and branches, interrogates
operational data without exposing PII to the LLM, and proposes remediation that a compliance officer approves.

**100% Google Cloud:** Google ADK · Gemini 2.5 Pro/Flash on Vertex AI · Gemma 2 (Vertex AI endpoint / GKE) ·
Document AI Layout Parser · Spanner Graph · Cloud SQL for PostgreSQL · Sensitive Data Protection (Cloud DLP) + Cloud KMS ·
Vertex AI Memory Bank · Cloud Run · Pub/Sub · Eventarc · Cloud Scheduler · Cloud Storage · Secret Manager ·
Cloud Build / Artifact Registry · Firebase Hosting + Firebase Authentication.

## What it does (demo data)

| Circular | Blast radius | Finding |
| --- | --- | --- |
| RBI/2023-24/60: release property documents within 30 days | 4 secured retail products (MSME and unsecured excluded) | **12 loans** breached the 30-day limit, **7 at Koramangala**; ₹2.46 Cr statutory compensation; 5 control gaps (e.g. GoldEase has no release tracker) |
| RBI/2023-24/55: floating-rate reset on EMI loans | 3 floating EMI products | DriveNow lacks a fixed-rate switch (edge expired 2024-07-01, visible via graph time travel); borrower-notice and reset-statement gaps |

## Agent workflow (Google ADK)

```
Watcher (Eventarc / Scheduler on GCS incoming/)
   │
SequentialAgent compliance_pipeline
   ├─ reader_agent        Document AI chunks → Gemini structured rules → deterministic validator (maker-checker)
   ├─ mapper_agent        entity resolution (Vertex embeddings + BIAN glossary) → bitemporal Spanner Graph edges
   ├─ ParallelAgent impact_investigation
   │    ├─ graphrag_agent    GQL traversal Rule→Product→Process/Control→Team (the "Detective")
   │    └─ precedent_agent   prior reports + Vertex AI Memory Bank
   ├─ data_analyst_agent  aggregate-only SQL over sanitized vw_* views, sqlglot AST guard (the "Auditor")
   ├─ evaluator_agent     deterministic checks → violations, penalties, worklist ids
   └─ orchestrator_agent  impact report + remediation proposals (PENDING_APPROVAL)
                                   │ human approves in the UI
                     Pub/Sub → action-engine (separate Cloud Run SA) hydrates PII inside the VPC
```

All counts come from deterministic code. The LLM routes, reasons, writes guarded queries and drafts. Every
request to Gemini passes an ADK `before_model_callback` that masks PII (Cloud DLP + Gemma, reversible via
KMS-wrapped crypto-deterministic tokens). See [docs/architecture.md](docs/architecture.md).

## Layout

| Path | Purpose |
| --- | --- |
| `agents/compliance_coordinator/` | ADK root `SequentialAgent`, 7 sub-agents, function tools |
| `agents/compliance_assistant/` | Multi-turn chat agent over reports, graph time travel and safe SQL |
| `api/` | FastAPI bridge for Cloud Run: runs, WebSocket events, HITL approvals, Eventarc and Pub/Sub endpoints |
| `pipeline/` | Stage functions shared by agent tools and the deterministic runner |
| `ingestion/` | Document AI parsing, templates of record, Gemini extractor, validator, watcher |
| `knowledge_graph/` | BIAN ontology, bitemporal SQLite/Spanner store, mapper, traversal, GQL guard |
| `bank_mcp_server/` | Secure Execution Engine (+ MCP server), SQL AST guard, Cloud SQL access, action engine |
| `privacy/` | DLP / Gemma / vault sanitizer, ADK model callback |
| `core/` | Config, typed contracts, app store (runs, reports, actions, audit), Gemini client |
| `web/` | React + Vite + TypeScript UI for Firebase Hosting |
| `infrastructure/` | `gcp/deploy.sh` (gcloud only), Spanner Graph DDL, Cloud SQL schema and roles |
| `data/` | Sample RBI circulars, generated synthetic bank (`scripts/generate_synthetic_bank.py`) |

## Run locally (offline, no cloud needed)

```bash
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
python scripts/run_demo.py
```

```bash
cd web && npm install && npm run build && cd .. && uvicorn api.main:app --port 8080
```

Open http://localhost:8080. Without `GOOGLE_CLOUD_PROJECT` the API runs in deterministic mode. Set it (with
`gcloud auth application-default login`) to enable the agentic ADK + Gemini mode and the chat assistant. Every
other component stays local unless you set `YUKDHIS_BACKEND=gcp` or a per-component `*_BACKEND`
(see `.env.example`). Use `npm run dev` in `web/` for hot reload; it proxies to port 8080.

ADK developer UI: `PYTHONPATH=. adk web agents`. Tests: `python -m pytest -q`.

## Deploy to Google Cloud

```bash
cp infrastructure/gcp/config.env.example infrastructure/gcp/config.env
bash infrastructure/gcp/deploy.sh all
```

Read [docs/deployment.md](docs/deployment.md) first. It covers costs (Spanner Enterprise, Cloud SQL, and an L4 GPU
for Gemma), the order of steps, and the Firebase Authentication setup.
