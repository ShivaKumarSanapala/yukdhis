"""Read-only impact analysis over mapper output and operational loan records."""

from __future__ import annotations

from datetime import datetime, timezone

from bank_mcp_server.repository import ReadOnlyBankRepository
from knowledge_graph.models import ImpactReport, MappingResult
from knowledge_graph.store import BankKnowledgeGraph


class ImpactAnalyser:
    def __init__(self, repository: ReadOnlyBankRepository | None = None, graph: BankKnowledgeGraph | None = None) -> None:
        self.repository = repository or ReadOnlyBankRepository()
        self.graph = graph or BankKnowledgeGraph.from_environment()

    def analyse(self, mappings: list[MappingResult]) -> ImpactReport:
        product_ids = sorted({product for mapping in mappings for product in mapping.product_ids})
        loans = self.repository.active_floating_loans(product_ids)
        product_gaps: dict[str, list[str]] = {}
        for mapping in mappings:
            for product, gaps in mapping.missing_capabilities.items():
                product_gaps.setdefault(product, [])
                product_gaps[product].extend(gap for gap in gaps if gap not in product_gaps[product])

        products = [self.graph.node(product_id) for product_id in product_ids]
        unnotified = sum(not loan["borrower_notified"] for loan in loans)
        missing_statement = sum(not loan["statement_available"] for loan in loans)
        missing_fixed_option = sum(not loan["has_fixed_rate_option"] for loan in loans)
        missing_emi_tenor = sum(not loan["has_emi_tenor_choice"] for loan in loans)
        summary = {
            "active_floating_rate_loans": len(loans),
            "customers_to_notify": unnotified,
            "loans_without_reset_statement": missing_statement,
            "loans_without_fixed_rate_option": missing_fixed_option,
            "loans_without_emi_or_tenor_choice": missing_emi_tenor,
        }
        actions = []
        if unnotified:
            actions.append(f"Create review worklist for {unnotified} borrower notification(s).")
        if missing_statement:
            actions.append(f"Remediate reset statements for {missing_statement} loan(s).")
        if missing_fixed_option:
            actions.append(f"Escalate fixed-rate conversion gap affecting {missing_fixed_option} loan(s).")
        if product_gaps:
            actions.append("Ask product owners to approve remediation for graph-identified control gaps.")
        merged_rule = mappings[0].rule
        return ImpactReport(
            circular_id=merged_rule.circular_id,
            analysed_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            affected_products=[product for product in products if product],
            impacted_loans=loans, summary=summary, product_gaps=product_gaps,
            required_actions=actions, mapping_evidence=mappings[0],
            limitations=[
                "Synthetic non-PII data only; production queries must enforce entitlements and audit logging.",
                "This is a decision-support report and requires compliance-owner review before action.",
            ],
        )
