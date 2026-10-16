"""Load the versioned BIAN topology into Spanner Graph for one tenant (replaces that tenant's rows)."""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    from google.cloud import spanner

    tenant = os.getenv("TENANT_ID", "demo-bank")
    topology = json.loads((ROOT / "data" / "bank" / "architecture.json").read_text())
    database = spanner.Client(project=os.environ["GOOGLE_CLOUD_PROJECT"]).instance(os.environ["SPANNER_INSTANCE"]).database(
        os.environ["SPANNER_DATABASE"])
    database.execute_partitioned_dml("DELETE FROM Edges WHERE tenant_id = @t", params={"t": tenant},
                                     param_types={"t": spanner.param_types.STRING})
    database.execute_partitioned_dml("DELETE FROM Nodes WHERE tenant_id = @t", params={"t": tenant},
                                     param_types={"t": spanner.param_types.STRING})
    with database.batch() as batch:
        batch.insert("Nodes", ("tenant_id", "node_id", "node_type", "name", "attributes", "recorded_at"), [
            (tenant, n["id"], n["type"], n["name"], json.dumps(n.get("attributes", {})), spanner.COMMIT_TIMESTAMP)
            for n in topology["nodes"]])
        batch.insert("Edges", ("tenant_id", "source_id", "edge_type", "target_id", "valid_from", "valid_to", "recorded_at", "attributes"), [
            (tenant, e["source"], e["type"], e["target"], date.fromisoformat(e.get("valid_from", "2020-01-01")),
             date.fromisoformat(e["valid_to"]) if e.get("valid_to") else None, spanner.COMMIT_TIMESTAMP,
             json.dumps(e.get("attributes", {}))) for e in topology["edges"]])
    print(f"Seeded {len(topology['nodes'])} nodes / {len(topology['edges'])} edges for tenant {tenant}.", file=sys.stderr)


if __name__ == "__main__":
    main()
