"""Bitemporal, multi-tenant knowledge-graph store.

Two interchangeable backends share one interface:

* ``LocalGraphStore``   - SQLite, used offline, in tests and in the demo.
* ``SpannerGraphStore`` - Cloud Spanner Graph; reads use GQL over the
  ``BankArchitecture`` property graph, writes use table mutations.

Edges are bitemporal: ``valid_from``/``valid_to`` is *business* time (when the
relationship was true in the bank) and ``recorded_at``/``superseded_at`` is
*system* time (when we learned it).  Re-writing an edge with different
attributes supersedes the old row instead of overwriting it, so every graph
state that drove a past compliance decision can be reproduced.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

from core.config import DATA_DIR, Settings, get_settings

ARCHITECTURE_FILE = DATA_DIR / "bank" / "architecture.json"
OPEN_END = "9999-12-31"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _iso(value: str | date | None) -> str:
    if value is None:
        return date.today().isoformat()
    return value.isoformat() if isinstance(value, date) else str(value)[:10]


class GraphStore(Protocol):
    tenant_id: str
    query_language: str
    query_log: list[str]

    def nodes(self, node_type: str | None = None) -> list[dict[str, Any]]: ...
    def node(self, node_id: str) -> dict[str, Any] | None: ...
    def neighbours(self, ids: Iterable[str], edge_types: Iterable[str] | None, as_of: str | date | None,
                   direction: str = "out") -> list[dict[str, Any]]: ...
    def upsert_node(self, node_id: str, node_type: str, name: str, attributes: dict[str, Any]) -> None: ...
    def upsert_edge(self, source: str, target: str, edge_type: str, valid_from: str,
                    valid_to: str | None = None, attributes: dict[str, Any] | None = None) -> str: ...
    def end_edges(self, source: str, edge_type: str, valid_to: str) -> int: ...
    def snapshot(self, as_of: str | date | None) -> dict[str, list[dict[str, Any]]]: ...
    def run_gql(self, gql: str, params: dict[str, Any]) -> list[dict[str, Any]]: ...


class LocalGraphStore:
    query_language = "SQL (SQLite emulation of GQL hops)"

    def __init__(self, database: str | Path, tenant_id: str = "demo-bank") -> None:
        self.database = str(database)
        self.tenant_id = tenant_id
        self.query_log: list[str] = []
        self._lock = threading.Lock()
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                  tenant_id TEXT NOT NULL, node_id TEXT NOT NULL, node_type TEXT NOT NULL,
                  name TEXT NOT NULL, attributes TEXT NOT NULL, recorded_at TEXT NOT NULL,
                  PRIMARY KEY (tenant_id, node_id));
                CREATE TABLE IF NOT EXISTS edges (
                  tenant_id TEXT NOT NULL, source_id TEXT NOT NULL, edge_type TEXT NOT NULL,
                  target_id TEXT NOT NULL, valid_from TEXT NOT NULL, valid_to TEXT,
                  recorded_at TEXT NOT NULL, superseded_at TEXT, attributes TEXT NOT NULL,
                  PRIMARY KEY (tenant_id, source_id, edge_type, target_id, valid_from, recorded_at));
                CREATE INDEX IF NOT EXISTS edges_by_target ON edges(tenant_id, target_id);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def load_architecture(self, architecture_file: str | Path = ARCHITECTURE_FILE, reset: bool = True) -> None:
        topology = json.loads(Path(architecture_file).read_text())
        with self._lock, self._connect() as conn:
            if reset:
                conn.execute("DELETE FROM edges WHERE tenant_id = ?", (self.tenant_id,))
                conn.execute("DELETE FROM nodes WHERE tenant_id = ?", (self.tenant_id,))
            recorded = _now()
            conn.executemany(
                "INSERT OR REPLACE INTO nodes VALUES (?, ?, ?, ?, ?, ?)",
                [(self.tenant_id, n["id"], n["type"], n["name"], json.dumps(n.get("attributes", {}), sort_keys=True), recorded)
                 for n in topology["nodes"]],
            )
            conn.executemany(
                "INSERT OR REPLACE INTO edges VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                [(self.tenant_id, e["source"], e["type"], e["target"], e.get("valid_from", "2020-01-01"),
                  e.get("valid_to"), recorded, json.dumps(e.get("attributes", {}), sort_keys=True))
                 for e in topology["edges"]],
            )

    def is_empty(self) -> bool:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM nodes WHERE tenant_id = ?", (self.tenant_id,)).fetchone()[0] == 0

    def nodes(self, node_type: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM nodes WHERE tenant_id = ?" + (" AND node_type = ?" if node_type else "") + " ORDER BY node_id"
        params = (self.tenant_id, node_type) if node_type else (self.tenant_id,)
        with self._connect() as conn:
            return [self._node(row) for row in conn.execute(sql, params)]

    def node(self, node_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM nodes WHERE tenant_id = ? AND node_id = ?", (self.tenant_id, node_id)).fetchone()
        return self._node(row) if row else None

    def neighbours(self, ids, edge_types, as_of, direction="out"):
        ids = list(ids)
        if not ids:
            return []
        types = list(edge_types or [])
        near, far = ("source_id", "target_id") if direction == "out" else ("target_id", "source_id")
        sql = (
            f"SELECT e.source_id, e.edge_type, e.target_id, e.valid_from, e.valid_to, e.attributes AS edge_attributes, n.* "
            f"FROM edges e JOIN nodes n ON n.tenant_id = e.tenant_id AND n.node_id = e.{far} "
            f"WHERE e.tenant_id = ? AND e.{near} IN ({','.join('?' * len(ids))}) "
            f"AND e.valid_from <= ? AND COALESCE(e.valid_to, '{OPEN_END}') > ? AND e.superseded_at IS NULL"
        )
        params: list[Any] = [self.tenant_id, *ids, _iso(as_of), _iso(as_of)]
        if types:
            sql += f" AND e.edge_type IN ({','.join('?' * len(types))})"
            params += types
        self.query_log.append(_render(sql, params))
        with self._connect() as conn:
            rows = conn.execute(sql + " ORDER BY e.source_id, e.edge_type, e.target_id", params).fetchall()
        return [{
            "source_id": row["source_id"], "edge_type": row["edge_type"], "target_id": row["target_id"],
            "valid_from": row["valid_from"], "valid_to": row["valid_to"],
            "edge_attributes": json.loads(row["edge_attributes"]), "node": self._node(row),
        } for row in rows]

    def upsert_node(self, node_id, node_type, name, attributes):
        with self._lock, self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO nodes VALUES (?, ?, ?, ?, ?, ?)",
                         (self.tenant_id, node_id, node_type, name, json.dumps(attributes, sort_keys=True), _now()))

    def upsert_edge(self, source, target, edge_type, valid_from, valid_to=None, attributes=None):
        payload = json.dumps(attributes or {}, sort_keys=True)
        with self._lock, self._connect() as conn:
            current = conn.execute(
                "SELECT valid_from, valid_to, attributes, recorded_at FROM edges WHERE tenant_id = ? AND source_id = ? "
                "AND edge_type = ? AND target_id = ? AND superseded_at IS NULL",
                (self.tenant_id, source, edge_type, target)).fetchall()
            for row in current:
                if row["valid_from"] == valid_from and row["valid_to"] == valid_to and row["attributes"] == payload:
                    return "unchanged"
            now = _now()
            for row in current:
                conn.execute(
                    "UPDATE edges SET superseded_at = ? WHERE tenant_id = ? AND source_id = ? AND edge_type = ? "
                    "AND target_id = ? AND valid_from = ? AND recorded_at = ?",
                    (now, self.tenant_id, source, edge_type, target, row["valid_from"], row["recorded_at"]))
            conn.execute("INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                         (self.tenant_id, source, edge_type, target, valid_from, valid_to, now, payload))
        return "superseded" if current else "inserted"

    def end_edges(self, source, edge_type, valid_to):
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE edges SET valid_to = ? WHERE tenant_id = ? AND source_id = ? AND edge_type = ? "
                "AND superseded_at IS NULL AND valid_to IS NULL", (valid_to, self.tenant_id, source, edge_type))
            return cursor.rowcount

    def edge_history(self, source: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM edges WHERE tenant_id = ?" + (" AND source_id = ?" if source else "")
        params = (self.tenant_id, source) if source else (self.tenant_id,)
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(sql + " ORDER BY recorded_at", params)]

    def snapshot(self, as_of):
        nodes = self.nodes()
        edges = self.neighbours([n["id"] for n in nodes], None, as_of)
        return {"nodes": nodes, "edges": [
            {"source": e["source_id"], "target": e["target_id"], "type": e["edge_type"],
             "valid_from": e["valid_from"], "valid_to": e["valid_to"]} for e in edges]}

    def run_gql(self, gql, params):
        raise NotImplementedError("Raw GQL needs GRAPH_BACKEND=gcp (Spanner Graph); use traverse_blast_radius locally.")

    @staticmethod
    def _node(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["node_id"], "type": row["node_type"], "name": row["name"], "attributes": json.loads(row["attributes"])}


class SpannerGraphStore:
    """Spanner Graph backend. Hop queries are GQL against ``BankArchitecture``."""

    query_language = "GQL (Spanner Graph)"

    def __init__(self, settings: Settings) -> None:
        settings.require("project", "spanner_instance", "spanner_database")
        from google.cloud import spanner  # imported lazily: optional at local runtime

        self._spanner = spanner
        self.tenant_id = settings.tenant_id
        self.query_log: list[str] = []
        self.database = spanner.Client(project=settings.project).instance(settings.spanner_instance).database(settings.spanner_database)

    def _read(self, sql: str, params: dict[str, Any], types: dict[str, Any]) -> list[dict[str, Any]]:
        self.query_log.append(sql.strip())
        with self.database.snapshot() as snapshot:
            result = snapshot.execute_sql(sql, params=params, param_types=types)
            rows = list(result)
            fields = [field.name for field in result.fields]
        return [dict(zip(fields, row)) for row in rows]

    def nodes(self, node_type=None):
        from google.cloud.spanner_v1 import param_types as T
        sql = "SELECT node_id, node_type, name, attributes FROM Nodes WHERE tenant_id = @tenant_id"
        params, types = {"tenant_id": self.tenant_id}, {"tenant_id": T.STRING}
        if node_type:
            sql += " AND node_type = @node_type"
            params["node_type"], types["node_type"] = node_type, T.STRING
        return [self._node(row) for row in self._read(sql + " ORDER BY node_id", params, types)]

    def node(self, node_id):
        from google.cloud.spanner_v1 import param_types as T
        rows = self._read("SELECT node_id, node_type, name, attributes FROM Nodes WHERE tenant_id = @tenant_id AND node_id = @node_id",
                          {"tenant_id": self.tenant_id, "node_id": node_id}, {"tenant_id": T.STRING, "node_id": T.STRING})
        return self._node(rows[0]) if rows else None

    def neighbours(self, ids, edge_types, as_of, direction="out"):
        from google.cloud.spanner_v1 import param_types as T
        ids = list(ids)
        if not ids:
            return []
        anchor, other = ("s", "t") if direction == "out" else ("t", "s")
        gql = f"""
