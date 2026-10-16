"""MCP server exposing the Secure Execution Engine tools over a standard boundary.

Run inside the VPC:  python -m bank_mcp_server.server
Any MCP-capable agent (including ADK via McpToolset) gets the same four
contracts: schema discovery, guarded aggregate SQL, tokenized samples and the
approval-gated remediation router.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from bank_mcp_server.secure_engine import SecureEngine

mcp = FastMCP("yukdhis-secure-engine")
engine = SecureEngine()


@mcp.tool()
def get_graph_ontology(target_domain: str = "All") -> dict:
    """Knowledge-graph schema and sanitized view catalogue. No data."""
    return engine.get_graph_ontology(target_domain)


@mcp.tool()
def analyze_segment_impact(sql: str, intent: str) -> dict:
    """Aggregate-only SELECT over sanitized views, AST-validated before execution."""
    return engine.run_safe_sql(sql, intent, actor="mcp-client")


@mcp.tool()
def fetch_anonymized_samples(worklist_id: str, sample_size: int = 3, session_id: str = "mcp") -> dict:
    """Up to 5 tokenized sample records from a worklist."""
    return engine.fetch_anonymized_samples(worklist_id, sample_size, session_id)


@mcp.tool()
def trigger_compliance_workflow(workflow_id: str, rule_id: str, worklist_id: str, target_count: int,
                                generated_notice_text: str, owner: str = "Compliance") -> dict:
    """Queue a remediation workflow for human approval (never executes directly)."""
    action = engine.propose_workflow(workflow_id, rule_id, worklist_id or None, target_count, generated_notice_text, owner)
    return action.model_dump()


if __name__ == "__main__":
    mcp.run()
