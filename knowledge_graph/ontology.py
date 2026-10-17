"""Ontology: a small meta-model in code plus domain vocabulary loaded from packs.

*Meta-model (code, stable)*: node types, edge types, the PII column denylist and the regulated-entity terms.

*Domain vocabulary (data)*: capabilities, record flags, event types, product attributes, sanitized views, the regulatory
glossary, workflows and process maps live in ``data/ontology/packs/<pack_id>.json`` (see ``knowledge_graph/packs.py``
and ``docs/ontology.md``). A bank enables packs with ``ontology_packs`` in ``data/bank/entity_profile.json``.

Every name that older code imports (``CAPABILITIES``, ``RECORD_FLAGS``, ...) is still exported. They are computed once at
import time from the default profile's enabled packs. Code that must follow a custom profile, or pick up vocabulary
promoted while the process runs, calls ``get_ontology(profile)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from knowledge_graph import packs as _packs

# Meta-model: stable, in code ------------------------------------------------------------------------------------
NODE_TYPES = {
    "Policy": "Regulatory or internal policy document (BIAN: Regulatory Compliance).",
    "Rule": "Actionable clause extracted from a policy, with executable checks.",
    "Product": "Banking product (BIAN: Product Directory / Consumer Loan).",
    "Process": "Business process mapped to a BIAN service domain.",
    "Control": "Capability or control a product provides to satisfy obligations.",
    "CustomerSegment": "Abstract customer grouping; never individual customers.",
    "Team": "Accountable owner of a product or process.",
    "Branch": "Operational unit; manager contact details stay in the PII vault.",
}

# Policy nodes carry an attribute `kind`: a regulator's circular or an internal policy of the bank.
POLICY_KINDS = ("regulatory", "internal")

EDGE_TYPES = {
    "CONTAINS": ("Policy", "Rule"),
    "SUPERSEDES": ("Policy", "Policy"),
    "AMENDS": ("Policy", "Policy"),  # circular lineage: this policy amends an earlier one, which stays in force
    "REFERS_TO": ("Policy", "Policy"),  # circular lineage: this policy cites another without changing it
    "IMPLEMENTS": ("Policy", "Rule"),  # an internal policy clause implements a regulatory rule
    "ENFORCES": ("Policy", "Control"),  # an internal policy enforces a control
    "GOVERNS": ("Rule", "Product"),
    "APPLIES_TO": ("Rule", "Process"),
    "TARGETS": ("Rule", "CustomerSegment"),
    "REQUIRES": ("Rule", "Control"),
    "USES": ("Product", "Process"),
    "PROVIDES": ("Product", "Control"),
    "OWNED_BY": ("Product|Process", "Team"),
    "ELIGIBLE_FOR": ("CustomerSegment", "Product"),
    "OFFERED_AT": ("Product", "Branch"),
}

# Columns that must never reach the LLM even if a view accidentally exposes them.
PII_COLUMNS = {
    "customer_id", "full_name", "name", "email", "phone", "mobile", "pan", "aadhaar",
    "address", "account_number", "manager_name", "manager_email", "date_of_birth", "dob",
}

# Phrases naming the regulated entity itself (the bank, its licence class), not a product or a
# customer segment. They must never resolve to a CustomerSegment or Product node.
REGULATED_ENTITY_TERMS = {
    "bank", "banks", "commercial bank", "commercial banks", "scheduled commercial bank", "scheduled commercial banks",
    "authorised dealer", "authorised dealers", "authorized dealer", "authorized dealers", "ad bank", "ad banks",
    "authorised dealer category i", "authorised dealer category i banks", "nbfc", "nbfcs",
    "regulated entity", "regulated entities", "lender", "lenders", "cooperative bank", "cooperative banks",
    "co operative bank", "co operative banks", "small finance bank", "small finance banks", "payments bank",
    "payments banks", "primary dealer", "primary dealers", "financial institution", "financial institutions",
}


# Domain vocabulary: merged from the enabled packs -----------------------------------------------------------------
@dataclass(frozen=True)
class Ontology:
    """The vocabulary of one bank: meta-model plus the merged content of its enabled packs."""

    capabilities: dict[str, str]  # capability id -> canonical Control node id
    capability_specs: dict[str, _packs.CapabilitySpec]
    record_flags: dict[str, str]
    event_types: frozenset[str]
    product_attributes: tuple[str, ...]
    sanitized_views: dict[str, dict]
    view_domains: dict[str, str]
    glossary: dict[str, dict]
    event_processes: dict[str, str]
    capability_processes: dict[str, str]
    workflows: dict[str, str]
    capability_domains: dict[str, frozenset[str]]
    flag_domains: dict[str, frozenset[str]]
    event_domains: dict[str, frozenset[str]]
    packs: tuple[tuple[str, str], ...]  # (pack_id, version) in merge order
    node_types: dict[str, str]
    edge_types: dict[str, tuple[str, str]]
    pii_columns: frozenset[str]
    regulated_entity_terms: frozenset[str]

    def control_capability(self, control_id: str) -> str | None:
        return next((cap for cap, control in self.capabilities.items() if control == control_id), None)

    def summary(self, domain: str = "All") -> dict:
        """Schema-only view returned to agents by get_graph_ontology."""
        return {
            "domain": domain,
            "node_labels": NODE_TYPES,
            "edge_types": {name: f"{src} -> {dst}" for name, (src, dst) in EDGE_TYPES.items()},
            "capabilities": self.capabilities,
            "record_flags": self.record_flags,
            "event_types": sorted(self.event_types),
            "product_attributes": self.product_attributes,
            "packs": [{"pack_id": pack_id, "version": version} for pack_id, version in self.packs],
            "temporal_model": "Edges carry valid_from/valid_to (business time) and recorded_at/superseded_at (system time).",
        }


def build_ontology(enabled: list[_packs.OntologyPack]) -> Ontology:
    merged = _packs.merge_packs(enabled)
    return Ontology(
        capabilities=merged.capabilities, capability_specs=merged.capability_specs, record_flags=merged.record_flags,
        event_types=merged.event_types, product_attributes=merged.product_attributes, sanitized_views=merged.views,
        view_domains=merged.view_domains, glossary=merged.glossary, event_processes=merged.event_processes,
        capability_processes=merged.capability_processes, workflows=merged.workflows,
        capability_domains=merged.capability_domains, flag_domains=merged.flag_domains, event_domains=merged.event_domains,
        packs=merged.packs, node_types=NODE_TYPES, edge_types=EDGE_TYPES, pii_columns=frozenset(PII_COLUMNS),
        regulated_entity_terms=frozenset(REGULATED_ENTITY_TERMS),
    )


def get_ontology(profile: dict | None = None, pack_dir: str | Path | None = None, tenant_ids=()) -> Ontology:
    """Vocabulary for ``profile`` (default: ``data/bank/entity_profile.json``). Follows pack files as they change."""
    return build_ontology(_packs.enabled_packs(profile, pack_dir, tenant_ids))


_DEFAULT = get_ontology()

# Public names kept for existing importers; computed from the default profile's enabled packs at import time.
# Capability id used in ComplianceCheck.capability -> canonical Control node.
CAPABILITIES = _DEFAULT.capabilities
# Boolean fields on the compliance views that a record_flag check may test.
RECORD_FLAGS = _DEFAULT.record_flags
# Event types available on the event views.
EVENT_TYPES = set(_DEFAULT.event_types)
# Product attribute filters a rule's ProductScope may use.
PRODUCT_ATTRIBUTES = _DEFAULT.product_attributes
# Operational views the secure engine (and LLM-written SQL) may read.
SANITIZED_VIEWS = _DEFAULT.sanitized_views
# Regulatory terms of art that denote product *attributes*, not products.
# RBI's "personal loans" means all loans to individuals, not the bank's product literally named "Personal Loan".
REGULATORY_GLOSSARY = _DEFAULT.glossary
# Operational event -> process that emits it, used when a rule names no process.
EVENT_PROCESSES = _DEFAULT.event_processes
CAPABILITY_PROCESSES = _DEFAULT.capability_processes
# Remediation workflows the Orchestrator may propose (never auto-executed).
WORKFLOWS = _DEFAULT.workflows


def ontology_summary(domain: str = "All", profile: dict | None = None) -> dict:
    """Schema-only view returned to agents by get_graph_ontology (live: follows pack edits and ``profile``)."""
    return get_ontology(profile).summary(domain)
