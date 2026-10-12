"""Run the exact parser -> mapper -> analyser flow used in the demo."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.compliance_coordinator.sub_agents.analyzer.service import ImpactAnalyser
from agents.compliance_coordinator.sub_agents.mapper.service import PolicyMapper
from ingestion.circular_parser import parse_floating_rate_reset_circular
from knowledge_graph.store import BankKnowledgeGraph


DEFAULT_CIRCULAR = ROOT / "data" / "circulars" / "rbi_2023_08_18_floating_rate_reset_emi_loans.md"


def run(circular: Path, reset_graph: bool = True) -> dict:
    graph = BankKnowledgeGraph.from_environment()
    if isinstance(graph, BankKnowledgeGraph) and (reset_graph or not Path(graph.database).exists()):
        graph.initialise()
    rules = parse_floating_rate_reset_circular(circular)
    mappings = [PolicyMapper(graph).map_rule(rule) for rule in rules]
    report = ImpactAnalyser(graph=graph).analyse(mappings)
    return {"parsed_rules": [rule.to_dict() for rule in rules], "mappings": [mapping.to_dict() for mapping in mappings], "impact_report": report.to_dict()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the synthetic bank policy impact demo.")
    parser.add_argument("--circular", type=Path, default=DEFAULT_CIRCULAR)
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    args = parser.parse_args()
    result = run(args.circular)
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")
    print(rendered)


if __name__ == "__main__":
    main()
