"""Deterministic rule extraction for the prototype circular format.

An LLM/Document AI stage may turn PDFs into this normalised markdown, but the
compliance requirements below are intentionally parsed by code so a critical
rule cannot change with model wording.
"""

from __future__ import annotations

import re
from pathlib import Path

from knowledge_graph.models import RegulatoryRule


def parse_floating_rate_reset_circular(path: str | Path) -> list[RegulatoryRule]:
    text = Path(path).read_text(encoding="utf-8")
    reference = _find(r"Reference:\s*([^\n]+)", text, "RBI/UNKNOWN")
    effective = _find(r"Effective:\s*([^\n.]+)", text, "Not stated")
    title = _find(r"^#\s+(.+)$", text, "Floating-rate reset requirements")
    rules: list[RegulatoryRule] = []
    rule_specs = [
        ("notify-rate-reset", "Borrowers must be told the impact on EMI and/or tenor.", ("borrower_notification", "emi_or_tenor_choice"), "impact on EMI and/or tenor"),
        ("fixed-rate-choice", "Borrowers must be offered a fixed-rate switch.", ("fixed_rate_option",), "option to switch to a fixed rate"),
        ("reset-statement", "Borrowers must receive a reset statement with recovery and APR details.", ("reset_statement",), "statement of principal and interest recovered"),
    ]
    for suffix, description, requirements, evidence in rule_specs:
        if evidence.lower() in text.lower():
            rules.append(RegulatoryRule(
                rule_id=f"{reference}:{suffix}", circular_id=reference, title=description,
                effective_date=effective, applies_to=("FLOATING", "EMI_BASED", "RETAIL_LOAN"),
                requirements=requirements, evidence=evidence,
            ))
    if not rules:
        raise ValueError("No supported, auditable policy requirements found in document.")
    return rules


def _find(pattern: str, text: str, default: str) -> str:
    match = re.search(pattern, text, flags=re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else default
