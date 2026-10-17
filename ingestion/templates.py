"""Deterministic extractors for circular templates the bank has already approved.

When a known circular is ingested, these produce the reference extraction.
The Reader agent's Gemini output is cross-checked against it, and the
deterministic pipeline (AGENT_MODE=deterministic) uses it directly, so a
critical rule never depends on model wording.
"""

from __future__ import annotations

import re
from typing import Callable

from core.models import (ComplianceCheck, ExtractedPolicy, ParsedDocument, Policy, ProductScope, Rule, SourceCitation,
                         UnexecutableObligation)


def _find(pattern: str, text: str, default: str) -> str:
    match = re.search(pattern, text, flags=re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else default


def _cite(doc: ParsedDocument, phrase: str) -> SourceCitation | None:
    for chunk in doc.chunks:
        index = chunk.content.lower().find(phrase.lower())
        if index >= 0:
            line_start = chunk.content.rfind("\n", 0, index) + 1
            line_end = chunk.content.find("\n", index)
            snippet = chunk.content[line_start: line_end if line_end > 0 else None].lstrip("- ").strip()
            return SourceCitation(document_id=doc.document_id, chunk_id=chunk.chunk_id, page=chunk.page_start, snippet=snippet)
    return None


def floating_rate_reset(doc: ParsedDocument) -> ExtractedPolicy:
    text = doc.text
    reference = _find(r"Reference:\s*([^\n]+)", text, "DOR.MCS.REC.32/01.01.003/2023-24")
    policy = Policy(policy_id=f"policy:{reference}", title="Reset of Floating Interest Rate on EMI based Personal Loans",
                    issuer="RBI", reference=reference, issued_on="2023-08-18", effective_date="2023-12-31",
                    source_uri=doc.source_uri)
    scope = ProductScope(product_terms=["EMI based loans", "floating rate loans"], rate_type="FLOATING",
                         instalment_based=True, category="RETAIL_LOAN", record_status="ACTIVE")
    specs = [
        ("notify-rate-reset", "Notify borrowers of EMI/tenor impact on rate reset",
         "When the benchmark changes, borrowers must be told the impact on EMI and/or tenor.", "impact on EMI and/or tenor",
         ["benchmark rate reset", "borrower correspondence"], "HIGH",
         [ComplianceCheck(kind="record_flag", description="Borrower notified of reset impact", record_field="borrower_notified", expected=True),
          ComplianceCheck(kind="control_required", description="Product offers EMI or tenor choice", capability="emi_or_tenor_choice")]),
        ("fixed-rate-choice", "Offer switch to fixed rate at reset",
         "Borrowers must be given the option to switch to a fixed rate.", "option to switch to a fixed rate",
         ["benchmark rate reset"], "HIGH",
         [ComplianceCheck(kind="control_required", description="Product offers fixed-rate conversion", capability="fixed_rate_option"),
          ComplianceCheck(kind="record_flag", description="Loan carries a fixed-rate switch option", record_field="has_fixed_rate_option", expected=True)]),
        ("reset-statement", "Issue reset statement with principal, interest and APR",
         "Lenders must send a statement of principal and interest recovered, EMIs remaining and APR.",
         "statement of principal and interest recovered", ["borrower correspondence"], "MEDIUM",
         [ComplianceCheck(kind="control_required", description="Product issues reset statements", capability="reset_statement"),
          ComplianceCheck(kind="record_flag", description="Reset statement available to borrower", record_field="statement_available", expected=True)]),
    ]
    return _assemble(doc, policy, reference, scope, specs, segments=["individual borrowers"])


def property_document_release(doc: ParsedDocument) -> ExtractedPolicy:
    text = doc.text
    reference = _find(r"Reference:\s*([^\n]+)", text, "DoR.MCS.REC.38/01.01.001/2023-24")
    policy = Policy(policy_id=f"policy:{reference}", title="Release of Movable / Immovable Property Documents on Repayment",
                    issuer="RBI", reference=reference, issued_on="2023-09-13", effective_date="2023-12-01",
                    source_uri=doc.source_uri)
    scope = ProductScope(product_terms=["personal loans", "movable immovable property"], secured=True, category="RETAIL_LOAN")
    specs = [
        ("release-within-30-days", "Release original property documents within 30 days of closure",
         "All original movable/immovable property documents must be released within 30 days of full repayment or settlement.",
         "within a period of 30 days after full repayment", ["loan closure", "collateral release"], "CRITICAL",
         [ComplianceCheck(kind="event_deadline", description="Documents released within 30 days of closure",
                          trigger_event="LOAN_CLOSED", required_event="DOCUMENTS_RELEASED", deadline_days=30,
                          penalty_per_day_inr=5000),
          ComplianceCheck(kind="control_required", description="30-day release tracker in place", capability="document_release_sla")]),
        ("delay-compensation", "Compensate borrowers ₹5,000 per day of delay",
         "Where delay beyond 30 days is attributable to the lender, compensate ₹5,000 per day.",
         "₹5,000 for each day of delay", ["collateral release"], "HIGH",
         [ComplianceCheck(kind="control_required", description="Automatic delay compensation", capability="delay_compensation")]),
        ("collection-choice", "Let borrower choose the document collection location",
         "Borrowers may collect documents from the servicing branch or any other office holding them.",
         "option of collecting the original documents", ["original document custody"], "MEDIUM",
         [ComplianceCheck(kind="control_required", description="Collection-location choice offered", capability="document_collection_choice")]),
    ]
    return _assemble(doc, policy, reference, scope, specs, segments=["individual borrowers"])


# ---------------------------------------------------------------------------------------------------------------------
# IRACP Directions, 2025 (RBI/DOR/2025-26/164): the incurred-loss regime in force until the ACPIR Directions take over on
# 1 April 2027. Every check is a ``record_assertion`` on the sanitized view ``vw_asset_quality`` (columns documented in
# data/ontology/packs/provisioning_iracp.json). One function serves both versions of the Directions in the corpus: a rule
# whose text is absent from the version being read (for example para 84A before 1 July 2026) is simply not produced.
# ---------------------------------------------------------------------------------------------------------------------
IRACP_VIEW = "vw_asset_quality"
STANDARD_CLASSES = "('STANDARD', 'SMA0', 'SMA1', 'SMA2')"
NPA_CLASSES = "('SUBSTANDARD', 'DOUBTFUL', 'LOSS')"
LOW_RATE_CLASSES = "('INDIVIDUAL_HOUSING', 'FARM_CREDIT', 'SMALL_MICRO_ENTERPRISE')"
HIGH_RATE_CLASSES = "('MEDIUM_ENTERPRISE', 'OTHER')"
AMENDMENT_EFFECTIVE = "2026-07-01"
IRACP_SCOPE = ProductScope(product_terms=["loans and advances"])


def _assertion(description: str, where: str | None, assertion: str, tolerance: float | None = None) -> ComplianceCheck:
    return ComplianceCheck(kind="record_assertion", description=description, view=IRACP_VIEW, where=where, assertion=assertion,
                           tolerance=tolerance)


# (suffix, title, description, evidence phrase, processes, risk, checks, effective date or None for the policy date)
IRACP_RULES = [
    ("npa-classification-90-days", "Classify an advance as NPA when overdue for more than 90 days",
     "A term loan with interest or an instalment overdue for more than 90 days must be classified as a non-performing asset. "
     "Farm credit follows the crop-season norms of paragraph 42(6) and (7).",
     "remains overdue for a period of more than 90 days in respect of a term loan", ["day-end asset classification"], "CRITICAL",
     [_assertion("Accounts more than 90 days past due are classified NPA",
                 "days_past_due > 90 AND exposure_class <> 'FARM_CREDIT'", f"asset_classification IN {NPA_CLASSES}")], None),
    ("sma-reporting-bands", "Tag overdue accounts SMA-0, SMA-1 or SMA-2 in the day-end process",
     "Classification as SMA or NPA is done in the day-end process: SMA-0 from the first overdue day, SMA-1 after 30 and SMA-2 after 60 "
     "days of continuous overdue, NPA after 90.",
     "classification of borrower accounts as SMA as well as NPA shall be done as part of day-end process", ["day-end asset classification"],
     "HIGH",
     [_assertion("Accounts 1 to 30 days past due are tagged SMA-0", "days_past_due >= 1 AND days_past_due <= 30", "asset_classification = 'SMA0'"),
      _assertion("Accounts 31 to 60 days past due are tagged SMA-1", "days_past_due >= 31 AND days_past_due <= 60", "asset_classification = 'SMA1'"),
      _assertion("Accounts 61 to 90 days past due are tagged SMA-2", "days_past_due >= 61 AND days_past_due <= 90", "asset_classification = 'SMA2'")],
     None),
    ("standard-asset-provision-025", "Hold 0.25 per cent on standard farm credit, individual housing and small and micro enterprise advances",
     "General provision on standard assets at 0.25 per cent of the funded outstanding for farm credit, individual housing loans and "
     "small and micro enterprises.",
     "farm credit to agricultural activities, individual housing loans and Small and Micro Enterprises (SMEs) sectors at 0.25 per cent",
     ["provisioning and loss allowance"], "HIGH",
     [_assertion("Standard accounts in the 0.25 per cent sectors carry at least 0.25 per cent provision",
                 f"asset_classification IN {STANDARD_CLASSES} AND exposure_class IN {LOW_RATE_CLASSES}",
                 "provision_held * 10000 >= outstanding_balance * 25")], None),
    ("standard-asset-provision-040", "Hold 0.40 per cent on all other standard advances",
     "General provision on all other standard loans and advances, including loans to medium enterprises, at 0.40 per cent.",
     "loans and advances not included in", ["provisioning and loss allowance"], "HIGH",
     [_assertion("Other standard accounts carry at least 0.40 per cent provision",
                 f"asset_classification IN {STANDARD_CLASSES} AND exposure_class IN {HIGH_RATE_CLASSES}",
                 "provision_held * 10000 >= outstanding_balance * 40")], None),
    ("substandard-provision-secured-15", "Provide 15 per cent on substandard assets",
     "A general provision of 15 per cent of the total outstanding on substandard assets, without allowance for guarantee cover or security.",
     "A general provision of 15 per cent on total outstanding shall be made in respect of substandard assets",
     ["provisioning and loss allowance"], "CRITICAL",
     [_assertion("Substandard secured accounts carry at least 15 per cent provision", "asset_classification = 'SUBSTANDARD' AND secured = true",
                 "provision_held * 100 >= outstanding_balance * 15")], None),
    ("substandard-provision-unsecured-25", "Provide 25 per cent on unsecured substandard exposures",
     "Unsecured exposures identified as substandard attract an additional 10 per cent, a total of 25 per cent of the outstanding balance.",
     "shall attract additional provision of 10 per cent, i.e., a total of 25 per cent on the outstanding balance",
     ["provisioning and loss allowance"], "CRITICAL",
     [_assertion("Substandard unsecured accounts carry at least 25 per cent provision", "asset_classification = 'SUBSTANDARD' AND secured = false",
                 "provision_held * 100 >= outstanding_balance * 25")], None),
    ("doubtful-unsecured-portion-100", "Provide 100 per cent on the unsecured portion of doubtful assets",
     "On a doubtful asset, 100 per cent of the extent to which the advance is not covered by the realisable value of the security.",
     "A bank shall make provisions of 100 per cent of the extent to which the advance is not covered by the realisable value of the security",
     ["provisioning and loss allowance"], "CRITICAL",
     [_assertion("Doubtful accounts hold at least the unsecured portion", "asset_classification = 'DOUBTFUL'",
                 "provision_held >= unsecured_portion")], None),
    ("doubtful-secured-portion-ageing", "Provide 25, 40 or 100 per cent on the secured portion of doubtful assets by age",
     "Secured portion of a doubtful asset: 25 per cent up to one year in doubtful, 40 per cent from one to three years, 100 per cent after three "
     "years (under 24 months, 24 to 47 months, 48 months or more as NPA).",
     "In regard to the secured portion, provision shall be made on the following basis", ["provisioning and loss allowance"], "CRITICAL",
     [_assertion("Doubtful accounts up to one year in doubtful hold the unsecured portion plus 25 per cent of the secured portion",
                 "asset_classification = 'DOUBTFUL' AND npa_age_months < 24",
                 "provision_held * 100 >= unsecured_portion * 100 + secured_portion * 25"),
      _assertion("Doubtful accounts one to three years in doubtful hold the unsecured portion plus 40 per cent of the secured portion",
                 "asset_classification = 'DOUBTFUL' AND npa_age_months >= 24 AND npa_age_months < 48",
                 "provision_held * 100 >= unsecured_portion * 100 + secured_portion * 40"),
      _assertion("Doubtful accounts more than three years in doubtful are fully provided",
                 "asset_classification = 'DOUBTFUL' AND npa_age_months >= 48", "provision_held >= outstanding_balance")], None),
    ("loss-asset-provision-100", "Provide 100 per cent on loss assets kept on the books",
     "If loss assets are permitted to remain in the books, 100 per cent of the outstanding must be provided for.",
     "If loss assets are permitted to remain in the books for any reason, 100 per cent of the outstanding should be provided for",
     ["provisioning and loss allowance"], "CRITICAL",
     [_assertion("Loss assets are fully provided", "asset_classification = 'LOSS'", "provision_held >= outstanding_balance")], None),
    ("npa-income-cash-basis", "Recognise income on non-standard accounts on cash basis only",
     "Income on credit facilities that are not standard is recognised on actual receipt, that is on a cash basis.",
     "income shall be recognized on actual receipt basis i.e. cash basis", ["interest income recognition"], "HIGH",
     [_assertion("Interest on NPAs is recognised on cash basis", f"asset_classification IN {NPA_CLASSES}", "interest_recognition = 'CASH'")], None),
]

# Inserted by the IRACP Amendment Directions, 2026 (RBI/2026-27/45), effective 1 July 2026.
AMENDMENT_RULES = [
    ("resolution-plan-classification-62a-62b", "Keep standard accounts standard on implementation of a resolution plan",
     "Standard accounts are retained as standard when a resolution plan under Chapter VI-A is implemented; accounts restructured again "
     "under that chapter continue as standard. Later classification follows the Directions.",
     "may be retained as such upon implementation", ["resolution plans and restructuring"], "HIGH",
     [_assertion("Accounts under a resolution plan that are not 90 days overdue are not classified NPA",
                 "restructured = true AND days_past_due <= 90", f"asset_classification IN {STANDARD_CLASSES}")], AMENDMENT_EFFECTIVE),
    ("resolution-plan-additional-provision-84a", "Hold an additional 5 per cent where a resolution plan has been implemented",
     "An additional specific provision of 5 per cent of the outstanding debt for borrowers with a resolution plan, over and above the "
     "applicable prudential provision, up to 100 per cent.",
     "A bank shall make an additional specific provision of five percent of the outstanding debt in respect of borrowers",
     ["provisioning and loss allowance", "resolution plans and restructuring"], "HIGH",
     [_assertion("Standard accounts in the 0.25 per cent sectors with one resolution plan carry 0.25 per cent plus 5 per cent",
                 f"restructure_count = 1 AND asset_classification IN {STANDARD_CLASSES} AND exposure_class IN {LOW_RATE_CLASSES}",
                 "provision_held * 10000 >= outstanding_balance * (25 + 500 * restructure_count)"),
      _assertion("Other standard accounts with one resolution plan carry 0.40 per cent plus 5 per cent",
                 f"restructure_count = 1 AND asset_classification IN {STANDARD_CLASSES} AND exposure_class IN {HIGH_RATE_CLASSES}",
                 "provision_held * 10000 >= outstanding_balance * (40 + 500 * restructure_count)")], AMENDMENT_EFFECTIVE),
    ("repeated-restructuring-additional-provision-84b", "Hold 5 per cent for each instance of repeated restructuring",
     "Where repeated restructuring is necessitated, an additional specific provision of 5 per cent of the outstanding debt for each instance "
     "of restructuring, over and above the prudential provision, up to 100 per cent.",
     "a bank shall make additional specific provisioning of five per cent of the outstanding debt for each instance of restructuring",
     ["provisioning and loss allowance", "resolution plans and restructuring"], "HIGH",
     [_assertion("Standard accounts in the 0.25 per cent sectors restructured more than once carry 5 per cent per instance",
                 f"restructure_count >= 2 AND asset_classification IN {STANDARD_CLASSES} AND exposure_class IN {LOW_RATE_CLASSES}",
                 "provision_held * 10000 >= outstanding_balance * (25 + 500 * restructure_count)"),
      _assertion("Other standard accounts restructured more than once carry 5 per cent per instance",
                 f"restructure_count >= 2 AND asset_classification IN {STANDARD_CLASSES} AND exposure_class IN {HIGH_RATE_CLASSES}",
                 "provision_held * 10000 >= outstanding_balance * (40 + 500 * restructure_count)")], AMENDMENT_EFFECTIVE),
    ("resolution-plan-income-accrual-139a", "Recognise income on accrual basis after a resolution plan",
     "Interest income on borrower accounts where a resolution plan has been implemented is recognised on an accrual basis.",
     "Interest income recognition in respect of borrower accounts where resolution plan has been implemented",
     ["interest income recognition", "resolution plans and restructuring"], "MEDIUM",
     [_assertion("Standard accounts with one resolution plan are on accrual basis",
                 f"restructure_count = 1 AND asset_classification IN {STANDARD_CLASSES}", "interest_recognition = 'ACCRUAL'")], AMENDMENT_EFFECTIVE),
    ("restructured-again-income-cash-basis-139b", "Recognise income on cash basis for accounts restructured again",
     "For accounts that continue as standard after a subsequent restructuring (paragraph 62B), interest income is recognised on a cash basis.",
     "For accounts specified at paragraph 62B of these Directions, interest income shall be recognized on cash basis",
     ["interest income recognition", "resolution plans and restructuring"], "HIGH",
     [_assertion("Accounts restructured more than once are on cash basis", "restructure_count >= 2", "interest_recognition = 'CASH'")],
     AMENDMENT_EFFECTIVE),
]

# Obligations of the Directions that no check on the onboarded data can test. Reported, never turned into a fake check.
IRACP_UNEXECUTABLE = [
    ("A bank shall frame a Board approved policy for the implementation of the regulations contained in this Chapter",
     "A Board-approved policy is a document, not a record in the loan book.", ["board_policy_register"]),
    ("All borrowal accounts, including temporary overdrafts, irrespective of size, sector or types of limits, shall be covered in the automated IT based system",
     "System coverage cannot be read from the loan book; it needs an inventory of accounts outside the classification system.",
     ["cbs_system_coverage_inventory"]),
    ("There shall be periodic system audit, at least once in a year",
     "Audit reports are not onboarded.", ["system_audit_reports"]),
    ("the instalment of principal or interest thereon remains overdue for two crop seasons for short duration crops",
     "Needs the State Level Bankers Committee crop-season calendar by crop and state, which is not onboarded.", ["slbc_crop_season_calendar"]),
    ("Stock audit at annual intervals by external agencies appointed as per the guidelines approved by the Board shall be mandatory in cases of NPAs",
     "Needs the stock audit register for NPAs of INR 5 crore and above.", ["stock_audit_register"]),
]


def _iracp_rules(doc: ParsedDocument, specs: list, policy: Policy, reference: str) -> list[Rule]:
    rules = []
    for suffix, title, description, evidence, processes, risk, checks, effective in specs:
        citation = _cite(doc, evidence)
        if citation is None:
            continue  # the rule is not in this version of the Directions
        rules.append(Rule(rule_id=f"rule:{reference}:{suffix}", title=title, description=description,
                          effective_date=effective or policy.effective_date, scope=IRACP_SCOPE, processes=processes, segments=[],
                          checks=[c.model_copy(deep=True) for c in checks], penalty_risk=risk, citation=citation, confidence=1.0))
    return rules


def iracp_directions(doc: ParsedDocument) -> ExtractedPolicy:
    """The Income Recognition, Asset Classification and Provisioning Directions, 2025: the January 2026 and July 2026 versions."""
    reference = _find(r"\b(RBI/DOR/\d{4}-\d{2}/\d+)", doc.text, "RBI/DOR/2025-26/164")
    policy = Policy(policy_id=f"policy:{reference}", title="Commercial Banks - Income Recognition, Asset Classification and Provisioning Directions, 2025",
                    issuer="RBI", reference=reference, issued_on="2025-11-28", effective_date="2025-11-28", source_uri=doc.source_uri)
    rules = _iracp_rules(doc, [*IRACP_RULES, *AMENDMENT_RULES], policy, reference)
    unexecutable = []
    for phrase, reason, missing in IRACP_UNEXECUTABLE:
        citation = _cite(doc, phrase)
        if citation:
            unexecutable.append(UnexecutableObligation(citation=citation.snippet, reason=reason, missing_data=missing))
    if not rules:
        raise ValueError("Template matched but no auditable requirement text was found.")
    return ExtractedPolicy(policy=policy, rules=rules, unexecutable=unexecutable)


def iracp_amendment_2026(doc: ParsedDocument) -> ExtractedPolicy:
    """The IRACP Amendment Directions, 2026: only the obligations the amendment inserts (62A/62B, 84A, 84B, 139A, 139B).

    The deletions of paragraphs 57(4) and 80(6) are not obligations. 84C and 84D only permit write-backs, so they are not rules."""
    reference = _find(r"\b(RBI/\d{4}-\d{2}/\d+)", doc.text, "RBI/2026-27/45")
    policy = Policy(policy_id=f"policy:{reference}",
                    title="Commercial Banks - Income Recognition, Asset Classification and Provisioning Amendment Directions, 2026",
                    issuer="RBI", reference=reference, issued_on="2026-04-29", effective_date=AMENDMENT_EFFECTIVE, source_uri=doc.source_uri)
    rules = _iracp_rules(doc, AMENDMENT_RULES, policy, reference)
    if not rules:
        raise ValueError("Template matched but no auditable requirement text was found.")
    return ExtractedPolicy(policy=policy, rules=rules)


def _assemble(doc, policy, reference, scope, specs, segments) -> ExtractedPolicy:
    rules = []
    for suffix, title, description, evidence, processes, risk, checks in specs:
        citation = _cite(doc, evidence)
        if citation is None:
            continue
        rules.append(Rule(rule_id=f"rule:{reference}:{suffix}", title=title, description=description,
                          effective_date=policy.effective_date, scope=scope, processes=processes, segments=segments,
                          checks=checks, penalty_risk=risk, citation=citation, confidence=1.0))
    if not rules:
        raise ValueError("Template matched but no auditable requirement text was found.")
    return ExtractedPolicy(policy=policy, rules=rules)


TEMPLATES: list[tuple[re.Pattern, Callable[[ParsedDocument], ExtractedPolicy]]] = [
    # The consolidated IRACP text also quotes the amendment in its footnotes, so the master Direction (matched on its short-title
    # paragraph) comes first. The ACPIR Directions only cite the IRACP title; they do not carry this paragraph.
    (re.compile(r"These directions shall be called the Reserve Bank of India \(Commercial Banks\s*[–-]\s*Income Recognition, Asset "
                r"Classification and Provisioning\) Directions, 2025", re.IGNORECASE), iracp_directions),
    (re.compile(r"Provisioning\) Amendment Directions, 2026.{0,3000}?Paragraph 57\(4\) shall be deleted", re.IGNORECASE | re.DOTALL),
     iracp_amendment_2026),
    (re.compile(r"DOR\.MCS\.REC\.32/01\.01\.003/2023-24|floating interest rate on EMI", re.IGNORECASE), floating_rate_reset),
    (re.compile(r"DoR\.MCS\.REC\.38/01\.01\.001/2023-24|Release of Movable\s*/\s*Immovable Property Documents", re.IGNORECASE), property_document_release),
]


def match_template(doc: ParsedDocument) -> Callable[[ParsedDocument], ExtractedPolicy] | None:
    text = doc.text
    return next((extractor for pattern, extractor in TEMPLATES if pattern.search(text)), None)
