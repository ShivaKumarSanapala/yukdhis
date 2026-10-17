"""AST guardrail for LLM-authored SQL (sqlglot).

The database role is already SELECT-only on sanitized views; this guard is the
second, deterministic layer that runs *before* execution:

1. exactly one statement, and it is a SELECT (no DML/DDL/commands anywhere);
2. every table is an allow-listed ``vw_*`` view (CTE names excepted);
3. no PII column names, no ``SELECT *``;
4. aggregate-only output: no row-level identifiers, every projection is an
   aggregate or a GROUP BY key;
5. LIMIT forced to <= 100, then transpiled to the target dialect.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from bank_mcp_server.view_catalog import sanitized_views
from knowledge_graph.ontology import PII_COLUMNS

MAX_ROWS = 100
IDENTIFIER_COLUMNS = {"loan_ref", "customer_ref", "loan_id"}
FORBIDDEN_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command,
                   exp.Merge, exp.TruncateTable, exp.Grant, exp.Into)


class SqlRejected(ValueError):
    pass


@dataclass
class GuardResult:
    sql: str
    tables: list[str]
    original: str


def validate_sql(query: str, dialect: str = "sqlite", source_dialect: str = "postgres") -> GuardResult:
    try:
        statements = [s for s in sqlglot.parse(query, read=source_dialect) if s is not None]
    except sqlglot.errors.ParseError as error:
        raise SqlRejected(f"Unparseable SQL: {error.errors[0]['description'] if error.errors else error}") from error
    if len(statements) != 1:
        raise SqlRejected("Exactly one statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, exp.Select):
        raise SqlRejected(f"Only SELECT is allowed, got {tree.key.upper()}.")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SqlRejected(f"Forbidden operation: {node.key.upper()}.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables = sorted({t.name.lower() for t in tree.find_all(exp.Table)} - cte_names)
    views = sanitized_views()
    unknown = [t for t in tables if t not in views]
    if unknown:
        raise SqlRejected(f"Only sanitized views are queryable ({', '.join(views)}); rejected: {', '.join(unknown)}.")
    if not tables:
        raise SqlRejected("Query must read from a sanitized view.")
    for column in tree.find_all(exp.Column):
        if column.name.lower() in PII_COLUMNS:
            raise SqlRejected(f"Column '{column.name}' is PII and not available.")
    if any(isinstance(p, exp.Star) or (isinstance(p, exp.Column) and isinstance(p.this, exp.Star)) for p in tree.expressions):
        raise SqlRejected("SELECT * is not allowed; select aggregates.")

    group_keys = {g.sql(dialect=source_dialect).lower() for g in (tree.args.get("group") or exp.Group()).expressions}
    group_names = {g.alias_or_name.lower() for g in (tree.args.get("group") or exp.Group()).expressions}
    has_aggregate = False
    for projection in tree.expressions:
        inner = projection.this if isinstance(projection, exp.Alias) else projection
        if inner.find(exp.AggFunc):
            has_aggregate = True
            if any(isinstance(arg, exp.Column) and arg.name.lower() in IDENTIFIER_COLUMNS and not isinstance(agg, exp.Count)
                   for agg in inner.find_all(exp.AggFunc) for arg in agg.find_all(exp.Column)):
                raise SqlRejected("Aggregating identifiers into lists is not allowed.")
            continue
        if inner.sql(dialect=source_dialect).lower() not in group_keys and inner.alias_or_name.lower() not in group_names:
            raise SqlRejected(f"'{projection.sql()}' must be aggregated or in GROUP BY (aggregate-only access).")
        if any(c.name.lower() in IDENTIFIER_COLUMNS for c in inner.find_all(exp.Column)) or (
                isinstance(inner, exp.Column) and inner.name.lower() in IDENTIFIER_COLUMNS):
            raise SqlRejected("Grouping by row-level identifiers is not allowed.")
    if not has_aggregate:
        raise SqlRejected("Query must return aggregates (COUNT/SUM/AVG/MIN/MAX).")

    limit = tree.args.get("limit")
    current = None
    if limit is not None:
        try:
            current = int(limit.expression.this)
        except (AttributeError, TypeError, ValueError):
            current = None
    if current is None or current > MAX_ROWS:
        tree = tree.limit(MAX_ROWS)
    return GuardResult(sql=tree.sql(dialect=dialect), tables=tables, original=query)
