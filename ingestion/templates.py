"""Deterministic extractors for circular templates the bank has already approved.

When a known circular is ingested, these produce the reference extraction.
The Reader agent's Gemini output is cross-checked against it, and the
deterministic pipeline (AGENT_MODE=deterministic) uses it directly, so a
critical rule never depends on model wording.
"""

from __future__ import annotations

import re
from typing import Callable

from core.models import ComplianceCheck, ExtractedPolicy, ParsedDocument, Policy, ProductScope, Rule, SourceCitation


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
    (re.compile(r"DOR\.MCS\.REC\.32/01\.01\.003/2023-24|floating interest rate on EMI", re.IGNORECASE), floating_rate_reset),
    (re.compile(r"DoR\.MCS\.REC\.38/01\.01\.001/2023-24|Release of Movable\s*/\s*Immovable Property Documents", re.IGNORECASE), property_document_release),
]


def match_template(doc: ParsedDocument) -> Callable[[ParsedDocument], ExtractedPolicy] | None:
    text = doc.text
    return next((extractor for pattern, extractor in TEMPLATES if pattern.search(text)), None)
