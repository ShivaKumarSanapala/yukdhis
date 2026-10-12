"""Local, deterministic graph store used by the prototype and tests.

The schema mirrors the labels/edges deployed to Spanner Graph.  SQLite keeps
the demo runnable without cloud credentials; set BANK_GRAPH_BACKEND=spanner in
deployment to use the matching cloud graph schema.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "bank" / "bank_graph.db"
ARCHITECTURE_FILE = ROOT / "data" / "bank" / "architecture.json"


class BankKnowledgeGraph:
    def __init__(self, database: str | Path = DEFAULT_DB) -> None:
        self.database = str(database)

    @classmethod
    def from_environment(cls) -> "BankKnowledgeGraph | SpannerBankKnowledgeGraph":
        """Select the local demo store or production Spanner read adapter."""
        if os.getenv("BANK_GRAPH_BACKEND", "sqlite").lower() == "spanner":
            return SpannerBankKnowledgeGraph.from_environment()
        return cls(os.getenv("BANK_GRAPH_SQLITE_PATH", str(DEFAULT_DB)))

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        return conn

    def initialise(self, architecture_file: str | Path = ARCHITECTURE_FILE) -> None:
        architecture = json.loads(Path(architecture_file).read_text())
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                DROP TABLE IF EXISTS edges;
                DROP TABLE IF EXISTS nodes;
                CREATE TABLE nodes (
                  node_id TEXT PRIMARY KEY, node_type TEXT NOT NULL,
                  name TEXT NOT NULL, attributes_json TEXT NOT NULL
                );
                CREATE TABLE edges (
                  source_id TEXT NOT NULL, target_id TEXT NOT NULL,
                  edge_type TEXT NOT NULL, attributes_json TEXT NOT NULL,
                  PRIMARY KEY (source_id, target_id, edge_type)
                );
                CREATE INDEX edges_source ON edges(source_id);
                CREATE INDEX edges_target ON edges(target_id);
                """
            )
            conn.executemany(
                "INSERT INTO nodes VALUES (:id, :type, :name, :attributes)",
                [
                    {**node, "attributes": json.dumps(node.get("attributes", {}), sort_keys=True)}
                    for node in architecture["nodes"]
                ],
            )
            conn.executemany(
                "INSERT INTO edges VALUES (:source, :target, :type, :attributes)",
                [
                    {**edge, "attributes": json.dumps(edge.get("attributes", {}), sort_keys=True)}
                    for edge in architecture["edges"]
                ],
            )

    def nodes(self, node_type: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM nodes" + (" WHERE node_type = ?" if node_type else "")
        params: Iterable[str] = (node_type,) if node_type else ()
        with self.connect() as conn:
            return [self._node(row) for row in conn.execute(query, params)]

    def node(self, node_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM nodes WHERE node_id = ?", (node_id,)).fetchone()
        return self._node(row) if row else None

    def related(self, node_id: str, edge_type: str | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT n.*, e.edge_type FROM edges e JOIN nodes n ON n.node_id = e.target_id
            WHERE e.source_id = ?
        """
        params: list[str] = [node_id]
        if edge_type:
            sql += " AND e.edge_type = ?"
            params.append(edge_type)
        with self.connect() as conn:
            return [{**self._node(row), "edge_type": row["edge_type"]} for row in conn.execute(sql, params)]

    def products_for_terms(self, terms: set[str]) -> list[dict[str, Any]]:
        products = self.nodes("Product")
        matches = []
        for product in products:
            searchable = " ".join([product["name"], *product["attributes"].get("keywords", [])]).lower()
            if terms.intersection(searchable.split()) or {"floating", "emi"}.issubset(terms):
                matches.append(product)
        return matches

    @staticmethod
    def _node(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["node_id"], "type": row["node_type"], "name": row["name"],
            "attributes": json.loads(row["attributes_json"]),
        }


class SpannerBankKnowledgeGraph:
    """Read-only adapter for the equivalent Spanner Graph topology.

    The mapper keeps its deterministic matching logic; this adapter changes
    only persistence.  It reads the graph backing tables, while the
    `BankArchitecture` property graph remains available for GQL exploration.
    """

    def __init__(self, project: str, instance: str, database: str) -> None:
        try:
            from google.cloud import spanner  # type: ignore
        except ImportError as error:  # pragma: no cover - needs optional cloud dependency
            raise RuntimeError("Install google-cloud-spanner for BANK_GRAPH_BACKEND=spanner") from error
        self.project, self.instance_id, self.database_id = project, instance, database
        self.database = f"spanner://{project}/{instance}/{database}"
        self._database = spanner.Client(project=project).instance(instance).database(database)

    @classmethod
    def from_environment(cls) -> "SpannerBankKnowledgeGraph":
        project = os.getenv("GOOGLE_CLOUD_PROJECT")
        instance = os.getenv("SPANNER_INSTANCE")
        database = os.getenv("SPANNER_DATABASE")
        if not all((project, instance, database)):
            raise RuntimeError("SPANNER_INSTANCE, SPANNER_DATABASE, and GOOGLE_CLOUD_PROJECT are required for Spanner Graph")
        return cls(project, instance, database)

    def nodes(self, node_type: str | None = None) -> list[dict[str, Any]]:
        rows = self._query("SELECT node_id, node_type, name, attributes FROM Nodes")
        result = [self._as_node(row) for row in rows]
        return [node for node in result if not node_type or node["type"] == node_type]

    def node(self, node_id: str) -> dict[str, Any] | None:
        # Parameter binding avoids query-text injection when product IDs are external input.
        rows = self._query(
            "SELECT node_id, node_type, name, attributes FROM Nodes WHERE node_id = @node_id",
            {"node_id": node_id},
        )
        return self._as_node(rows[0]) if rows else None

    def related(self, node_id: str, edge_type: str | None = None) -> list[dict[str, Any]]:
        rows = self._query(
            """SELECT n.node_id, n.node_type, n.name, n.attributes, e.edge_type
               FROM Edges e JOIN Nodes n ON n.node_id = e.target_id
               WHERE e.source_id = @node_id""",
            {"node_id": node_id},
        )
        result = [{**self._as_node(row), "edge_type": row[4]} for row in rows]
        return [node for node in result if not edge_type or node["edge_type"] == edge_type]

    def products_for_terms(self, terms: set[str]) -> list[dict[str, Any]]:
        products = self.nodes("Product")
        return [
            product for product in products
            if terms.intersection(" ".join([product["name"], *product["attributes"].get("keywords", [])]).lower().split())
            or {"floating", "emi"}.issubset(terms)
        ]

    def _query(self, sql: str, params: dict[str, str] | None = None) -> list[tuple[Any, ...]]:
        from google.cloud.spanner_v1 import param_types  # type: ignore
        with self._database.snapshot() as snapshot:
            return list(snapshot.execute_sql(
                sql, params=params or {},
                param_types={key: param_types.STRING for key in (params or {})},
            ))

    @staticmethod
    def _as_node(row: tuple[Any, ...]) -> dict[str, Any]:
        raw_attributes = row[3]
        attributes = json.loads(raw_attributes) if isinstance(raw_attributes, str) else (raw_attributes or {})
        return {"id": row[0], "type": row[1], "name": row[2], "attributes": attributes}
