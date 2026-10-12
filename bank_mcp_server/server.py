"""MCP facade for least-privilege, read-only bank queries.

Run with: python -m bank_mcp_server.server
"""

from __future__ import annotations

from bank_mcp_server.repository import ReadOnlyBankRepository

try:  # Optional at import time so the core demo does not need an MCP runtime.
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    FastMCP = None


repository = ReadOnlyBankRepository()


def find_active_floating_loans(product_ids: list[str]) -> list[dict]:
    """Return minimal, non-PII fields for active floating-rate loans."""
    return repository.active_floating_loans(product_ids)


if FastMCP:
    mcp = FastMCP("bank-readonly-data")
    mcp.tool()(find_active_floating_loans)

    if __name__ == "__main__":
        mcp.run()
elif __name__ == "__main__":  # pragma: no cover
    raise SystemExit("Install the 'mcp' optional dependency to run the MCP server.")
