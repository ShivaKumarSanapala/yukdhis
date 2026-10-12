# Design notes (from design meeting)

- **Parser agent**: converts PDFs to markdown (e.g. Docling or Document AI), chunks them, stores versioned docs in RAG, and diffs new circulars against prior versions. Its output is structured JSON of rule changes.
- **Coordinator**: the ADK orchestrator. It accepts RBI feed/uploaded PDFs and user Q&A, and routes work to the other agents.
- **Analyzer** (mapper + reasoner): maps changes to graph nodes (policy → product → customer), then queries bank data through tools to get the blast radius. It has read-only access.
- **Executor**: performs safe actions (reports, emails, GCS report drops). Changes to bank systems need human approval.
- **Knowledge graph**: holds only entity relationships (policies, products, processes, customer links). Customer detail data stays in SQL/BigQuery. Candidate store: Spanner Graph.
- **Deterministic vs LLM**: use the LLM for classification, reasoning and structuring. Anything critical should be deterministic code.
- **Owners**: ingestion/RAG — Sunil; orchestration, analyzer, executor — Shiva; knowledge graph and seed data — Saurabh; pitch deck — Divya.
- **Example use case**: a repo rate change → home loan product → impacted floating-rate customers.
