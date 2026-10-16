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
from ingestion.validator import has_errors, validate_extraction
from knowledge_graph.ontology import CAPABILITIES, EVENT_TYPES, RECORD_FLAGS

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 2

EXTRACTION_PROMPT = """You are the Reader agent of a banking compliance engine. Convert the regulatory circular
below into machine-executable rules. Rules must be grounded: each citation.snippet is copied VERBATIM
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


def extract_policy(document: ParsedDocument, use_llm: bool | None = None) -> tuple[ExtractedPolicy, list[ValidationIssue], dict]:
    """Return (extraction, issues, provenance)."""
    settings = get_settings()
    template = match_template(document)
    reference = template(document) if template else None
    use_llm = settings.agent_mode == "agentic" and settings.is_gcp_llm_ready if use_llm is None else use_llm
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
    if extraction is None or has_errors(issues):
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
        config=types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json",
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
