"""Guard and compiler for ``record_assertion`` checks.

A ``record_assertion`` is declarative: ``{view, where, assert, tolerance?}``. ``where`` selects the rows the rule applies to,
``assert`` is what each of them must satisfy. Both are *expressions*, never SQL statements, so a model (or a template)
can propose them and a deterministic checker can prove them safe before anything touches the database.

Safety rules (all enforced here, by AST allow-list rather than by deny-list):

1. The ``view`` must be a sanitized view (``view_catalog.sanitized_views``). Only that view is read.
2. Each expression is one expression of at most ``MAX_EXPRESSION_CHARS`` characters, with no ``;``, comment markers,
   backslashes, control characters or quoted identifiers. It must parse as exactly one sqlglot expression.
3. Only these AST nodes are accepted: ``AND OR NOT ( )``, comparisons (``= <> != > >= < <=``), ``IN`` over literals,
   ``BETWEEN``, ``IS [NOT] NULL``, ``+ - * /`` and unary minus, number, string, ``TRUE/FALSE/NULL`` literals, unqualified
   columns of the view, and the single placeholder ``:as_of``. Everything else is rejected: functions, ``CAST``, ``CASE``,
   ``LIKE``, ``||``, ``%``, subqueries, ``SELECT``, parameters (``?``, ``$1``, ``@x``), hex/bit strings and so on.
4. Division is allowed only by a non-zero numeric literal (no data-dependent division by zero).
5. Columns must belong to the view and must not be PII or row identifiers (``loan_ref``, ``customer_ref``, ``loan_id``):
   an assertion can count rows, never fish for one.
6. Literals are bounded (numbers up to 1e12, strings of at most 40 safe characters, at most 20 values in an ``IN`` list),
   the tree has at most ``MAX_NODES`` nodes and ``MAX_DEPTH`` levels, and the root must be a boolean predicate.
7. Where the view's column types are known (pack views), operands are type-checked (``days_past_due > 'x'`` is rejected,
   an unknown enum value is a warning).
8. ``:as_of`` is replaced by an ISO date literal produced here, never by user text.

The engine then builds the SQL itself from the re-serialised AST (see ``SecureEngine._record_assertion``), runs it on the
read-only analyst connection with a row cap and a wall-clock limit, and returns counts, aggregates and a worklist of opaque
references only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import sqlglot
from sqlglot import exp

from bank_mcp_server.view_catalog import IDENTIFIER_COLUMNS, Column, sanitized_views, view_columns
from knowledge_graph.ontology import PII_COLUMNS

MAX_EXPRESSION_CHARS = 800
MAX_NODES = 120
MAX_DEPTH = 16
MAX_IN_VALUES = 20
MAX_STRING_CHARS = 40
MAX_NUMBER = 10 ** 12
SAFE_STRING = re.compile(r"^[A-Za-z0-9_ .:/-]*$")
NUMBER = re.compile(r"^\d+(\.\d+)?$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BANNED_TEXT = re.compile(r"--|/\*|\*/|;|\\|[\x00-\x08\x0b-\x1f\x7f]|\"|`")
COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)
ARITHMETIC = (exp.Add, exp.Sub, exp.Mul, exp.Div)
PREDICATES = (exp.And, exp.Or, exp.Not, exp.Paren, exp.In, exp.Between, exp.Is, exp.Boolean, *COMPARISONS)


class AssertionRejected(ValueError):
    """The expression, view or column is not allowed."""


@dataclass
class Compiled:
    view: str
    where: exp.Expression | None
    assertion: exp.Expression
    columns_used: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)

    def sql(self, dialect: str) -> tuple[str | None, str]:
        return (self.where.sql(dialect=dialect) if self.where is not None else None), self.assertion.sql(dialect=dialect)


def compile_assertion(view: str | None, where: str | None, assertion: str | None, as_of: str | None = None,
                      packs_dir=None) -> Compiled:
    """Parse and validate. ``as_of`` is substituted for ``:as_of`` (pass None to only validate the shape)."""
    if not view or view not in sanitized_views(packs_dir):
        raise AssertionRejected(f"view must be one of {sorted(sanitized_views(packs_dir))}, got {view!r}")
    if not (assertion or "").strip():
        raise AssertionRejected("assert is required")
    if as_of is not None and not _is_iso_date(as_of):
        raise AssertionRejected("as_of must be an ISO date")
    columns = view_columns(view, packs_dir)
    allowed = {name: col for name, col in columns.items() if name.lower() not in PII_COLUMNS | IDENTIFIER_COLUMNS}
    used: set[str] = set()
    warnings: list[str] = []
    parsed_where = _parse("where", where, allowed, as_of, used, warnings) if (where or "").strip() else None
    parsed_assert = _parse("assert", assertion, allowed, as_of, used, warnings)
    return Compiled(view=view, where=parsed_where, assertion=parsed_assert, columns_used=used, warnings=warnings)


def validate_check(view: str | None, where: str | None, assertion: str | None, packs_dir=None) -> tuple[list[str], list[str]]:
    """(errors, warnings) for use by the extraction validator; never raises."""
    try:
        compiled = compile_assertion(view, where, assertion, as_of=None, packs_dir=packs_dir)
    except AssertionRejected as error:
        return [str(error)], []
    return [], compiled.warnings


# ------------------------------------------------------------------------------------------------ parsing
def _parse(label: str, text: str | None, allowed: dict[str, Column], as_of: str | None, used: set[str],
           warnings: list[str]) -> exp.Expression:
    text = (text or "").strip()
    if not text:
        raise AssertionRejected(f"{label} is empty")
    if len(text) > MAX_EXPRESSION_CHARS:
        raise AssertionRejected(f"{label} is longer than {MAX_EXPRESSION_CHARS} characters")
    if BANNED_TEXT.search(text):
        raise AssertionRejected(f"{label} contains a forbidden character or comment marker")
    try:
        statements = sqlglot.parse(text, read="postgres")
    except sqlglot.errors.SqlglotError as error:
        raise AssertionRejected(f"{label} is not a valid expression: {str(error).splitlines()[0][:120]}") from error
    if len(statements) != 1 or statements[0] is None:
        raise AssertionRejected(f"{label} must be a single expression")
    tree = statements[0]
    if not isinstance(tree, PREDICATES):
        raise AssertionRejected(f"{label} must be a boolean condition, not {type(tree).__name__}")
    nodes = list(tree.walk())
    if len(nodes) > MAX_NODES:
        raise AssertionRejected(f"{label} is too complex (more than {MAX_NODES} nodes)")
    if _depth(tree) > MAX_DEPTH:
        raise AssertionRejected(f"{label} is nested more than {MAX_DEPTH} levels")
    for node in nodes:
        _check_node(label, node, allowed, used)
    _type_of(label, tree, allowed, warnings)
    return _substitute_as_of(tree, as_of)


def _depth(node: exp.Expression) -> int:
    best, stack = 0, [(node, 1)]
    while stack:
        current, level = stack.pop()
        best = max(best, level)
        stack.extend((child, level + 1) for child in current.iter_expressions())
    return best


def _check_node(label: str, node: exp.Expression, allowed: dict[str, Column], used: set[str]) -> None:
    if isinstance(node, exp.Column):
        if node.args.get("table") or node.args.get("db") or node.args.get("catalog"):
            raise AssertionRejected(f"{label}: qualified column names are not allowed")
        name = node.name.lower()
        if name in PII_COLUMNS or name in IDENTIFIER_COLUMNS:
            raise AssertionRejected(f"{label}: column {node.name!r} is PII or a row identifier and cannot be used")
        if name not in allowed:
            raise AssertionRejected(f"{label}: unknown column {node.name!r}; allowed: {', '.join(sorted(allowed))}")
        if isinstance(node.this, exp.Star) or node.this.args.get("quoted"):
            raise AssertionRejected(f"{label}: quoted or star columns are not allowed")
        used.add(name)
        return
    if isinstance(node, exp.Identifier):
        if not isinstance(node.parent, exp.Column):
            raise AssertionRejected(f"{label}: bare identifier {node.name!r}")
        return
    if isinstance(node, exp.Literal):
        value = str(node.this)
        if node.is_string:
            if len(value) > MAX_STRING_CHARS or not SAFE_STRING.match(value):
                raise AssertionRejected(f"{label}: string literal {value[:20]!r} is too long or has unsafe characters")
        elif not NUMBER.match(value) or float(value) > MAX_NUMBER:
            raise AssertionRejected(f"{label}: number literal {value[:20]!r} is not a plain number up to {MAX_NUMBER:g}")
        return
    if isinstance(node, exp.Placeholder):
        if node.name != "as_of" or node.args.get("kind"):
            raise AssertionRejected(f"{label}: the only parameter allowed is :as_of")
        return
    if isinstance(node, exp.Div):
        divisor = node.expression
        while isinstance(divisor, exp.Paren):
            divisor = divisor.this
        if not (isinstance(divisor, exp.Literal) and not divisor.is_string and float(divisor.this) != 0):
            raise AssertionRejected(f"{label}: division is allowed only by a non-zero number literal")
        return
    if isinstance(node, exp.In):
        if node.args.get("query") or node.args.get("unnest") or node.args.get("field"):
            raise AssertionRejected(f"{label}: IN must list literal values")
        values = node.expressions
        if not values or len(values) > MAX_IN_VALUES or not all(isinstance(v, exp.Literal) for v in values):
            raise AssertionRejected(f"{label}: IN needs 1 to {MAX_IN_VALUES} literal values")
        return
    if isinstance(node, exp.Is):
        if not isinstance(node.expression, (exp.Null, exp.Boolean)):
            raise AssertionRejected(f"{label}: IS must be followed by NULL, TRUE or FALSE")
        return
    if isinstance(node, (*COMPARISONS, *ARITHMETIC, exp.And, exp.Or, exp.Not, exp.Paren, exp.Neg, exp.Between, exp.Boolean,
                         exp.Null)):
        return
    raise AssertionRejected(f"{label}: {type(node).__name__} is not allowed (comparisons, AND/OR/NOT, IN, BETWEEN, "
                            "IS NULL and arithmetic only)")


def _substitute_as_of(tree: exp.Expression, as_of: str | None) -> exp.Expression:
    def swap(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Placeholder):
            if as_of is None:
                return exp.Literal.string("1970-01-01")  # shape-only validation
            return exp.Literal.string(as_of)
        return node

    return tree.transform(swap)


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return bool(ISO_DATE.match(value))
    except ValueError:
        return False


# ------------------------------------------------------------------------------------------------ typing
def _type_of(label: str, node: exp.Expression, allowed: dict[str, Column], warnings: list[str]) -> str:
    """Best-effort type inference: num | text | date | bool | unknown. Raises on a definite mismatch."""
    if isinstance(node, exp.Paren):
        return _type_of(label, node.this, allowed, warnings)
    if isinstance(node, exp.Column):
        column = allowed[node.name.lower()]
        return {"int": "num", "bool": "bool", "text": "text", "enum": "text", "date": "date"}.get(column.type, "unknown")
    if isinstance(node, exp.Literal):
        return "text" if node.is_string else "num"
    if isinstance(node, exp.Boolean):
        return "bool"
    if isinstance(node, exp.Null):
        return "null"
    if isinstance(node, exp.Placeholder):
        return "date"
    if isinstance(node, exp.Neg):
        kind = _type_of(label, node.this, allowed, warnings)
        _require(label, kind in {"num", "unknown"}, "unary minus needs a number")
        return "num"
    if isinstance(node, ARITHMETIC):
        for side in (node.this, node.expression):
            kind = _type_of(label, side, allowed, warnings)
            _require(label, kind in {"num", "unknown"}, f"arithmetic needs numbers, got {kind}")
        return "num"
    if isinstance(node, (exp.And, exp.Or)):
        for side in (node.this, node.expression):
            _require(label, _type_of(label, side, allowed, warnings) in {"bool", "unknown"}, "AND/OR need conditions")
        return "bool"
    if isinstance(node, exp.Not):
        _require(label, _type_of(label, node.this, allowed, warnings) in {"bool", "unknown"}, "NOT needs a condition")
        return "bool"
    if isinstance(node, COMPARISONS):
        left, right = (_type_of(label, node.this, allowed, warnings), _type_of(label, node.expression, allowed, warnings))
        _comparable(label, left, right, node, allowed, warnings)
        return "bool"
    if isinstance(node, exp.In):
        left = _type_of(label, node.this, allowed, warnings)
        for value in node.expressions:
            _comparable(label, left, _type_of(label, value, allowed, warnings), exp.EQ(this=node.this, expression=value),
                        allowed, warnings)
        return "bool"
    if isinstance(node, exp.Between):
        left = _type_of(label, node.this, allowed, warnings)
        for bound in (node.args["low"], node.args["high"]):
            _comparable(label, left, _type_of(label, bound, allowed, warnings), node, allowed, warnings)
        return "bool"
    if isinstance(node, exp.Is):
        _type_of(label, node.this, allowed, warnings)
        return "bool"
    return "unknown"


def _require(label: str, condition: bool, message: str) -> None:
    if not condition:
        raise AssertionRejected(f"{label}: {message}")


def _comparable(label: str, left: str, right: str, node: exp.Expression, allowed: dict[str, Column], warnings: list[str]) -> None:
    if "unknown" in (left, right) or "null" in (left, right):
        return
    if {left, right} == {"date", "text"}:  # a date column against a string literal: the literal must be an ISO date
        left = right = "date"
    if left != right:
        raise AssertionRejected(f"{label}: cannot compare {left} with {right} in {node.sql()}")
    if left == "date":
        for side in (node.this, node.expression):
            if isinstance(side, exp.Literal) and side.is_string and not ISO_DATE.match(str(side.this)):
                raise AssertionRejected(f"{label}: date comparisons need ISO dates (YYYY-MM-DD), got {side.this!r}")
    if isinstance(node.this, exp.Column):
        column = allowed.get(node.this.name.lower())
        if column is not None and column.type == "enum" and column.values:
            literals = [node.expression] if not isinstance(node, exp.Between) else [node.args["low"], node.args["high"]]
            for literal in literals:
                if isinstance(literal, exp.Literal) and literal.is_string and str(literal.this) not in column.values:
                    warnings.append(f"{label}: {literal.this!r} is not a known value of {column.name} ({'|'.join(column.values)})")
