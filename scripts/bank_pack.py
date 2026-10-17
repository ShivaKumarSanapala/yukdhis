"""Writes the ontology pack ``data/ontology/packs/provisioning_iracp.json`` from the control library.

The pack declares what the bank baseline adds to the shared vocabulary: capabilities (one per library control that is not
a legacy rate-reset or document-release control), record flags, event types, the two new sanitized views and their typed
columns, a few glossary terms and the applicability signals. It never redefines anything in the retail-lending vocabulary of
``knowledge_graph/ontology.py``. Column descriptions start with a type (``int:``, ``bool:``, ``text:``, ``date:`` or
``enum[A|B]:``) which the ``record_assertion`` guard uses to type-check expressions.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.bank_library import CONTROLS

ROOT = Path(__file__).resolve().parents[1]
PACK_FILE = ROOT / "data" / "ontology" / "packs" / "provisioning_iracp.json"
LEGACY = {"borrower-notification", "emi-tenor-choice", "fixed-rate-option", "reset-statement", "document-release-sla",
          "delay-compensation", "document-collection-choice"}

CAPABILITY_IDS = {
    "day-end-npa-classification": "automated_npa_classification", "sma-day-end-tagging": "sma_tagging",
    "overdue-day-end-flagging": "overdue_flagging", "standard-asset-provisioning": "standard_asset_provisioning",
    "npa-provisioning-engine": "npa_provisioning", "resolution-plan-provisioning": "resolution_plan_provisioning",
    "cash-basis-income-recognition": "cash_basis_income_recognition", "interest-reversal-memorandum": "interest_reversal",
    "classification-override-governance": "classification_override_governance", "npa-system-audit": "npa_system_audit",
    "npa-mis-reporting": "npa_mis_reporting", "crilc-reporting": "crilc_reporting",
    "collateral-valuation-stock-audit": "collateral_valuation_stock_audit", "borrower-education-sma-npa": "borrower_education_sma_npa",
    "credit-limit-review-tracker": "credit_limit_review_tracking", "floating-provision-governance": "floating_provision_governance",
    "resolution-plan-register": "resolution_plan_register", "crop-season-npa-calendar": "crop_season_npa_calendar",
    "loan-agreement-due-date-disclosure": "loan_agreement_due_date_disclosure", "board-iracp-policy": "board_iracp_policy",
    "kyc-risk-categorisation": "kyc_risk_categorisation", "kyc-periodic-updation": "kyc_periodic_updation",
    "aml-transaction-monitoring": "aml_transaction_monitoring", "grievance-redressal-tracker": "grievance_redressal_tracking",
    "fair-practices-disclosure": "fair_practices_disclosure", "recovery-agent-conduct": "recovery_agent_conduct",
    "outsourcing-due-diligence-register": "outsourcing_due_diligence", "collateral-document-custody-register": "document_custody_register",
    "credit-appraisal-delegation": "credit_appraisal_delegation", "regulatory-change-tracking": "regulatory_change_tracking",
    "ecl-staging-engine": "ecl_staging", "sicr-criteria-documentation": "sicr_criteria_documentation",
    "pd-model": "probability_of_default_model", "lgd-model": "loss_given_default_model", "ead-model": "exposure_at_default_model",
    "eir-computation-engine": "effective_interest_rate_computation", "macro-scenario-framework": "macroeconomic_scenarios",
    "ecl-model-validation-framework": "ecl_model_validation", "prudential-floor-calculator": "prudential_floor_calculation",
    "ecl-model-inventory-tiering": "ecl_model_inventory", "ecl-historical-loss-database": "historical_loss_database",
    "ecl-transition-fair-valuation": "ecl_transition_fair_valuation", "ecl-board-governance": "ecl_board_governance",
    "ecl-disclosure-reporting": "ecl_disclosure_reporting", "third-party-model-governance": "third_party_model_governance",
    "ecl-model-monitoring": "ecl_model_monitoring",
}

ASSET_CLASSES = "STANDARD|SMA0|SMA1|SMA2|SUBSTANDARD|DOUBTFUL|LOSS"
EXPOSURE_CLASSES = "INDIVIDUAL_HOUSING|FARM_CREDIT|SMALL_MICRO_ENTERPRISE|MEDIUM_ENTERPRISE|OTHER"

VIEWS = {
    "vw_asset_quality": {
        "description": "One row per ACTIVE loan with its asset classification, security, provisioning and interest recognition. "
                       "Opaque refs only; no names or contact details.",
        "columns": {
            "loan_ref": "text: opaque loan reference",
            "customer_ref": "text: opaque customer reference",
            "product_id": "text: canonical product node id",
            "segment_id": "text: customer segment node id",
            "branch_id": "text: branch node id",
            "exposure_class": f"enum[{EXPOSURE_CLASSES}]: provisioning exposure class (IRACP para 80)",
            "outstanding_balance": "int: INR outstanding",
            "overdue_amount": "int: INR overdue",
            "days_past_due": "int: days past due, counting the due date as day 1",
            "asset_classification": f"enum[{ASSET_CLASSES}]: asset classification recorded by the bank's day-end process",
            "npa_date": "date: date the account was classified NPA, null for standard accounts",
            "npa_age_months": "int: completed months since npa_date, null for standard accounts (below 12 is substandard, 12 or more doubtful)",
            "security_value": "int: INR assessed realisable value of the security",
            "secured": "bool: true when the exposure was secured ab initio (security above 10 per cent of the outstanding, para 5(13))",
            "secured_portion": "int: INR part of the outstanding covered by the security",
            "unsecured_portion": "int: INR part of the outstanding not covered by the security",
            "restructured": "bool: a resolution plan has been implemented",
            "restructure_date": "date: date of the latest resolution plan",
            "restructure_count": "int: number of resolution plans (restructuring instances) implemented",
            "provision_held": "int: INR provision held against the account",
            "provision_required": "int: INR provision the bank's system computed as required under IRACP",
            "interest_recognition": "enum[ACCRUAL|CASH]: basis on which interest income is recognised",
            "ecl_stage_proxy": "int: PROXY ONLY, derived from days past due (1 up to 30, 2 up to 90, 3 above); not the output of an ECL staging engine",
        },
    },
    "vw_kyc_status": {
        "description": "One row per customer with the periodic KYC updation status. Opaque customer reference only.",
        "columns": {
            "customer_ref": "text: opaque customer reference",
            "segment_id": "text: customer segment node id",
            "branch_id": "text: branch node id",
            "kyc_risk_category": "enum[LOW|MEDIUM|HIGH]: customer risk category (re-KYC every 10, 8 or 2 years)",
            "last_kyc_review": "date: date of the last KYC review",
            "kyc_due_date": "date: date by which the next KYC updation is due",
            "kyc_status": "enum[CURRENT|DUE_SOON|OVERDUE]: DUE_SOON within 60 days of the due date, OVERDUE after it",
        },
    },
}


def pack() -> dict:
    capabilities = {}
    for c in CONTROLS:
        slug = c["id"].removeprefix("control:")
        if slug in LEGACY:
            continue
        capabilities[CAPABILITY_IDS[slug]] = {"control": c["id"], "description": c["description"], "process": c["process"],
                                              "synonyms": sorted({c["name"].lower(), slug.replace("-", " "), *c["synonyms"]})}
    return {
        "pack_id": "provisioning_iracp", "version": "1.0.0",
        "title": "Provisioning, asset classification and income recognition (IRACP) and the ECL readiness controls",
        "domain": "asset_quality", "business_line": "retail_lending", "requires_data_domains": ["loans", "asset_quality", "kyc"],
        "capabilities": capabilities,
        "record_flags": {
            "restructured": "A resolution plan has been implemented on the loan (IRACP paras 62A, 62B, 84A-84D, 139A, 139B).",
            "secured": "The exposure was secured ab initio: security above 10 per cent of the outstanding (IRACP para 5(13)).",
        },
        "event_types": ["OVERDUE_FLAGGED", "EMI_BOUNCED", "COLLECTION_NOTICE_SENT", "SMA_FLAGGED", "NPA_CLASSIFIED", "PROVISION_BOOKED",
                        "INTEREST_REVERSED", "ACCOUNT_RESTRUCTURED"],
        "event_processes": {
            "OVERDUE_FLAGGED": "process:asset-classification", "SMA_FLAGGED": "process:asset-classification",
            "NPA_CLASSIFIED": "process:asset-classification", "PROVISION_BOOKED": "process:provisioning",
            "INTEREST_REVERSED": "process:income-recognition", "ACCOUNT_RESTRUCTURED": "process:restructuring-resolution",
            "EMI_BOUNCED": "process:collections-recovery", "COLLECTION_NOTICE_SENT": "process:collections-recovery",
        },
        "product_attributes": [],
        "views": VIEWS,
        "glossary": {
            "farm credit": {"category": "AGRI_LOAN"},
            "agricultural advances": {"category": "AGRI_LOAN"},
            "credit card receivables": {"category": "CARD"},
            "msme advances": {"category": "MSME_LOAN"},
        },
        "workflows": {},
        "applicability_signals": {
            "keywords": ["non-performing asset", "NPA", "special mention account", "SMA", "provision", "provisioning", "asset classification",
                         "income recognition", "substandard", "doubtful", "loss asset", "days past due", "overdue", "resolution plan",
                         "restructuring", "expected credit loss", "KYC", "periodic updation"],
            "business_line": "retail_lending", "data_domains": ["loans", "asset_quality", "kyc"],
        },
    }


def write_pack(path: Path = PACK_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pack(), indent=1) + "\n")
