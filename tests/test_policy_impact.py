import tempfile
import unittest
from pathlib import Path

from agents.compliance_coordinator.sub_agents.analyzer.service import ImpactAnalyser
from agents.compliance_coordinator.sub_agents.mapper.service import PolicyMapper
from ingestion.circular_parser import parse_floating_rate_reset_circular
from knowledge_graph.store import BankKnowledgeGraph


ROOT = Path(__file__).resolve().parents[1]
CIRCULAR = ROOT / "data" / "circulars" / "rbi_2023_08_18_floating_rate_reset_emi_loans.md"
ARCHITECTURE = ROOT / "data" / "bank" / "architecture.json"


class PolicyImpactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.graph = BankKnowledgeGraph(Path(self.tmp.name) / "graph.db")
        self.graph.initialise(ARCHITECTURE)
        self.rules = parse_floating_rate_reset_circular(CIRCULAR)

    def tearDown(self):
        self.tmp.cleanup()

    def test_parser_extracts_auditable_requirements(self):
        self.assertEqual(3, len(self.rules))
        self.assertIn("fixed_rate_option", self.rules[1].requirements)
        self.assertEqual("DOR.MCS.REC.32/01.01.003/2023-24", self.rules[0].circular_id)

    def test_mapper_finds_only_floating_emi_products_and_control_gap(self):
        mappings = [PolicyMapper(self.graph).map_rule(rule) for rule in self.rules]
        products = set(mappings[1].product_ids)
        self.assertEqual({"product:personal-loan-floating", "product:home-loan-floating", "product:auto-loan-floating"}, products)
        self.assertEqual(["fixed_rate_option"], mappings[1].missing_capabilities["product:auto-loan-floating"])

    def test_analyser_calculates_blast_radius_from_read_only_fixture(self):
        mappings = [PolicyMapper(self.graph).map_rule(rule) for rule in self.rules]
        report = ImpactAnalyser(graph=self.graph).analyse(mappings)
        self.assertEqual(10, report.summary["active_floating_rate_loans"])
        self.assertEqual(6, report.summary["customers_to_notify"])
        self.assertEqual(3, report.summary["loans_without_fixed_rate_option"])
        self.assertIn("product:auto-loan-floating", report.product_gaps)


if __name__ == "__main__":
    unittest.main()
