# Design notes (from design meeting)

- **Parser agent**: converts PDFs to markdown (e.g. Docling or Document AI), chunks them, stores versioned docs in RAG, and diffs new circulars against prior versions. Its output is structured JSON of rule changes.
- **Coordinator**: the ADK orchestrator. It accepts RBI feed/uploaded PDFs and user Q&A, and routes work to the other agents.
- **Mapper**: turns structured policy requirements into explicit paths through the bank graph (rule → product → process/control). It produces auditable mapping evidence and does not query customer records.
- **Analyzer**: receives mapper evidence, joins its eligible product scope to read-only operational loan data, and computes the blast radius and operational compliance gaps.
- **Executor**: performs safe actions (reports, emails, GCS report drops). Changes to bank systems need human approval.
- **Knowledge graph**: holds only entity relationships (policies, products, processes and controls). Customer detail data stays in SQL/BigQuery. The demo runs on SQLite and includes an equivalent Spanner Graph schema for deployment.
- **Deterministic vs LLM**: use the LLM for classification, reasoning and structuring. Anything critical should be deterministic code.
- **Owners**: ingestion/RAG — Sunil; orchestration, analyzer, executor — Shiva; knowledge graph and seed data — Saurabh; pitch deck — Divya.
- **Example use case**: a repo rate change → home loan product → impacted floating-rate customers.
