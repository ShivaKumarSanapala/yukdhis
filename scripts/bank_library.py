"""Internal policy and control library of the synthetic bank.

The bank is fictional; the *RBI references* in the mappings are real and come from the documents in ``data/circulars/``
(paragraph numbers of the IRACP Directions, the IRACP Amendment Directions and the final ACPIR Directions) plus a few
instruments those documents themselves cite (Resolution of Stressed Assets Directions, the outsourcing of IT services
Master Direction, the Integrated Ombudsman Scheme). External instruments that are not in the corpus carry
``"in_corpus": false`` and no paragraph numbers.

``write_library()`` (called by generate_synthetic_bank.py) writes:
  data/bank/controls.json   the control library (implementation status, owner, evidence, data check)
  data/bank/policies.json   the internal policies, their clauses, and the mapping to RBI instruments and to controls
and ``library_nodes_and_edges()`` turns them into knowledge-graph nodes and edges:
  Policy (kind=internal), Control, ``IMPLEMENTS`` (internal Policy -> regulatory Rule), ``ENFORCES`` (Policy -> Control),
  ``PROVIDES`` (Product -> Control, bitemporal: valid from the date the control went live).

The incurred-loss controls (classification, provisioning, income recognition) are ``implemented``. The controls that the final
ACPIR Directions (effective 1 Apr 2027) add (staging engine, SICR criteria, PD/LGD/EAD models, EIR, macro scenarios, model
validation, prudential floors, ...) are ``absent`` or ``planned``: against those Directions the bank has readiness gaps, not
compliance failures.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANK = ROOT / "data" / "bank"

# ----------------------------------------------------------------------------------------------------- instruments
INSTRUMENTS = {
    "iracp": dict(reference="RBI/DOR/2025-26/164", title="Commercial Banks - Income Recognition, Asset Classification and Provisioning Directions, 2025",
                  effective_date="2025-11-28", status="in_force", in_corpus=True,
                  corpus_file="rbi_ecl_2025_11_28_iracp_directions_v2026_07_01.md"),
    "iracp_amend_2025": dict(reference="RBI/2025-26/128", title="IRACP Amendment Directions, 2025 (deletes paragraph 117)",
                             effective_date="2026-01-01", status="in_force", in_corpus=True,
                             corpus_file="rbi_ecl_2025_12_04_iracp_amendment_directions.md"),
    "iracp_amend_2026": dict(reference="RBI/2026-27/45", title="IRACP Amendment Directions, 2026 (resolution plans: 62A/62B, 84A-84D, 139A/139B)",
                             effective_date="2026-07-01", status="in_force", in_corpus=True,
                             corpus_file="rbi_ecl_2026_04_29_iracp_amendment_directions.md"),
    "acpir": dict(reference="RBI/DOR/2026-27/398", title="Commercial Banks - Asset Classification, Provisioning and Income Recognition (ACPIR) Directions, 2026",
                  effective_date="2027-04-01", status="not_yet_in_force", in_corpus=True,
                  corpus_file="rbi_ecl_2026_04_27_acpir_directions_final.md"),
    "iracp_repeal": dict(reference="RBI/DOR/2026-27/36", title="IRACP Repeal Directions, 2026 (repeals the IRACP Directions from 1 Apr 2027)",
                         effective_date="2027-04-01", status="not_yet_in_force", in_corpus=True,
                         corpus_file="rbi_ecl_2026_04_27_iracp_repeal_directions.md"),
    "floating_rate_reset": dict(reference="DOR.MCS.REC.32/01.01.003/2023-24", title="Reset of Floating Interest Rate on EMI based Personal Loans (RBI/2023-24/55)",
                                effective_date="2023-12-31", status="in_force", in_corpus=True,
                                corpus_file="rbi_2023_08_18_floating_rate_reset_emi_loans.md"),
    "document_release": dict(reference="DoR.MCS.REC.38/01.01.001/2023-24", title="Release of Movable / Immovable Property Documents (RBI/2023-24/60)",
                            effective_date="2023-12-01", status="in_force", in_corpus=True,
                            corpus_file="rbi_2023_09_13_release_of_property_documents.md"),
    "resolution_stressed_assets": dict(reference="Reserve Bank of India (Commercial Banks - Resolution of Stressed Assets) Directions, 2025",
                                       title="Resolution of Stressed Assets Directions, 2025 (Chapter VI-A: resolution plans)", effective_date="2025-11-28",
                                       status="in_force", in_corpus=False),
    "outsourcing_it": dict(reference="Master Direction on Outsourcing of Information Technology Services dated April 10, 2023",
                           title="Master Direction on Outsourcing of Information Technology Services", effective_date="2023-10-01",
                           status="in_force", in_corpus=False),
    "kyc_md": dict(reference="Master Direction - Know Your Customer (KYC) Direction, 2016 (as updated)",
                   title="Know Your Customer (KYC) Direction", effective_date="2016-02-25", status="in_force", in_corpus=False),
    "ombudsman": dict(reference="Reserve Bank - Integrated Ombudsman Scheme, 2021", title="Integrated Ombudsman Scheme (RB-IOS), 2021",
                      effective_date="2021-11-12", status="in_force", in_corpus=False),
    "fair_practices": dict(reference="Guidelines on Fair Practices Code for Lenders", title="Fair Practices Code for Lenders",
                           effective_date="2012-05-01", status="in_force", in_corpus=False),
}

SYSTEM = "system"
PROCESS = "process"
GOVERNANCE = "governance"
PRODUCT = "product"


def sat(instrument: str, *paragraphs: str) -> list[dict]:
    return [{"instrument": instrument, "paragraph": p} for p in paragraphs]


def control(slug, name, kind, owner, process, status, *, desc="", since=None, planned=None, products="all", satisfies=(), check=None,
            evidence=(), tested=None, result=None, synonyms=(), notes="") -> dict:
    return {"id": f"control:{slug}", "name": name, "kind": kind, "description": desc, "owner_team": f"team:{owner}",
            "process": f"process:{process}" if process else None, "implementation_status": status,
            "implemented_from": since, "planned_go_live": planned, "applies_to": products, "satisfies": list(satisfies),
            "data_check": check, "evidence": [{"type": t, "ref": r, "title": ti} for t, r, ti in evidence],
            "last_tested": tested, "last_test_result": result, "synonyms": list(synonyms), "notes": notes}


def check(rule: str | None, instrument: str, view: str, what: str, expression: dict | None = None) -> dict:
    """The data check that evidences a control: a rule of the template of record (``rule``) or, where no template covers the
    obligation, an explicit ``expression`` {where, assert}; either runs as a record_assertion on ``view``."""
    return {"instrument": instrument, "rule": rule, "view": view, "description": what, "expression": expression}


# ----------------------------------------------------------------------------------------------------- controls
CONTROLS = [
    # --- Product controls of the rate-reset and document-release circulars (RBI/2023-24/55 and /60) ------------------
    control("borrower-notification", "Reset Impact Notification", PRODUCT, "customer-comms", "borrower-notification", "implemented",
            since="2023-12-31", desc="Tells each borrower the EMI or tenor impact of a benchmark reset.",
            satisfies=sat("floating_rate_reset", "Key provisions (1)"), tested="2026-08-12", result="exceptions",
            evidence=[("mis_report", "MIS-RESET-2026Q3", "Quarterly reset notification report")]),
    control("emi-tenor-choice", "EMI or Tenor Choice", PRODUCT, "lending-ops", "rate-reset", "implemented", since="2023-12-31",
            desc="Lets the borrower choose a higher EMI or a longer tenor at reset.", satisfies=sat("floating_rate_reset", "Key provisions (2)"),
            tested="2026-08-12", result="pass"),
    control("fixed-rate-option", "Fixed Rate Conversion Option", PRODUCT, "lending-ops", "rate-reset", "partial", since="2023-12-31",
            desc="Lets the borrower switch to a fixed rate. DriveNow Floating lost the option on 2024-07-01 (change CHG-2024-0612).",
            satisfies=sat("floating_rate_reset", "Key provisions (2)"), tested="2026-08-12", result="exceptions",
            notes="Planted product-level gap: DriveNow Floating Loan has no fixed-rate switch."),
    control("reset-statement", "Quarterly Reset Statement with APR", PRODUCT, "customer-comms", "borrower-notification", "implemented",
            since="2023-12-31", desc="Issues a statement of principal and interest recovered, EMIs remaining and APR.",
            satisfies=sat("floating_rate_reset", "Key provisions (3)"), tested="2026-08-12", result="exceptions"),
    control("document-release-sla", "30-day Document Release Tracker", PRODUCT, "collateral-ops", "collateral-release", "implemented",
            since="2023-12-01", desc="Tracks original property documents from loan closure to release; escalates before day 30.",
            satisfies=sat("document_release", "Release timeline"), tested="2026-09-02", result="exceptions",
            evidence=[("mis_report", "MIS-DOC-2026-09", "Document release ageing report")]),
    control("delay-compensation", "Automatic Delay Compensation", PRODUCT, "collateral-ops", "collateral-release", "implemented",
            since="2023-12-01", desc="Computes and pays INR 5,000 per day of delay attributable to the bank.",
            satisfies=sat("document_release", "Compensation for delay"), tested="2026-09-02", result="pass"),
    control("document-collection-choice", "Document Collection Location Choice", PRODUCT, "collateral-ops", "document-custody", "implemented",
            since="2023-12-01", desc="Offers the branch of servicing or any office holding the documents.",
            satisfies=sat("document_release", "Place of collection"), tested="2026-09-02", result="pass"),
    # --- Current incurred-loss regime (IRACP Directions, 2025) -------------------------------------------------------
    control("day-end-npa-classification", "Automated Day-end NPA Classification", SYSTEM, "credit-risk", "asset-classification", "implemented",
            since="2024-04-01", desc="Core-banking module that classifies every borrowal account at day-end, with straight-through upgrades and downgrades.",
            satisfies=sat("iracp", "31", "36(2)", "37", "42(1)") + sat("acpir", "8", "15"), tested="2026-09-18", result="exceptions",
            check=check("npa-classification-90-days", "iracp", "vw_asset_quality", "Accounts with DPD above 90 are classified NPA."),
            evidence=[("audit_report", "IA-2026-011", "System audit of the NPA classification system, FY26"),
                      ("system_config", "CBS-ACM-2026.3", "Asset classification parameters, release 2026.3")],
            synonyms=["npa classification", "asset classification system", "day-end process classification"]),
    control("sma-day-end-tagging", "SMA Day-end Tagging (SMA-0, SMA-1, SMA-2)", SYSTEM, "credit-risk", "asset-classification", "implemented",
            since="2024-04-01", desc="Tags overdue accounts as SMA-0, SMA-1 or SMA-2 in the day-end process, with the date of tagging.",
            satisfies=sat("iracp", "30", "31"), tested="2026-09-18", result="exceptions",
            check=check("sma-reporting-bands", "iracp", "vw_asset_quality", "SMA tag matches the days past due band."),
            evidence=[("system_config", "CBS-ACM-2026.3", "SMA band parameters")], synonyms=["sma classification", "special mention accounts"]),
    control("overdue-day-end-flagging", "Overdue Flagging at Day-end", SYSTEM, "credit-risk", "asset-classification", "implemented",
            since="2024-04-01", desc="Flags an account overdue in the day-end process for the due date, whatever time the process runs.",
            satisfies=sat("iracp", "30") + sat("acpir", "14"), tested="2026-09-18", result="pass"),
    control("standard-asset-provisioning", "Standard Asset Provisioning Engine", SYSTEM, "finance", "provisioning", "implemented",
            since="2024-04-01", desc="Computes the general provision on standard assets at 0.25 per cent (housing, farm credit, small and micro enterprises) or 0.40 per cent.",
            satisfies=sat("iracp", "36(3)", "80", "81"), tested="2026-09-18", result="pass",
            check=check("standard-asset-provision-025", "iracp", "vw_asset_quality", "Provision held meets the standard-asset rate."),
            evidence=[("audit_report", "IA-2026-011", "System audit of provisioning rules, FY26")]),
    control("npa-provisioning-engine", "NPA Provisioning Engine", SYSTEM, "finance", "provisioning", "implemented", since="2024-04-01",
            desc="Computes substandard (15% / 25% unsecured), doubtful (100% unsecured plus 25/40/100% secured by age) and loss (100%) provisions.",
            satisfies=sat("iracp", "36(3)", "85", "86", "90", "91", "95"), tested="2026-09-18", result="exceptions",
            check=check("substandard-provision-secured-15", "iracp", "vw_asset_quality", "Provision held meets the substandard, doubtful and loss rates."),
            evidence=[("audit_report", "IA-2026-011", "System audit of provisioning rules, FY26"),
                      ("test_script", "UAT-PROV-0412", "Provisioning regression pack")]),
    control("resolution-plan-provisioning", "Additional Provision for Resolution Plans (5% per plan)", SYSTEM, "finance", "provisioning", "implemented",
            since="2026-07-01", desc="Books the additional specific provision of 5 per cent of the outstanding for each resolution plan implemented.",
            satisfies=sat("iracp", "84A", "84B", "84C", "84D") + sat("iracp_amend_2026", "3(iv)"), tested="2026-09-30", result="pass",
            check=check("resolution-plan-additional-provision-84a", "iracp", "vw_asset_quality", "Additional 5% per resolution plan is held."),
            evidence=[("change_ticket", "CHG-2026-0701", "Go-live of 84A/84B parameters on 1 Jul 2026")],
            notes="Implemented from 1 Jul 2026, the day the amendment took effect (bitemporal demo)."),
    control("cash-basis-income-recognition", "Income Recognition on Cash Basis for NPAs", SYSTEM, "finance", "income-recognition", "implemented",
            since="2024-04-01", desc="Stops interest accrual on non-standard accounts and recognises income on receipt; re-restructured accounts are on cash basis.",
            satisfies=sat("iracp", "36(4)", "124", "125", "139A", "139B") + sat("acpir", "115"), tested="2026-09-18", result="exceptions",
            check=check("npa-income-cash-basis", "iracp", "vw_asset_quality", "Interest on NPAs is recognised on cash basis."),
            evidence=[("audit_report", "IA-2026-011", "System audit of income recognition, FY26")]),
    control("interest-reversal-memorandum", "Interest Reversal and Memorandum Account on NPA", SYSTEM, "finance", "income-recognition", "implemented",
            since="2024-04-01", desc="Reverses uncollected interest on an account that turns NPA and tracks it in a memorandum account.",
            satisfies=sat("iracp", "128", "132", "133", "134"), tested="2026-09-18", result="pass"),
    control("classification-override-governance", "Manual Override Log with Dual Authorisation", GOVERNANCE, "credit-risk", "asset-classification", "implemented",
            since="2024-04-01", desc="Overrides of system classification need two-level authorisation, a system-generated log kept three years, and Audit Committee reporting.",
            satisfies=sat("iracp", "38(2)", "38(3)", "38(4)"), tested="2026-06-30", result="pass",
            evidence=[("audit_report", "IA-2026-004", "Concurrent audit: overrides, Q1 FY27")]),
    control("npa-system-audit", "Annual System Audit of the NPA Classification System", GOVERNANCE, "internal-audit", "asset-classification", "implemented",
            since="2024-04-01", desc="Yearly audit of the classification system parameters and of compliance with the IRACP norms.",
            satisfies=sat("iracp", "39(3)", "40"), tested="2026-03-20", result="pass",
            evidence=[("audit_report", "IA-2026-011", "System audit of the NPA classification system, FY26")]),
    control("npa-mis-reporting", "Segment-wise NPA and Restructured Asset MIS", PROCESS, "finance", "regulatory-reporting", "implemented",
            since="2024-04-01", desc="System-generated segment-wise NPA, provisions, write-offs and restructured assets, reconciled to regulatory returns.",
            satisfies=sat("iracp", "29", "33", "34") + sat("acpir", "16"), tested="2026-07-15", result="pass"),
    control("crilc-reporting", "CRILC and Supervisory NPA Reporting", PROCESS, "compliance", "regulatory-reporting", "implemented",
            since="2024-04-01", desc="Reports large credits and SMA accounts to CRILC and files the supervisory returns on time.",
            satisfies=sat("iracp", "11"), tested="2026-07-15", result="pass"),
    control("collateral-valuation-stock-audit", "Collateral Valuation and Stock Audit for NPAs of INR 5 crore and above", PROCESS, "credit-risk",
            "provisioning", "partial", since="2024-10-01",
            desc="Annual external stock audit and three-yearly valuation of immovable collateral for NPAs of INR 5 crore and above.",
            satisfies=sat("iracp", "22", "23"), tested="2026-05-10", result="exceptions",
            notes="Two valuations older than three years at the last test; remediation owner Credit Risk."),
    control("borrower-education-sma-npa", "Borrower Education on SMA and NPA", PROCESS, "customer-comms", "borrower-notification", "implemented",
            since="2022-01-01", desc="Website literature and branch posters on overdue, SMA and NPA; front-line briefing at sanction.",
            satisfies=sat("iracp", "25"), tested="2026-04-22", result="pass"),
    control("credit-limit-review-tracker", "Credit Limit Review and Renewal Tracker", PROCESS, "credit-risk", "credit-underwriting", "implemented",
            since="2022-04-01", desc="Tracks regular and ad hoc limits so that reviews finish within three months and no limit goes 180 days unreviewed.",
            satisfies=sat("iracp", "16", "18", "20", "42(5)"), tested="2026-06-05", result="pass"),
    control("floating-provision-governance", "Floating Provisions Utilisation Governance", GOVERNANCE, "finance", "provisioning", "implemented",
            since="2022-04-01", desc="Board-approved ceiling and extraordinary-circumstance criteria; RBI approval before any utilisation.",
            satisfies=sat("iracp", "10(2)", "10(3)", "97", "98", "99"), tested="2026-03-31", result="pass"),
    control("resolution-plan-register", "Resolution Plan Register and Restructuring Flagging", PROCESS, "collections-recovery", "restructuring-resolution", "implemented",
            since="2026-07-01", desc="Flags accounts under a resolution plan, counts restructuring instances and keeps them standard per 62A/62B.",
            satisfies=sat("iracp", "62A", "62B", "84A", "84B") + sat("iracp_amend_2026", "3(ii)") + sat("resolution_stressed_assets", "Chapter VI-A"),
            tested="2026-09-30", result="pass", check=check("resolution-plan-classification-62a-62b", "iracp", "vw_asset_quality",
                                                            "Accounts under a resolution plan are not downgraded for the plan itself."),
            evidence=[("change_ticket", "CHG-2026-0701", "Go-live of the resolution plan register on 1 Jul 2026")]),
    control("crop-season-npa-calendar", "Crop-season NPA Calendar for Agricultural Advances", SYSTEM, "agri-banking", "asset-classification", "partial",
            since="2025-04-01", desc="Applies the two-crop-season (short duration) and one-crop-season (long duration) norms with SLBC calendars.",
            satisfies=sat("iracp", "42(6)", "42(7)", "57"), tested="2026-08-01", result="exceptions",
            notes="Calendars loaded for 9 of 14 states; the rest follow the 90-day norm conservatively."),
    control("loan-agreement-due-date-disclosure", "Loan Agreement Due-date and SMA/NPA Date Disclosure", PROCESS, "lending-ops", "credit-underwriting", "implemented",
            since="2022-01-01", desc="Loan agreements state due dates, frequency, principal and interest split and example SMA/NPA dates.",
            satisfies=sat("iracp", "13", "14") + sat("acpir", "13"), tested="2026-05-30", result="pass"),
    control("board-iracp-policy", "Board-approved IRACP Implementation Policy", GOVERNANCE, "credit-risk", "asset-classification", "implemented",
            since="2024-04-01", desc="Board policy covering income recognition, floating provisions, higher standard-asset provisioning, valuers and delegation for overrides.",
            satisfies=sat("iracp", "10"), tested="2026-03-31", result="pass",
            evidence=[("board_minute", "BRD-2026-03-27", "Board approval of the Asset Classification and Provisioning Policy v5")]),
    # --- KYC, AML, conduct, outsourcing -----------------------------------------------------------------------------
    control("kyc-risk-categorisation", "Customer Risk Categorisation", PROCESS, "kyc-aml", "kyc-periodic-review", "implemented", since="2019-04-01",
            desc="Assigns low, medium or high risk at onboarding and at review.", satisfies=sat("kyc_md", "risk categorisation"),
            tested="2026-06-12", result="pass"),
    control("kyc-periodic-updation", "Risk-based Periodic KYC Updation", PROCESS, "kyc-aml", "kyc-periodic-review", "implemented", since="2026-01-01",
            desc="Re-KYC every 2 years (high risk), 8 years (medium) and 10 years (low), with reminders from 60 days before the due date.",
            satisfies=sat("kyc_md", "periodic updation"), tested="2026-09-24", result="exceptions",
            check=check(None, "kyc_md", "vw_kyc_status", "No customer is past the re-KYC due date.",
                        {"where": None, "assert": "kyc_due_date >= :as_of"}),
            evidence=[("campaign", "KYC-REFRESH-2026", "Re-KYC campaign and automated reminders from Jan 2026")],
            notes="Automated reminders only started in 2026; 22 customers are past their due date at the reference date."),
    control("aml-transaction-monitoring", "AML Transaction Monitoring", SYSTEM, "kyc-aml", "kyc-periodic-review", "partial", since="2021-04-01",
            desc="Scenario-based monitoring on the deposit and card books; loan repayments are not yet covered.", satisfies=sat("kyc_md", "ongoing monitoring"),
            tested="2026-02-18", result="exceptions"),
    control("grievance-redressal-tracker", "Customer Grievance Redressal Tracker", PROCESS, "customer-service", "customer-grievance", "implemented",
            since="2021-11-12", desc="Registers, routes and ages complaints; escalates to the internal ombudsman and tracks Ombudsman references.",
            satisfies=sat("ombudsman", "complaint handling"), tested="2026-07-08", result="pass"),
    control("fair-practices-disclosure", "Fair Practices Code Disclosure Checks", PROCESS, "retail-lending", "credit-underwriting", "implemented",
            since="2012-05-01", desc="Checks that loan applications, sanction letters and charge disclosures follow the Fair Practices Code.",
            satisfies=sat("fair_practices", "disclosure"), tested="2026-05-30", result="pass"),
    control("recovery-agent-conduct", "Recovery Agent Conduct Monitoring", PROCESS, "collections-recovery", "collections-recovery", "partial",
            since="2023-04-01", desc="Code of conduct, call-hour limits and complaint review for recovery agents; call recording covers 60 per cent of agencies.",
            satisfies=sat("fair_practices", "recovery"), tested="2026-04-15", result="exceptions"),
    control("outsourcing-due-diligence-register", "Outsourced Service Provider Register and Due Diligence", PROCESS, "vendor-management", None, "implemented",
            since="2023-10-01", desc="Register of material outsourcing, due diligence, access for supervisors and exit plans.",
            satisfies=sat("outsourcing_it", "outsourcing governance"), tested="2026-05-05", result="pass"),
    control("collateral-document-custody-register", "Original Document Custody Register", PROCESS, "collateral-ops", "document-custody", "implemented",
            since="2018-04-01", desc="Records every original property document by loan, location and movement; feeds the 30-day release tracker.",
            satisfies=sat("document_release", "Release timeline"), tested="2026-09-02", result="pass"),
    control("credit-appraisal-delegation", "Credit Appraisal and Delegation of Powers Checks", PROCESS, "credit-risk", "credit-underwriting", "implemented",
            since="2020-04-01", desc="Appraisal checklist, cash-flow-based repayment schedules and sanction within delegated limits.",
            satisfies=sat("iracp", "12") + sat("acpir", "12"), tested="2026-06-05", result="pass"),
    control("regulatory-change-tracking", "Regulatory Change Tracking", GOVERNANCE, "compliance", "regulatory-reporting", "partial", since="2025-04-01",
            desc="Compliance logs RBI circulars and assigns owners; impact assessment is manual and takes weeks.",
            satisfies=[], tested="2026-06-20", result="exceptions", notes="The gap Yukdhis is meant to close."),
    # --- ECL readiness: controls the ACPIR Directions (effective 1 Apr 2027) require -----------------------------------
    control("ecl-staging-engine", "ECL Staging Engine (Stages 1, 2, 3)", SYSTEM, "credit-risk", "asset-classification", "absent",
            desc="Assigns every instrument to Stage 1, 2 or 3 at each reporting date, with SICR triggers.",
            satisfies=sat("acpir", "25", "26", "33"), notes="Only a DPD-based proxy (ecl_stage_proxy) exists; it is not a staging engine."),
    control("sicr-criteria-documentation", "SICR Criteria Documentation", GOVERNANCE, "credit-risk", "model-governance", "planned", planned="2026-12-31",
            desc="Documented, consistently applied criteria for a significant increase in credit risk, with the 30-day rebuttable presumption.",
            satisfies=sat("acpir", "26", "29", "30", "33", "34", "35", "36"), notes="Working group set up in Aug 2026; drafting in progress."),
    control("pd-model", "Probability of Default Models", SYSTEM, "model-risk", "model-governance", "absent",
            desc="12-month and lifetime PD by segment, with the regulatory floor of 0.03 per cent.", satisfies=sat("acpir", "45", "96")),
    control("lgd-model", "Loss Given Default Models", SYSTEM, "model-risk", "model-governance", "absent",
            desc="LGD from own recovery history, with the prescribed LGD where history is insufficient.", satisfies=sat("acpir", "97", "98", "99")),
    control("ead-model", "Exposure at Default Models", SYSTEM, "model-risk", "model-governance", "absent",
            desc="EAD including drawdown expectations on commitments and guarantees.", satisfies=sat("acpir", "100", "43")),
    control("eir-computation-engine", "Effective Interest Rate Computation", SYSTEM, "finance", "income-recognition", "absent",
            desc="EIR at initial recognition, fee and cost amortisation, interest income on gross carrying amount.",
            satisfies=sat("acpir", "20", "21", "50", "51", "52", "53", "113")),
    control("macro-scenario-framework", "Macroeconomic Scenario Framework", GOVERNANCE, "model-risk", "model-governance", "planned", planned="2027-02-28",
            desc="Multiple macro scenarios with probability weights, linked to PD and LGD.", satisfies=sat("acpir", "47", "48", "49", "72", "124", "132")),
    control("ecl-model-validation-framework", "ECL Model Validation Framework", GOVERNANCE, "model-risk", "model-governance", "planned", planned="2027-01-31",
            desc="Independent pre-implementation validation, back-testing and calibration of ECL models.",
            satisfies=sat("acpir", "99", "130", "131"), notes="The general MRM policy exists; the ECL-specific validation standard does not."),
    control("prudential-floor-calculator", "Prudential Floor Calculator", SYSTEM, "finance", "provisioning", "absent",
            desc="Product-wise and stage-wise floors on the ECL allowance, with strict product category segregation.",
            satisfies=sat("acpir", "82", "90", "91", "92", "93", "94")),
    control("ecl-model-inventory-tiering", "ECL Model Inventory and Risk Tiering", GOVERNANCE, "model-risk", "model-governance", "planned", planned="2026-12-31",
            desc="Inventory of all models in the ECL lifecycle with risk-based tiering and documentation.", satisfies=sat("acpir", "120", "121", "122")),
    control("ecl-historical-loss-database", "Historical Loss and Default Database", SYSTEM, "it-core-banking", "model-governance", "absent",
            desc="Sufficient history of defaults, recoveries and loss to calibrate PD and LGD.", satisfies=sat("acpir", "64", "70", "71")),
    control("ecl-transition-fair-valuation", "Fair Valuation on Transition and Transitional Arrangements", PROCESS, "finance", "provisioning", "absent",
            desc="Fair valuation of the loan portfolio on 1 Apr 2027 and the CET1 transition adjustment.", satisfies=sat("acpir", "19", "106", "107", "108", "110")),
    control("ecl-board-governance", "Board ECL Oversight (Committee with CFO and CRO)", GOVERNANCE, "compliance", "model-governance", "planned", planned="2026-12-15",
            desc="Board policy and a committee with the CFO and CRO that oversees ECL implementation.", satisfies=sat("acpir", "56", "57")),
    control("ecl-disclosure-reporting", "ECL Disclosures and Regulatory Reporting", PROCESS, "finance", "regulatory-reporting", "absent",
            desc="Annex 4 disclosures and the first ECL reporting as at 30 Jun 2027.", satisfies=sat("acpir", "141", "142", "144", "147", "148", "150")),
    control("third-party-model-governance", "Third-party Model Governance", GOVERNANCE, "vendor-management", "model-governance", "planned", planned="2027-02-28",
            desc="Selection, validation and contractual access for models bought from vendors.", satisfies=sat("acpir", "135", "136", "137", "139", "140")),
    control("ecl-model-monitoring", "Continuous ECL Model Performance Monitoring", GOVERNANCE, "model-risk", "model-governance", "absent",
            desc="Ongoing performance monitoring with clear responsibilities and frequency.", satisfies=sat("acpir", "133")),
]

# ----------------------------------------------------------------------------------------------------- policies
def m(instrument: str, *paragraphs: str, rules: tuple[str, ...] = ()) -> dict:
    return {"instrument": instrument, "paragraphs": list(paragraphs), "rules": list(rules)}


def clause(policy: str, number: str, title: str, summary: str, owner: str, effective: str, maps: list[dict], controls: tuple[str, ...] = (),
           status: str = "in_force") -> dict:
    return {"clause_id": f"{policy}/{number}", "title": title, "summary": summary, "owner_team": f"team:{owner}", "effective_from": effective,
            "status": status, "maps_to": maps, "controls": [f"control:{c}" for c in controls]}


def policy(code: str, title: str, owner: str, version: str, approved_by: str, approved_on: str, effective: str, review: str, clauses: list[dict]) -> dict:
    return {"policy_id": f"policy:{code}", "code": code, "title": title, "kind": "internal", "owner_team": f"team:{owner}", "version": version,
            "approved_by": approved_by, "approved_on": approved_on, "effective_date": effective, "next_review": review, "status": "in_force", "clauses": clauses}


POLICIES = [
    policy("POL-CRD-001", "Credit Policy", "credit-risk", "7.2", "Board Credit Committee", "2025-12-18", "2026-01-01", "2027-03-31", [
        clause("POL-CRD-001", "2.1", "Appraisal and repayment schedules", "Repayment schedules follow borrower cash flows; sanction within delegated limits.",
               "credit-risk", "2026-01-01", [m("iracp", "12")], ("credit-appraisal-delegation",)),
        clause("POL-CRD-001", "2.3", "Review and renewal of credit limits", "Limits are reviewed within three months of the due date; no limit goes 180 days unreviewed.",
               "credit-risk", "2026-01-01", [m("iracp", "16", "18", "20", "42(5)")], ("credit-limit-review-tracker",)),
        clause("POL-CRD-001", "3.2", "Loan agreement disclosures", "Agreements state due dates, instalment split and example SMA/NPA dates.",
               "lending-ops", "2026-01-01", [m("iracp", "13", "14")], ("loan-agreement-due-date-disclosure",)),
    ]),
    policy("POL-ACP-002", "Asset Classification and Provisioning Policy", "credit-risk", "5.0", "Board of Directors", "2026-03-27", "2026-04-01", "2027-03-31", [
        clause("POL-ACP-002", "4.1", "Classification as NPA and SMA tagging",
               "Classification and SMA tagging are system driven at day-end; an account is NPA when overdue for more than 90 days.",
               "credit-risk", "2025-11-28", [m("iracp", "30", "31", "42(1)", rules=("npa-classification-90-days", "sma-reporting-bands"))],
               ("day-end-npa-classification", "sma-day-end-tagging", "overdue-day-end-flagging")),
        clause("POL-ACP-002", "4.2", "Standard asset provisioning", "General provision at 0.25 per cent for housing, farm credit and small and micro enterprises, 0.40 per cent otherwise.",
               "finance", "2025-11-28", [m("iracp", "80", "81", rules=("standard-asset-provision-025", "standard-asset-provision-040"))],
               ("standard-asset-provisioning",)),
        clause("POL-ACP-002", "4.3", "NPA provisioning", "Substandard 15 per cent (25 per cent unsecured); doubtful 100 per cent unsecured plus 25/40/100 per cent secured by age; loss 100 per cent.",
               "finance", "2025-11-28", [m("iracp", "85", "86", "90", "91", "95", rules=(
                   "substandard-provision-secured-15", "substandard-provision-unsecured-25", "doubtful-unsecured-portion-100",
                   "doubtful-secured-portion-ageing", "loss-asset-provision-100"))], ("npa-provisioning-engine",)),
        clause("POL-ACP-002", "4.4", "Income recognition", "Accrual only on standard accounts; cash basis for the rest; reversal and memorandum tracking on slippage.",
               "finance", "2025-11-28", [m("iracp", "124", "125", "128", "132", "133", rules=("npa-income-cash-basis",))],
               ("cash-basis-income-recognition", "interest-reversal-memorandum")),
        clause("POL-ACP-002", "4.5", "Resolution plans and restructured accounts",
               "Standard accounts stay standard on implementation of a resolution plan; 5 per cent additional provision per plan; accrual after a plan, cash basis after repeated restructuring.",
               "collections-recovery", "2026-07-01", [
                   m("iracp", "62A", "62B", "84A", "84B", "139A", "139B", rules=(
                       "resolution-plan-classification-62a-62b", "resolution-plan-additional-provision-84a",
                       "repeated-restructuring-additional-provision-84b", "resolution-plan-income-accrual-139a",
                       "restructured-again-income-cash-basis-139b")),
                   m("iracp_amend_2026", "3(ii)", "3(iv)", "3(v)", rules=(
                       "resolution-plan-classification-62a-62b", "resolution-plan-additional-provision-84a",
                       "repeated-restructuring-additional-provision-84b", "resolution-plan-income-accrual-139a",
                       "restructured-again-income-cash-basis-139b"))],
               ("resolution-plan-register", "resolution-plan-provisioning")),
        clause("POL-ACP-002", "4.6", "Overrides and audit trail", "Manual overrides need two-level authorisation, system logs kept for three years and Audit Committee reporting.",
               "credit-risk", "2025-11-28", [m("iracp", "38")], ("classification-override-governance",)),
        clause("POL-ACP-002", "4.7", "Floating provisions", "Ceiling set by the Board; use only in extraordinary circumstances with RBI approval.",
               "finance", "2025-11-28", [m("iracp", "10(2)", "10(3)", "97", "98", "99")], ("floating-provision-governance",)),
        clause("POL-ACP-002", "4.8", "Agricultural advances", "Crop-season norms apply to farm credit as notified by the State Level Bankers Committee.",
               "agri-banking", "2025-11-28", [m("iracp", "42(6)", "42(7)", "57")], ("crop-season-npa-calendar",)),
        clause("POL-ACP-002", "4.9", "Security valuation for large NPAs", "Stock audit annually and collateral valuation every three years for NPAs of INR 5 crore and above.",
               "credit-risk", "2025-11-28", [m("iracp", "22", "23")], ("collateral-valuation-stock-audit",)),
        clause("POL-ACP-002", "9.1", "Expected credit loss framework (to be adopted)",
               "Staging, SICR, PD/LGD/EAD, EIR, macro scenarios and prudential floors from 1 April 2027. Draft; no operating procedure yet.",
               "credit-risk", "2027-04-01", [m("acpir", "25", "26", "33", "45", "50", "82", "96", "97", "100")],
               ("ecl-staging-engine", "sicr-criteria-documentation", "pd-model", "lgd-model", "ead-model", "eir-computation-engine",
                "macro-scenario-framework", "prudential-floor-calculator", "ecl-transition-fair-valuation"), status="draft"),
    ]),
    policy("POL-FPC-003", "Fair Practices Code", "retail-lending", "4.1", "Customer Service Committee of the Board", "2025-09-30", "2025-10-01", "2026-09-30", [
        clause("POL-FPC-003", "1", "Loan applications and disclosures", "Plain-language terms, all charges disclosed before sanction.",
               "retail-lending", "2025-10-01", [m("fair_practices", "disclosure")], ("fair-practices-disclosure",)),
        clause("POL-FPC-003", "5", "Recovery conduct", "No harassment; contact hours; recording of calls by recovery agents.",
               "collections-recovery", "2025-10-01", [m("fair_practices", "recovery")], ("recovery-agent-conduct",)),
    ]),
    policy("POL-RRD-004", "Rate Reset and Disclosure Policy", "lending-ops", "3.0", "Retail Credit Committee", "2023-12-15", "2023-12-31", "2026-12-31", [
        clause("POL-RRD-004", "2", "Reset notification", "Borrowers are told the EMI or tenor impact of every benchmark reset.", "customer-comms", "2023-12-31",
               [m("floating_rate_reset", "Key provisions (1)", rules=("notify-rate-reset",))], ("borrower-notification",)),
        clause("POL-RRD-004", "3", "Choice at reset", "Borrowers may choose a higher EMI or a longer tenor, and may switch to a fixed rate.", "lending-ops", "2023-12-31",
               [m("floating_rate_reset", "Key provisions (2)", rules=("fixed-rate-choice",))], ("emi-tenor-choice", "fixed-rate-option")),
        clause("POL-RRD-004", "4", "Reset statement", "A statement of principal, interest, EMIs remaining and APR is issued at each reset.", "customer-comms", "2023-12-31",
               [m("floating_rate_reset", "Key provisions (3)", rules=("reset-statement",))], ("reset-statement",)),
    ]),
    policy("POL-COL-005", "Collateral and Document Custody Policy", "collateral-ops", "2.4", "Operations Risk Committee", "2023-11-20", "2023-12-01", "2026-11-30", [
        clause("POL-COL-005", "3", "Release within 30 days", "Original documents are released within 30 days of closure and charges are removed.", "collateral-ops",
               "2023-12-01", [m("document_release", "Release timeline", rules=("release-within-30-days",))], ("document-release-sla", "collateral-document-custody-register")),
        clause("POL-COL-005", "4", "Compensation for delay", "INR 5,000 per day for delay attributable to the bank.", "collateral-ops", "2023-12-01",
               [m("document_release", "Compensation for delay", rules=("delay-compensation",))], ("delay-compensation",)),
        clause("POL-COL-005", "5", "Place of collection", "Borrowers choose the branch of servicing or any office holding the documents.", "collateral-ops", "2023-12-01",
               [m("document_release", "Place of collection", rules=("collection-choice",))], ("document-collection-choice",)),
    ]),
    policy("POL-GRV-006", "Customer Grievance Policy", "customer-service", "3.2", "Customer Service Committee of the Board", "2025-06-30", "2025-07-01", "2026-06-30", [
        clause("POL-GRV-006", "2", "Registration, ageing and escalation", "Every complaint is registered, aged and escalated; Ombudsman references are tracked.",
               "customer-service", "2025-07-01", [m("ombudsman", "complaint handling")], ("grievance-redressal-tracker",)),
    ]),
    policy("POL-KYC-007", "KYC and AML Policy", "kyc-aml", "6.0", "Board of Directors", "2025-12-18", "2026-01-01", "2026-12-31", [
        clause("POL-KYC-007", "3", "Customer risk categorisation", "Customers are rated low, medium or high risk at onboarding and review.", "kyc-aml", "2019-04-01",
               [m("kyc_md", "risk categorisation")], ("kyc-risk-categorisation",)),
        clause("POL-KYC-007", "7", "Periodic KYC updation", "Re-KYC every 2 years (high risk), 8 years (medium) and 10 years (low), with reminders.", "kyc-aml", "2026-01-01",
               [m("kyc_md", "periodic updation", rules=())], ("kyc-periodic-updation",)),
        clause("POL-KYC-007", "9", "Transaction monitoring", "Scenario-based monitoring of accounts and transactions.", "kyc-aml", "2021-04-01",
               [m("kyc_md", "ongoing monitoring")], ("aml-transaction-monitoring",)),
    ]),
    policy("POL-MRM-008", "Model Risk Management Policy", "model-risk", "2.1", "Risk Management Committee of the Board", "2025-03-28", "2025-04-01", "2026-12-31", [
        clause("POL-MRM-008", "2", "Model inventory and validation (credit scorecards)", "Inventory, tiering and annual validation of credit scorecards.", "model-risk",
               "2025-04-01", [], ()),
        clause("POL-MRM-008", "8", "ECL model risk (addendum, to be approved)",
               "Inventory, tiering, validation, calibration, monitoring and third-party models for ECL. Draft addendum, not yet approved.", "model-risk", "2027-04-01",
               [m("acpir", "119", "120", "121", "123", "130", "131", "133", "135", "136")],
               ("ecl-model-inventory-tiering", "ecl-model-validation-framework", "ecl-model-monitoring", "third-party-model-governance"), status="draft"),
        clause("POL-MRM-008", "9", "Board governance of ECL", "A Board committee with the CFO and CRO oversees ECL implementation. Draft.", "compliance", "2027-04-01",
               [m("acpir", "56", "57")], ("ecl-board-governance",), status="draft"),
    ]),
    policy("POL-REC-009", "Recovery and Restructuring Policy", "collections-recovery", "4.3", "Board Credit Committee", "2026-06-24", "2026-07-01", "2027-06-30", [
        clause("POL-REC-009", "5", "Resolution plans under Chapter VI-A", "Resolution of stressed accounts after a calamity; register, flagging and provisioning of the plan.", "collections-recovery",
               "2026-07-01", [m("resolution_stressed_assets", "Chapter VI-A"), m("iracp", "62A", "62B", "84A", "84B")],
               ("resolution-plan-register", "resolution-plan-provisioning")),
        clause("POL-REC-009", "7", "Recovery agencies", "Appointment, monitoring and conduct of recovery agencies.", "collections-recovery", "2023-04-01",
               [m("fair_practices", "recovery")], ("recovery-agent-conduct",)),
    ]),
    policy("POL-OUT-010", "Outsourcing Policy", "vendor-management", "3.0", "Board of Directors", "2023-09-29", "2023-10-01", "2026-09-30", [
        clause("POL-OUT-010", "4", "Register, due diligence and supervisory access", "Material outsourcing is registered, assessed and open to supervisory evaluation.",
               "vendor-management", "2023-10-01", [m("outsourcing_it", "outsourcing governance")], ("outsourcing-due-diligence-register",)),
    ]),
    policy("POL-DAT-011", "Data Governance and Regulatory Reporting Policy", "finance", "2.0", "Audit Committee of the Board", "2025-03-28", "2025-04-01", "2026-03-31", [
        clause("POL-DAT-011", "3", "Regulatory MIS and returns", "System-generated NPA, provision and restructuring MIS reconciled to returns; CRILC filings on time.", "finance",
               "2025-04-01", [m("iracp", "11", "29", "33", "34")], ("npa-mis-reporting", "crilc-reporting")),
        clause("POL-DAT-011", "8", "Credit data for ECL (planned)", "History of defaults, recoveries and losses at account level, for PD and LGD calibration.", "it-core-banking",
               "2027-04-01", [m("acpir", "62", "63", "64", "70", "71")], ("ecl-historical-loss-database", "ecl-disclosure-reporting"), status="draft"),
        clause("POL-DAT-011", "9", "Regulatory change management", "Compliance logs and assesses each RBI instruction and assigns owners.", "compliance", "2025-04-01",
               [], ("regulatory-change-tracking",)),
    ]),
    policy("POL-AGR-012", "Agricultural and MSME Lending Policy", "agri-banking", "2.2", "Board Credit Committee", "2025-09-30", "2025-10-01", "2026-09-30", [
        clause("POL-AGR-012", "3", "Crop-season norms", "Overdue short-duration crop loans are NPA after two crop seasons, long-duration after one.", "agri-banking", "2025-10-01",
               [m("iracp", "42(6)", "42(7)", "57")], ("crop-season-npa-calendar",)),
        clause("POL-AGR-012", "6", "Guarantee-covered MSME advances", "No provision on the portion covered by CGTMSE, CRGFTLIH or NCGTC guarantees.", "msme-banking", "2025-10-01",
               [m("iracp", "111")], ()),
    ]),
    policy("POL-ICA-013", "Internal Audit and Compliance Testing Policy", "internal-audit", "3.1", "Audit Committee of the Board", "2025-09-30", "2025-10-01", "2026-09-30", [
        clause("POL-ICA-013", "5", "Annual system audit of the NPA system", "Internal or external system auditors test parameters and compliance every year.", "internal-audit",
               "2025-10-01", [m("iracp", "39(3)")], ("npa-system-audit", "board-iracp-policy")),
        clause("POL-ICA-013", "6", "Concurrent audit of credit processes", "Review and renewal of credit facilities is within concurrent audit scope.", "internal-audit", "2025-10-01",
               [m("iracp", "20")], ()),
    ]),
]


# ----------------------------------------------------------------------------------------------------- outputs
def library() -> dict:
    return {"instruments": INSTRUMENTS, "controls": CONTROLS, "policies": POLICIES}


def write_library(directory: Path = BANK) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "controls.json").write_text(json.dumps({"synthetic": True, "controls": CONTROLS}, indent=1) + "\n")
    (directory / "policies.json").write_text(json.dumps({"synthetic": True, "instruments": INSTRUMENTS, "policies": POLICIES}, indent=1) + "\n")


def rule_id(instrument: str, suffix: str) -> str:
    return f"rule:{INSTRUMENTS[instrument]['reference']}:{suffix}"


def library_nodes_and_edges(product_ids: list[str]) -> tuple[list[dict], list[dict]]:
    """Graph nodes and edges for the internal policies and the control library."""
    def node(node_id, node_type, name, **attributes):
        return {"id": node_id, "type": node_type, "name": name, "attributes": attributes}

    def edge(source, target, edge_type, valid_from, valid_to=None, **attributes):
        return {"source": source, "target": target, "type": edge_type, "valid_from": valid_from, "valid_to": valid_to, "attributes": attributes}

    nodes, edges = [], []
    for c in CONTROLS:
        attributes = {k: v for k, v in c.items() if k not in ("id", "name") and v not in (None, [], "")}
        attributes["synonyms"] = c["synonyms"]
        nodes.append(node(c["id"], "Control", c["name"], **attributes))
        status = c["implementation_status"]
        go_live = c["implemented_from"] or None
        if status == "implemented" and go_live:  # a partial control is not provided by every product, so it creates no edge
            targets = product_ids if c["applies_to"] == "all" and c["kind"] != PRODUCT else (
                [] if c["kind"] == PRODUCT else c["applies_to"])
            edges += [edge(pid, c["id"], "PROVIDES", go_live) for pid in targets]
    for p in POLICIES:
        nodes.append(node(p["policy_id"], "Policy", p["title"], kind="internal", policy_code=p["code"], version=p["version"],
                          owner_team=p["owner_team"], approved_by=p["approved_by"], approved_on=p["approved_on"], effective_date=p["effective_date"],
                          next_review=p["next_review"], status=p["status"],
                          clauses=[{"clause_id": c["clause_id"], "title": c["title"], "owner_team": c["owner_team"], "effective_from": c["effective_from"],
                                    "status": c["status"], "maps_to": c["maps_to"], "controls": c["controls"]} for c in p["clauses"]]))
        enforced: dict[str, str] = {}
        implemented: dict[str, dict] = {}
        for c in p["clauses"]:
            for control_id in c["controls"]:
                spec = next(x for x in CONTROLS if x["id"] == control_id)
                start = spec["implemented_from"] or spec["planned_go_live"]
                if spec["implementation_status"] == "absent" or not start:
                    continue  # nothing enforces an absent control yet
                enforced[control_id] = min(enforced.get(control_id, start), start)
            for mapping in c["maps_to"]:
                for suffix in mapping["rules"]:
                    target = rule_id(mapping["instrument"], suffix)
                    entry = implemented.setdefault(target, {"valid_from": c["effective_from"], "clauses": [], "paragraphs": []})
                    entry["valid_from"] = min(entry["valid_from"], c["effective_from"])
                    entry["clauses"].append(c["clause_id"])
                    entry["paragraphs"] += mapping["paragraphs"]
        edges += [edge(p["policy_id"], cid, "ENFORCES", start) for cid, start in sorted(enforced.items())]
        for target, entry in sorted(implemented.items()):
            edges.append(edge(p["policy_id"], target, "IMPLEMENTS", entry["valid_from"], clauses=sorted(set(entry["clauses"])),
                              paragraphs=sorted(set(entry["paragraphs"]))))
    return nodes, edges
