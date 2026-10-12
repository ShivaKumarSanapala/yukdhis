"""Load the versioned synthetic topology into an already-created Spanner database."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    from google.cloud import spanner

    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    instance_id = os.environ["SPANNER_INSTANCE"]
    database_id = os.environ["SPANNER_DATABASE"]
    topology = json.loads((ROOT / "data" / "bank" / "architecture.json").read_text())
    database = spanner.Client(project=project).instance(instance_id).database(database_id)
    with database.batch() as batch:
        batch.delete("Edges", spanner.KeySet(all_=True))
        batch.delete("Nodes", spanner.KeySet(all_=True))
        batch.insert("Nodes", ("node_id", "node_type", "name", "attributes"), [
            (node["id"], node["type"], node["name"], json.dumps(node.get("attributes", {})))
            for node in topology["nodes"]
        ])
        batch.insert("Edges", ("source_id", "target_id", "edge_type", "attributes"), [
            (edge["source"], edge["target"], edge["type"], json.dumps(edge.get("attributes", {})))
            for edge in topology["edges"]
        ])
    print(f"Seeded {len(topology['nodes'])} graph nodes and {len(topology['edges'])} edges into {database_id}.")


if __name__ == "__main__":
    main()
