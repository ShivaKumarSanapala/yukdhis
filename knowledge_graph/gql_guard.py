"""Read-only guard for LLM-authored Spanner Graph (GQL) queries."""

from __future__ import annotations

import re

FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|DROP|ALTER|SET|REMOVE|DETACH|CALL|GRANT|REVOKE|TRUNCATE|EXPORT)\b",
    re.IGNORECASE,
)
MAX_RESULT_ROWS = 200


class GqlRejected(ValueError):
    pass


def validate_gql(query: str) -> str:
    """Return a safe, row-limited query or raise GqlRejected."""
    text = re.sub(r"--[^\n]*", "", query).strip().rstrip(";")
    if ";" in text:
        raise GqlRejected("Only one statement is allowed.")
    if not re.match(r"^GRAPH\s+BankArchitecture\b", text, re.IGNORECASE):
        raise GqlRejected("Query must start with 'GRAPH BankArchitecture'.")
    if not re.search(r"\bMATCH\b", text, re.IGNORECASE) or not re.search(r"\bRETURN\b", text, re.IGNORECASE):
        raise GqlRejected("Query must be a MATCH ... RETURN read.")
    if FORBIDDEN.search(text):
        raise GqlRejected(f"Forbidden keyword: {FORBIDDEN.search(text).group(0).upper()}")
    if "@tenant_id" not in text:
        raise GqlRejected("Query must filter on @tenant_id (bound by the engine) for tenant isolation.")
    match = re.search(r"\bLIMIT\s+(\d+)\s*$", text, re.IGNORECASE)
    if match:
        if int(match.group(1)) > MAX_RESULT_ROWS:
            text = text[: match.start(1)] + str(MAX_RESULT_ROWS)
    else:
        text += f"\nLIMIT {MAX_RESULT_ROWS}"
    return text
