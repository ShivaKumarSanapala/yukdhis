"""Local PII detectors (offline stand-ins for Cloud DLP and Gemma).

Pass 1 is deterministic regex for structured identifiers.  Pass 2 is a small
contextual heuristic that emulates what the Gemma redactor catches in VPC
(names after honorifics/relationship words, "account ending in ...").
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    text: str
    entity_type: str
    source: str


STRUCTURED = [
    ("EMAIL_ADDRESS", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("INDIA_PAN", re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b|\b[A-Z]{4}X[0-9]{4}[A-Z]\b")),
    ("INDIA_AADHAAR", re.compile(r"\b[2-9][0-9]{3}\s?[0-9]{4}\s?[0-9]{4}\b")),
    ("CREDIT_CARD_NUMBER", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("PHONE_NUMBER", re.compile(r"(?:\+91[-\s]?)?\b[6-9]\d{4}[-\s]?\d{5}\b")),
    ("IFSC_CODE", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
    ("ACCOUNT_NUMBER", re.compile(r"\b(?:a/c|acct|account)(?:\s+(?:no\.?|number))?\s*[:#]?\s*(\d{9,18})\b", re.IGNORECASE)),
]
_NAME = r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})"
CONTEXTUAL = [
    ("PERSON_NAME", re.compile(rf"\b(?i:Mr|Mrs|Ms|Dr|Shri|Smt)\.?\s+{_NAME}")),
    ("PERSON_NAME", re.compile(rf"\b(?i:customer|borrower|applicant|guarantor|my (?:wife|husband|son|daughter|father|mother)|named|called)\s+{_NAME}")),
    ("PERSON_NAME", re.compile(rf"\b(?:I am|I'm|this is|name is)\s+{_NAME}")),
    ("ACCOUNT_FRAGMENT", re.compile(r"\b(?:account|card|loan)\s+ending\s+(?:in|with)\s+([\w-]{2,12})\b", re.IGNORECASE)),
    ("STREET_ADDRESS", re.compile(r"\b(\d{1,4},?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:Road|Street|Lane|Nagar|Layout|Colony|Cross|Main))\b")),
]
_NOT_NAMES = {"Bank", "Branch", "Loan", "Loans", "Reserve", "India", "Customer", "The", "This", "Policy", "Rule"}


def structured_findings(text: str) -> list[Finding]:
    found: list[Finding] = []
    for entity_type, pattern in STRUCTURED:
        for match in pattern.finditer(text):
            value = match.group(1) if match.groups() else match.group(0)
            if entity_type == "CREDIT_CARD_NUMBER" and len(re.sub(r"\D", "", value)) < 13:
                continue
            found.append(Finding(value.strip(), entity_type, "regex"))
    return found


def contextual_findings(text: str) -> list[Finding]:
    found: list[Finding] = []
    for entity_type, pattern in CONTEXTUAL:
        for match in pattern.finditer(text):
            value = match.group(1).strip()
            if entity_type == "PERSON_NAME" and value.split()[0] in _NOT_NAMES:
                continue
            found.append(Finding(value, entity_type, "contextual"))
    return found
