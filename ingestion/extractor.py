"""Reader: Gemini 2.5 Pro structured rule extraction with a deterministic checker.

Maker:   Gemini on Vertex AI with ``response_schema=ExtractedPolicy`` (JSON mode,
         temperature 0) over Document AI chunks.
Checker: ``validate_extraction``.  Errors are sent back for one corrective
         retry; if they persist the extraction fails closed.
Cross-check: when the circular matches an approved template, the template is
         the source of truth and Gemini's output is scored against it.
"""

from __future__ import annotations

import json
import logging

from core.config import get_settings
from core.models import ExtractedPolicy, ParsedDocument, ValidationIssue
from ingestion.templates import match_template
from ingestion.validator import DRAFT_MESSAGE, has_errors, non_binding_message, validate_extraction
from knowledge_graph.ontology import CAPABILITIES, EVENT_TYPES, RECORD_FLAGS

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 2
NO_BINDING_MESSAGE = non_binding_message()
NON_BINDING_TYPES = {"draft_or_consultation", "press_release"}

EXTRACTION_PROMPT = """You are the Reader agent of a banking compliance engine. Convert the regulatory circular
below into machine-executable rules. Extract ONLY binding obligations from the operative text of a FINAL
instrument. A press release, announcement, FAQ, or any document that only describes a draft or proposal has
NO binding rules: return an empty rules list. Set each rule's modality: MUST for an obligation, MAY for a
permission or option available to the bank (e.g. "may", "allowing banks to choose"), MUST_NOT for a prohibition.
Rules must be grounded: each citation.snippet is copied VERBATIM
from the text. Use ISO dates. Express every obligation as checks:
- control_required: capability one of {capabilities} (or a new snake_case capability if none fits)
- record_flag: record_field one of {flags}; expected = compliant value
- event_deadline: trigger_event/required_event from {events}, deadline_days, penalty_per_day_inr if stated
Use scope.product_terms for product wording exactly as written and the attribute filters only when the text
states them. Prefix rule ids with "rule:<reference>:" and the policy id with "policy:<reference>".
Set confidence below 0.8 for anything ambiguous.

DOCUMENT {document_id} ({parser})
{chunks}
"""


def llm_enabled() -> bool:
    settings = get_settings()
    return settings.agent_mode == "agentic" and settings.is_gcp_llm_ready


def ensure_binding(classification: dict | None, announced: dict | None = None) -> None:
    """Document-type gate: drafts, consultations and press releases never produce obligations."""
    if classification and classification.get("doc_type") in NON_BINDING_TYPES:
        raise ValueError(non_binding_message(classification, announced))


def extract_policy(document: ParsedDocument, use_llm: bool | None = None, classification: dict | None = None,
                   announced: dict | None = None) -> tuple[ExtractedPolicy, list[ValidationIssue], dict]:
    """Return (extraction, issues, provenance). ``classification`` (a DocumentClassification dict) gates non-binding documents."""
    ensure_binding(classification, announced)
    settings = get_settings()
    template = match_template(document)
    reference = template(document) if template else None
    use_llm = llm_enabled() if use_llm is None else use_llm
    provenance: dict = {"template": template.__name__ if template else None, "model": None, "attempts": 0}

    if not use_llm:
        if reference is None:
            raise ValueError("No approved template matches this circular and LLM extraction is disabled.")
        return reference, validate_extraction(reference, document), {**provenance, "method": "deterministic-template"}

    extraction, issues = None, []
    feedback = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        provenance["attempts"] = attempt
        try:
            extraction = _gemini_extract(document, feedback)
        except Exception as error:
            log.warning("Gemini extraction failed (attempt %s): %s", attempt, error)
            issues = [ValidationIssue(severity="ERROR", field="model", message=f"Gemini extraction failed: {error}")]
            continue
        issues = validate_extraction(extraction, document)
        if not has_errors(issues):
            break
        feedback = "Fix these validation errors:\n" + "\n".join(f"- {i.rule_id or ''} {i.field}: {i.message}" for i in issues if i.severity == "ERROR")
    provenance["model"] = settings.reasoning_model

    if reference is not None:
        agreement = _agreement(extraction, reference) if extraction else 0.0
        provenance.update(method="template-of-record+gemini-crosscheck", gemini_agreement=agreement)
        notes = [] if agreement >= 0.99 else [ValidationIssue(
            severity="WARNING", field="cross_check",
            message=f"Gemini matched {agreement:.0%} of the approved template's checks; template used as source of truth.")]
        return reference, validate_extraction(reference, document) + notes, provenance
    errors = [i for i in issues if i.severity == "ERROR"]
    if extraction is not None and errors and all(i.message in (DRAFT_MESSAGE, "No rules extracted.") for i in errors):
        raise ValueError(non_binding_message(classification, announced))
    if extraction is None or errors:
        raise ValueError("Extraction failed validation: " + "; ".join(i.message for i in issues if i.severity == "ERROR"))
    provenance["method"] = "gemini-structured-output"
    return extraction, issues, provenance


def _gemini_extract(document: ParsedDocument, feedback: str) -> ExtractedPolicy:
    from google.genai import types

    from core.llm import genai_client

    settings = get_settings()
    chunks = "\n\n".join(f"<chunk id={c.chunk_id} page={c.page_start}>\n{c.content}\n</chunk>" for c in document.chunks)
    prompt = EXTRACTION_PROMPT.format(capabilities=sorted(CAPABILITIES), flags=sorted(RECORD_FLAGS), events=sorted(EVENT_TYPES),
                                      document_id=document.document_id, parser=document.parser, chunks=chunks)
    if feedback:
        prompt += "\n\n" + feedback
    response = genai_client().models.generate_content(
        model=settings.reasoning_model, contents=prompt,
        config=types.GenerateContentConfig(temperature=0.0, seed=0, response_mime_type="application/json",
                                           response_schema=ExtractedPolicy, max_output_tokens=8192),
    )
    if getattr(response, "parsed", None) is not None:
        return response.parsed
    return ExtractedPolicy.model_validate(json.loads(response.text))


def _agreement(candidate: ExtractedPolicy, reference: ExtractedPolicy) -> float:
    def signature(policy: ExtractedPolicy) -> set[tuple]:
        return {(c.kind, c.capability, c.record_field, c.trigger_event, c.required_event, c.deadline_days)
                for rule in policy.rules for c in rule.checks}

    expected = signature(reference)
    return round(len(signature(candidate) & expected) / len(expected), 3) if expected else 0.0
