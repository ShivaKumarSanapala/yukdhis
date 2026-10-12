"""Maps a structured regulatory rule to the internal bank architecture graph."""

from __future__ import annotations

from collections import defaultdict

from knowledge_graph.models import MappingResult, RegulatoryRule
from knowledge_graph.store import BankKnowledgeGraph


CAPABILITY_NODES = {
    "emi_or_tenor_choice": "control:emi-tenor-choice",
    "fixed_rate_option": "control:fixed-rate-option",
    "reset_statement": "control:reset-statement",
}


class PolicyMapper:
    def __init__(self, graph: BankKnowledgeGraph | None = None) -> None:
        self.graph = graph or BankKnowledgeGraph.from_environment()

    def map_rule(self, rule: RegulatoryRule) -> MappingResult:
        terms = {term.lower() for term in " ".join((*rule.applies_to, rule.title)).replace("_", " ").split()}
        products = [
            product for product in self.graph.products_for_terms(terms)
            if product["attributes"].get("rate_type") == "FLOATING"
            and product["attributes"].get("instalment_based")
        ]
        processes: set[str] = set()
        controls: set[str] = set()
        missing: dict[str, list[str]] = defaultdict(list)
        paths: list[list[str]] = []
        for product in products:
            linked = self.graph.related(product["id"])
            provided = {item["id"] for item in linked if item["edge_type"] == "PROVIDES"}
            used = {item["id"] for item in linked if item["edge_type"] == "USES"}
            processes.update(used)
            controls.update(provided)
            for requirement in rule.requirements:
                if requirement == "borrower_notification":
                    present = "process:borrower-notification" in used
                    target = "process:borrower-notification"
                else:
                    target = CAPABILITY_NODES[requirement]
                    present = target in provided
                paths.append([rule.rule_id, product["id"], target])
                if not present:
                    missing[product["id"]].append(requirement)
        return MappingResult(
            rule=rule, product_ids=[product["id"] for product in products],
            process_ids=sorted(processes), control_ids=sorted(controls),
            missing_capabilities=dict(missing), paths=paths,
            rationale=[
                "Selected active product topology where rate_type=FLOATING and instalment_based=true.",
                "Capability checks use explicit graph edges; no LLM inference determines compliance status.",
            ],
        )
