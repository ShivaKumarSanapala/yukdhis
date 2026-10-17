"""Canonical BIAN-aligned ontology shared by every tenant.

The engine reasons over this fixed vocabulary.  Each bank adapter maps its own
core-banking product codes and processes onto these labels, which is what lets
one deployment serve many banks.
"""

from __future__ import annotations

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

EDGE_TYPES = {
    "CONTAINS": ("Policy", "Rule"),
    "SUPERSEDES": ("Policy", "Policy"),
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

# Capability id used in ComplianceCheck.capability -> canonical Control node.
CAPABILITIES = {
    "borrower_notification": "control:borrower-notification",
    "emi_or_tenor_choice": "control:emi-tenor-choice",
    "fixed_rate_option": "control:fixed-rate-option",
    "reset_statement": "control:reset-statement",
    "document_release_sla": "control:document-release-sla",
    "delay_compensation": "control:delay-compensation",
    "document_collection_choice": "control:document-collection-choice",
}

# Boolean fields on vw_compliance_impact that a record_flag check may test.
RECORD_FLAGS = {
    "borrower_notified": "Borrower was told the EMI/tenor impact of the last reset.",
    "has_fixed_rate_option": "Loan offers a switch to a fixed rate.",
    "has_emi_tenor_choice": "Borrower may choose EMI increase or tenor extension.",
    "statement_available": "Reset statement with principal, interest and APR issued.",
}

# Event types available on vw_loan_events.
EVENT_TYPES = {
    "LOAN_DISBURSED", "LOAN_CLOSED", "DOCUMENTS_RELEASED", "RATE_RESET", "RATE_RESET_NOTICE_SENT",
}

# Product attribute filters a rule's ProductScope may use.
PRODUCT_ATTRIBUTES = ("rate_type", "instalment_based", "secured", "collateral_type", "category")

# Operational views the secure engine (and LLM-written SQL) may read.
SANITIZED_VIEWS = {
    "vw_compliance_impact": {
        "description": "One row per loan. Opaque refs only; no names, contact or account numbers.",
        "columns": {
            "loan_ref": "opaque loan reference", "customer_ref": "opaque customer reference",
            "product_id": "canonical product node id", "segment_id": "customer segment node id",
            "branch_id": "branch node id", "status": "ACTIVE | CLOSED", "rate_type": "FLOATING | FIXED",
            "emi_amount": "INR", "outstanding_balance": "INR", "sanctioned_on": "date",
            "closed_on": "date or null", "rate_reset_due": "date or null",
            **{flag: f"boolean - {text}" for flag, text in RECORD_FLAGS.items()},
        },
    },
    "vw_loan_events": {
        "description": "Operational event log per loan, tokenized.",
        "columns": {
            "loan_ref": "opaque loan reference", "customer_ref": "opaque customer reference",
            "product_id": "canonical product node id", "branch_id": "branch node id",
            "segment_id": "customer segment node id", "event_type": "|".join(sorted(EVENT_TYPES)),
            "occurred_on": "date",
        },
    },
    "vw_branch_directory": {
        "description": "Branch metadata without manager contact details.",
        "columns": {"branch_id": "branch node id", "branch_name": "name", "city": "city", "region": "region"},
    },
}

# Columns that must never reach the LLM even if a view accidentally exposes them.
PII_COLUMNS = {
    "customer_id", "full_name", "name", "email", "phone", "mobile", "pan", "aadhaar",
    "address", "account_number", "manager_name", "manager_email", "date_of_birth", "dob",
}

# Regulatory terms of art that denote product *attributes*, not products.
# RBI's "personal loans" means all loans to individuals, not the bank's
# product literally named "Personal Loan".
REGULATORY_GLOSSARY = {
    "personal loans": {"category": "RETAIL_LOAN"},
    "retail loans": {"category": "RETAIL_LOAN"},
    "loans to individuals": {"category": "RETAIL_LOAN"},
    "emi based loans": {"instalment_based": True},
    "equated instalment loans": {"instalment_based": True},
    "floating rate loans": {"rate_type": "FLOATING"},
    "secured loans": {"secured": True},
    "movable immovable property": {"secured": True},
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

# Operational event -> process that emits it, used when a rule names no process.
EVENT_PROCESSES = {
    "LOAN_CLOSED": "process:loan-closure",
    "DOCUMENTS_RELEASED": "process:collateral-release",
    "RATE_RESET": "process:rate-reset",
    "RATE_RESET_NOTICE_SENT": "process:borrower-notification",
}
CAPABILITY_PROCESSES = {
    "borrower_notification": "process:borrower-notification",
    "emi_or_tenor_choice": "process:rate-reset",
    "fixed_rate_option": "process:rate-reset",
    "reset_statement": "process:borrower-notification",
    "document_release_sla": "process:collateral-release",
    "delay_compensation": "process:collateral-release",
    "document_collection_choice": "process:document-custody",
}

# Remediation workflows the Orchestrator may propose (never auto-executed).
WORKFLOWS = {
    "SEND_BORROWER_NOTICE": "Customer Communications sends a templated notice to each borrower in the worklist.",
    "BRANCH_MANAGER_ALERT": "Alert branch managers with the per-branch worklist.",
    "PRODUCT_CONTROL_CHANGE": "Raise a change request with the product owner to add a missing control.",
    "COMPENSATION_PAYOUT_REVIEW": "Finance reviews and approves statutory delay compensation.",
}


def ontology_summary(domain: str = "All") -> dict:
    """Schema-only view returned to agents by get_graph_ontology."""
    return {
        "domain": domain,
        "node_labels": NODE_TYPES,
        "edge_types": {name: f"{src} -> {dst}" for name, (src, dst) in EDGE_TYPES.items()},
        "capabilities": CAPABILITIES,
        "record_flags": RECORD_FLAGS,
        "event_types": sorted(EVENT_TYPES),
        "product_attributes": PRODUCT_ATTRIBUTES,
        "temporal_model": "Edges carry valid_from/valid_to (business time) and recorded_at/superseded_at (system time).",
    }