GRAPH BankArchitecture
MATCH (s:Entity)-[e:Relationship]->(t:Entity)
WHERE e.tenant_id = @tenant_id AND {anchor}.node_id IN UNNEST(@ids)
  AND (ARRAY_LENGTH(@edge_types) = 0 OR e.edge_type IN UNNEST(@edge_types))
  AND e.valid_from <= @as_of AND (e.valid_to IS NULL OR e.valid_to > @as_of)
  AND e.superseded_at IS NULL
RETURN s.node_id AS source_id, e.edge_type AS edge_type, t.node_id AS target_id,
       CAST(e.valid_from AS STRING) AS valid_from, CAST(e.valid_to AS STRING) AS valid_to,
       SAFE.TO_JSON_STRING(e.attributes) AS edge_attributes,
       {other}.node_id AS node_id, {other}.node_type AS node_type, {other}.name AS name,
       SAFE.TO_JSON_STRING({other}.attributes) AS attributes"""
        rows = self._read(gql, {
            "tenant_id": self.tenant_id, "ids": ids, "edge_types": list(edge_types or []),
            "as_of": date.fromisoformat(_iso(as_of)),
        }, {"tenant_id": T.STRING, "ids": T.Array(T.STRING), "edge_types": T.Array(T.STRING), "as_of": T.DATE})
        return [{
            "source_id": r["source_id"], "edge_type": r["edge_type"], "target_id": r["target_id"],
            "valid_from": r["valid_from"], "valid_to": r["valid_to"],
            "edge_attributes": json.loads(r["edge_attributes"] or "{}"), "node": self._node(r),
        } for r in rows]

    def upsert_node(self, node_id, node_type, name, attributes):
        with self.database.batch() as batch:
            batch.insert_or_update("Nodes", ("tenant_id", "node_id", "node_type", "name", "attributes", "recorded_at"),
                                   [(self.tenant_id, node_id, node_type, name, json.dumps(attributes), self._spanner.COMMIT_TIMESTAMP)])

    def upsert_edge(self, source, target, edge_type, valid_from, valid_to=None, attributes=None):
        from google.cloud.spanner_v1 import param_types as T
        payload = json.dumps(attributes or {}, sort_keys=True)
        outcome = {"value": "inserted"}

        def work(tx):
            key = {"tenant_id": self.tenant_id, "source_id": source, "edge_type": edge_type, "target_id": target}
            key_types = {name: T.STRING for name in key}
            current = list(tx.execute_sql(
                "SELECT CAST(valid_from AS STRING), CAST(valid_to AS STRING), TO_JSON_STRING(attributes), recorded_at FROM Edges "
                "WHERE tenant_id = @tenant_id AND source_id = @source_id AND edge_type = @edge_type "
                "AND target_id = @target_id AND superseded_at IS NULL", params=key, param_types=key_types))
            for vf, vt, attrs, _ in current:
                if vf == valid_from and vt == valid_to and json.dumps(json.loads(attrs or "{}"), sort_keys=True) == payload:
                    outcome["value"] = "unchanged"
                    return
            if current:
                outcome["value"] = "superseded"
                tx.execute_update(
                    "UPDATE Edges SET superseded_at = PENDING_COMMIT_TIMESTAMP() WHERE tenant_id = @tenant_id AND "
                    "source_id = @source_id AND edge_type = @edge_type AND target_id = @target_id AND superseded_at IS NULL",
                    params=key, param_types=key_types)
            tx.insert("Edges", ("tenant_id", "source_id", "edge_type", "target_id", "valid_from", "valid_to",
                                "recorded_at", "attributes"),
                      [(self.tenant_id, source, edge_type, target, date.fromisoformat(valid_from),
                        date.fromisoformat(valid_to) if valid_to else None, self._spanner.COMMIT_TIMESTAMP, payload)])

        self.database.run_in_transaction(work)
        return outcome["value"]

    def end_edges(self, source, edge_type, valid_to):
        from google.cloud.spanner_v1 import param_types as T

        def work(tx):
            return tx.execute_update(
                "UPDATE Edges SET valid_to = @valid_to WHERE tenant_id = @tenant_id AND source_id = @source_id "
                "AND edge_type = @edge_type AND superseded_at IS NULL AND valid_to IS NULL",
                params={"valid_to": date.fromisoformat(valid_to), "tenant_id": self.tenant_id, "source_id": source, "edge_type": edge_type},
                param_types={"valid_to": T.DATE, "tenant_id": T.STRING, "source_id": T.STRING, "edge_type": T.STRING})

        return self.database.run_in_transaction(work)

    def snapshot(self, as_of):
        nodes = self.nodes()
        edges = self.neighbours([n["id"] for n in nodes], None, as_of)
        return {"nodes": nodes, "edges": [
            {"source": e["source_id"], "target": e["target_id"], "type": e["edge_type"],
             "valid_from": e["valid_from"], "valid_to": e["valid_to"]} for e in edges]}

    def run_gql(self, gql, params):
        """Execute a guard-validated, read-only GQL query with tenant binding."""
        from google.cloud.spanner_v1 import param_types as T
        bound = {"tenant_id": self.tenant_id, **{k: v for k, v in params.items() if k != "tenant_id"}}
        types = {k: (T.Array(T.STRING) if isinstance(v, list) else T.INT64 if isinstance(v, int) else T.STRING)
                 for k, v in bound.items()}
        return self._read(gql, bound, types)

    @staticmethod
    def _node(row: dict[str, Any]) -> dict[str, Any]:
        raw = row.get("attributes")
        attributes = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        return {"id": row["node_id"], "type": row["node_type"], "name": row["name"], "attributes": attributes}


def _render(sql: str, params: list[Any]) -> str:
    rendered = " ".join(sql.split())
    for value in params:
        rendered = rendered.replace("?", repr(value), 1)
    return rendered


_STORE: GraphStore | None = None


def get_graph_store(settings: Settings | None = None) -> GraphStore:
    """Process-wide graph store for the configured backend."""
    global _STORE
    settings = settings or get_settings()
    if _STORE is None or _STORE.tenant_id != settings.tenant_id:
        if settings.graph_backend == "gcp":
            _STORE = SpannerGraphStore(settings)
        else:
            store = LocalGraphStore(settings.runtime_dir / "graph.db", settings.tenant_id)
            if store.is_empty():
                store.load_architecture()
            _STORE = store
    return _STORE


def set_graph_store(store: GraphStore | None) -> None:
    global _STORE
    _STORE = store
