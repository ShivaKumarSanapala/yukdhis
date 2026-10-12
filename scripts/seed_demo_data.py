"""Create or refresh the local knowledge-graph database from versioned fixtures."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from knowledge_graph.store import BankKnowledgeGraph


def main() -> None:
    graph = BankKnowledgeGraph()
    graph.initialise()
    print(f"Seeded {len(graph.nodes())} architecture nodes in {graph.database}")


if __name__ == "__main__":
    main()
