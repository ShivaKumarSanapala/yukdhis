# Yukdhis — Banking Policy Intelligence & Compliance Impact Engine

Multi-agent system (Google ADK + Gemini on Vertex AI) that ingests RBI circulars and policy documents, maps changes to a bank's internal policies, products, processes and customers, computes the blast radius, and drafts follow-up actions.

## Architecture

```
RBI API / user PDF ──► Parser agent ──► RAG store (versioned circulars)
                                  │
                     Compliance Coordinator (orchestrator)
                                  │
                      Mapper agent ◄── Knowledge graph (products ↔ processes ↔ controls)
                                  │ mapping evidence
                      Analyzer agent ◄── Bank MCP tools (read-only loan data)
                       ▼
              Executor agent ──► reports / notifications (human approval for bank changes)
```

## Layout

| Path | Purpose |
| --- | --- |
| `agents/compliance_coordinator/` | ADK root agent (orchestrator) |
| `agents/compliance_coordinator/sub_agents/{parser,mapper,analyzer,executor}/` | Domain agents |
| `agents/compliance_coordinator/tools/` | Shared ADK tools |
| `ingestion/` | Circular retrieval, PDF→markdown, chunking, RAG indexing |
| `knowledge_graph/` | Bank graph schema, builders, `seed/` dummy data |
| `bank_mcp_server/` | MCP server exposing read-only bank data tools |
| `data/` | Sample circulars and dummy bank data |
| `docs/` | Problem statement, design notes, meeting transcripts |
| `tests/` | Tests |

The mapper and analyser are deliberately distinct: the mapper identifies the
bank topology and required controls, while the analyser joins only that scoped
set to read-only operational records to calculate the blast radius. The data
fixtures are synthetic and contain opaque customer references only.

## Run

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example agents/compliance_coordinator/.env   # fill in project
adk run agents/compliance_coordinator    # CLI
adk web agents                            # dev UI
python scripts/run_demo.py                 # deterministic local end-to-end demo
```

For a Spanner Graph deployment that uses the same Nodes/Edges topology, see
[docs/deployment.md](docs/deployment.md).

## Submission (due 18th)

Pitch deck (prescribed template), a 3–4 minute demo video, a prototype link, this repo, and a description of the Google technologies used.
