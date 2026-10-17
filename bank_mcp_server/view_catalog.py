"""Catalogue of the sanitized operational views, with typed columns.

The base vocabulary lives in ``knowledge_graph.ontology.SANITIZED_VIEWS``. Domain packs
(``data/ontology/packs/*.json``, e.g. ``provisioning_iracp.json``) add views of their own. This module merges both so that
the SQL guard and the ``record_assertion`` guard accept a pack's views even when ``ontology`` has not loaded packs, and
reads a column's type from its description.

Column description convention (packs): ``"<type>: <text>"`` where ``<type>`` is ``int``, ``bool``, ``text``, ``date`` or
``enum[A|B|C]``. A description without a type prefix gives an ``unknown`` type, which the assertion guard does not type-check.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from core.config import DATA_DIR
from knowledge_graph.ontology import SANITIZED_VIEWS

PACKS_DIR = DATA_DIR / "ontology" / "packs"
TYPE_PREFIX = re.compile(r"^(int|bool|text|date|enum\[([^\]]*)\])\s*:\s*(.*)$", re.DOTALL)
# Columns that identify a row. They may be returned in worklists but never appear in an assertion expression.
IDENTIFIER_COLUMNS = frozenset({"loan_ref", "customer_ref", "loan_id"})


@dataclass(frozen=True)
class Column:
    name: str
    type: str  # int | bool | text | date | enum | unknown
    values: tuple[str, ...] = ()
    description: str = ""


def parse_column(name: str, description: str) -> Column:
    match = TYPE_PREFIX.match(description or "")
    if not match:
        return Column(name, "unknown", (), description or "")
    kind = match.group(1)
    if kind.startswith("enum"):
        return Column(name, "enum", tuple(v.strip() for v in (match.group(2) or "").split("|") if v.strip()), match.group(3))
    return Column(name, kind, (), match.group(3))


@lru_cache(maxsize=8)
def _pack_views(directory: str) -> dict[str, dict]:
    views: dict[str, dict] = {}
    for path in sorted(Path(directory).glob("*.json")):
        try:
            pack = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(pack, dict):
            continue
        for name, spec in (pack.get("views") or {}).items():
            if isinstance(spec, dict) and isinstance(spec.get("columns"), dict):
                merged = views.setdefault(name, {"description": spec.get("description", ""), "columns": {}})
                merged["columns"].update(spec["columns"])
    return views


def sanitized_views(packs_dir: Path | None = None) -> dict[str, dict]:
    """Base ontology views plus pack views; columns of a view defined in both are unioned."""
    merged = {name: {"description": spec.get("description", ""), "columns": dict(spec["columns"])}
              for name, spec in SANITIZED_VIEWS.items()}
    for name, spec in _pack_views(str(packs_dir or PACKS_DIR)).items():
        target = merged.setdefault(name, {"description": spec["description"], "columns": {}})
        for column, description in spec["columns"].items():
            target["columns"].setdefault(column, description)
    return merged


def view_columns(view: str, packs_dir: Path | None = None) -> dict[str, Column]:
    spec = sanitized_views(packs_dir).get(view)
    if spec is None:
        raise KeyError(view)
    return {name: parse_column(name, description) for name, description in spec["columns"].items()}


def reset_cache() -> None:
    _pack_views.cache_clear()
