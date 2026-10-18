"""Deterministic checker in the maker-checker extraction loop.

Gemini (maker) proposes an ExtractedPolicy; this code (checker) decides
whether it is executable and grounded.  ERRORs are fed back to the model for a
retry; WARNINGs survive into the report for the compliance reviewer.
"""

from __future__ import annotations

import re
from datetime import date

from bank_mcp_server.assertions import validate_check
from core.models import ExtractedPolicy, ParsedDocument, ValidationIssue
from knowledge_graph.ontology import CAPABILITIES, EVENT_TYPES, RECORD_FLAGS

MIN_CONFIDENCE = 0.6
DRAFT_MESSAGE = "Citation describes a draft or proposal, not an operative provision."
DRAFT_PATTERN = re.compile(
    r"\b(draft\s+(directions?|circular|framework)|propos(ed|es)\b|invit(ed|es|ing)\s+(feedback|comments?)"
    r"|exposure\s+draft|had\s+issued\s+draft|stakeholders)", re.IGNORECASE)


NO_BINDING_PREFIX = "No binding obligations were found"


def non_binding_message(classification: dict | None = None, announced: dict | None = None) -> str:
    """User-facing reason a document yields no obligations (shared by the doc-type gate and the draft-citation check)."""
    doc_type = (classification or {}).get("doc_type")
    reason = (classification or {}).get("reason", "").rstrip(".")
    if doc_type == "press_release":
        message = f"{NO_BINDING_PREFIX}: this document is an RBI press release, not a binding instrument ({reason})."
        if announced and announced.get("url"):
            label = announced.get("ref") if announced.get("ref") != announced.get("url") else "the instrument"
            message += f" It announces {label}: {announced['url']}. Analyse that document instead."
        else:
            message += " Upload or fetch the Directions or circular it announces."
        return message
    if doc_type == "draft_or_consultation":
        return (f"{NO_BINDING_PREFIX}: this document is a draft or consultation paper ({reason}). "
                "Upload the final Directions once they are issued.")
    return f"{NO_BINDING_PREFIX}: this document appears to be an announcement or a draft. Upload the final Directions."


def _is_date(value: str | None) -> bool:
    try:
        date.fromisoformat(value or "")
        return True
    except ValueError:
        return False


def validate_extraction(extraction: ExtractedPolicy, document: ParsedDocument | None = None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    def add(severity, field, message, rule_id=None):
        issues.append(ValidationIssue(severity=severity, rule_id=rule_id, field=field, message=message))

    policy = extraction.policy
    if not _is_date(policy.effective_date):
        add("ERROR", "policy.effective_date", f"Not an ISO date: {policy.effective_date!r}")
    if not policy.reference.strip():
        add("ERROR", "policy.reference", "Circular reference number is required.")
    if not extraction.rules and not extraction.unexecutable and not extraction.analytical_objectives:
        add("ERROR", "rules", "No rules extracted.")
    seen: set[str] = set()
    source = " ".join((document.text if document else "").lower().split())
    for rule in extraction.rules:
        rid = rule.rule_id
        if rid in seen:
            add("ERROR", "rule_id", "Duplicate rule_id.", rid)
        seen.add(rid)
        if not _is_date(rule.effective_date):
            add("ERROR", "effective_date", f"Not an ISO date: {rule.effective_date!r}", rid)
        if not rule.checks:
            add("ERROR", "checks", "Rule has no executable checks.", rid)
        if document is not None and not _verbatim(rule.citation.snippet, source):
            add("ERROR", "citation.snippet", "Citation text is not found verbatim in the source document.", rid)
        if DRAFT_PATTERN.search(rule.citation.snippet):
            add("ERROR", "citation.snippet", DRAFT_MESSAGE, rid)
        if rule.confidence < MIN_CONFIDENCE:
            add("WARNING", "confidence", f"Low extraction confidence {rule.confidence:.2f}; requires reviewer sign-off.", rid)
        scope = rule.scope
        if not scope.product_terms and all(getattr(scope, a) is None for a in ("rate_type", "instalment_based", "secured", "collateral_type", "category")):
            add("ERROR", "scope", "Rule scope names no products and no attribute filters.", rid)
        for index, check in enumerate(rule.checks):
            where = f"checks[{index}]"
            if check.kind == "control_required":
                if not check.capability:
                    add("ERROR", where, "control_required needs a capability.", rid)
                elif check.capability not in CAPABILITIES:
                    add("WARNING", where, f"Capability {check.capability!r} is new to the ontology; the mapper will match it "
                        "to an existing control or create one pending review.", rid)
            elif check.kind == "record_flag":
                if check.record_field not in RECORD_FLAGS:
                    add("ERROR", where, f"record_field must be one of {sorted(RECORD_FLAGS)}.", rid)
                if check.expected is None:
                    add("ERROR", where, "record_flag needs the compliant 'expected' value.", rid)
            elif check.kind == "event_deadline":
                if check.trigger_event not in EVENT_TYPES or check.required_event not in EVENT_TYPES:
                    add("ERROR", where, f"Events must be in {sorted(EVENT_TYPES)}.", rid)
                if not check.deadline_days or check.deadline_days <= 0:
                    add("ERROR", where, "deadline_days must be a positive integer.", rid)
            elif check.kind == "record_assertion":
                errors, warnings = validate_check(check.view, check.where, check.assertion)
                for message in errors:
                    add("ERROR", f"{where}.assert", message, rid)
                for message in warnings:
                    add("WARNING", f"{where}.assert", message, rid)
    for index, item in enumerate(extraction.unexecutable):
        where = f"unexecutable[{index}]"
        if document is not None and not _verbatim(item.citation, source):
            add("ERROR", f"{where}.citation", "Unexecutable obligation citation is not found verbatim in the source document.")
        if DRAFT_PATTERN.search(item.citation):
            add("ERROR", f"{where}.citation", DRAFT_MESSAGE)
        if not item.reason.strip():
            add("ERROR", f"{where}.reason", "Say why the obligation cannot be checked.")
    return issues


def _verbatim(snippet: str, normalised_source: str) -> bool:
    """Same rule for rule and unexecutable citations: the first 60 characters, whitespace-normalised, appear in the source."""
    text = " ".join((snippet or "").lower().split())
    return bool(text) and text[:60] in normalised_source


def has_errors(issues: list[ValidationIssue]) -> bool:
    return any(issue.severity == "ERROR" for issue in issues)
