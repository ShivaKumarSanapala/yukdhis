"""Reader: Gemini 2.5 Pro structured rule extraction with a deterministic checker.

Maker:   Gemini on Vertex AI with ``response_schema=ExtractedPolicy`` (JSON mode,
         temperature 0) over Document AI chunks.
Checker: ``validate_extraction``.  Errors are sent back for one corrective
         retry; if they persist the extraction fails closed.
Cross-check: when the circular matches an approved template, the template is
         the source of truth and Gemini's output is scored against it.
"""

from __future__ import annotations

from collections.abc import Callable

import json
import logging
import time

from core.config import get_settings
from core.models import ExtractedPolicy, ParsedDocument, ValidationIssue
from ingestion.evaluation import score_extraction
from ingestion.templates import match_template
from ingestion.validator import DRAFT_MESSAGE, has_errors, non_binding_message, validate_extraction
from knowledge_graph.ontology import get_ontology

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
from the text. Use ISO dates. Express each obligation as checks when the check kinds and data below can test it:
- control_required: capability one of {capabilities}; use a new snake_case capability only for a product or process
  capability the bank provides that none of these covers. Always fill capability_description with one sentence on
  what the capability does, in the circular's terms. Leave control_id empty.
- record_flag: record_field one of {flags}; expected = compliant value
- event_deadline: trigger_event/required_event from {events}, deadline_days, penalty_per_day_inr if stated
Do NOT invent a check for an obligation these cannot test (for example a reserve or capital amount to compute or
hold, a board-approved policy, a treasury or derivatives position, or data not listed above). Put each such
obligation in `unexecutable` instead, with citation copied VERBATIM, the reason it cannot be checked, and
missing_data naming the data that would be needed. A rule needs at least one real check; an obligation with none
goes only in `unexecutable`.
Use scope.product_terms for product wording exactly as written and the attribute filters only when the text
states them. Prefix rule ids with "rule:<reference>:" and the policy id with "policy:<reference>".
Set confidence below 0.8 for anything ambiguous.
Also fill `analytical_objectives` (at most 6) with data analyses a compliance analyst should run on the bank's loan
portfolio because of this circular, one sentence each, naming the metric, product and threshold (for example "Analyse
the gold loan portfolio for loan-to-value above 90%" or "Compare outstanding balance with security value for gold
loans"). Include limits, ratios and thresholds that are not obligations the checks can test. A circular with no
rules can still have objectives. Use only metrics the portfolio can plausibly hold (balances, security value, days past
due, asset classification, provisions, KYC status, product, branch).

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
                   announced: dict | None = None, report: Report | None = None) -> tuple[ExtractedPolicy, list[ValidationIssue], dict]:
    """Return (extraction, issues, provenance). ``classification`` (a DocumentClassification dict) gates non-binding documents.

    ``report(kind, payload)`` receives live progress: ``thinking`` (Gemini thought summaries) and ``extraction_retry``."""
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
            extraction = _gemini_extract(document, feedback, _attempt_reporter(report, attempt))
        except Exception as error:
            log.warning("Gemini extraction failed (attempt %s): %s", attempt, error)
            issues = [ValidationIssue(severity="ERROR", field="model", message=f"Gemini extraction failed: {error}")]
            _report(report, "extraction_retry", {"attempt": attempt, "of": MAX_ATTEMPTS, "error": str(error)[:500]})
            continue
        issues = validate_extraction(extraction, document)
        if not has_errors(issues):
            break
        feedback = "Fix these validation errors:\n" + "\n".join(f"- {i.rule_id or ''} {i.field}: {i.message}" for i in issues if i.severity == "ERROR")
    provenance["model"] = settings.reasoning_model

    if reference is not None:
        scores = score_extraction(extraction, reference) if extraction else None
        agreement = scores["checks"]["recall"] if scores else 0.0
        provenance.update(method="template-of-record+gemini-crosscheck", gemini_agreement=agreement,
                          extraction_scores=scores)
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


Report = Callable[[str, dict], None]
# Thinking tokens count against max_output_tokens on Gemini 2.5: 8192 truncated the JSON of a 35-page circular.
MAX_OUTPUT_TOKENS, THINKING_BUDGET = 32768, 4096
PROGRESS_EVERY_S = 1.5


def _report(report: Report | None, kind: str, payload: dict) -> None:
    if report is None:
        return
    try:
        report(kind, payload)
    except Exception:  # progress is observability only
        log.warning("Could not report %s", kind, exc_info=True)


def _attempt_reporter(report: Report | None, attempt: int) -> Callable[[str, int, bool], None] | None:
    if report is None:
        return None
    return lambda thoughts, chars, done: _report(report, "thinking", {
        "agent": "Rule extractor (Gemini)", "stream": f"extract-{attempt}", "text": thoughts[-8000:],
        "answer_chars": chars, "done": done})


def _gemini_extract(document: ParsedDocument, feedback: str,
                    progress: Callable[[str, int, bool], None] | None = None) -> ExtractedPolicy:
    """Streams the structured answer so thought summaries show up live; fails clearly when the answer is cut off."""
    from google.genai import types

    from core.llm import genai_client, retry_stream

    settings = get_settings()
    chunks = "\n\n".join(f"<chunk id={c.chunk_id} page={c.page_start}>\n{c.content}\n</chunk>" for c in document.chunks)
    vocabulary = get_ontology(tenant_ids=(settings.tenant_id,))  # enabled ontology packs, read at call time
    prompt = EXTRACTION_PROMPT.format(capabilities=sorted(vocabulary.capabilities), flags=sorted(vocabulary.record_flags),
                                      events=sorted(vocabulary.event_types),
                                      document_id=document.document_id, parser=document.parser, chunks=chunks)
    if feedback:
        prompt += "\n\n" + feedback
    config = types.GenerateContentConfig(
        temperature=0.0, seed=0, response_mime_type="application/json", response_schema=ExtractedPolicy,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        thinking_config=types.ThinkingConfig(include_thoughts=True, thinking_budget=THINKING_BUDGET))
    stream = retry_stream(lambda: genai_client().models.generate_content_stream(
        model=settings.reasoning_model, contents=prompt, config=config), label="extraction")
    answer, thoughts, finish, last = [], [], "", 0.0
    for chunk in stream:
        for candidate in chunk.candidates or []:
            finish = str(candidate.finish_reason or finish)
            for part in (candidate.content.parts if candidate.content else None) or []:
                if part.text:
                    (thoughts if part.thought else answer).append(part.text)
        if progress and time.monotonic() - last >= PROGRESS_EVERY_S:
            last = time.monotonic()
            progress("".join(thoughts), sum(map(len, answer)), False)
    if progress:
        progress("".join(thoughts), sum(map(len, answer)), True)
    if "MAX_TOKENS" in finish:
        raise ValueError(f"the model's answer was cut off at the output limit ({MAX_OUTPUT_TOKENS} tokens)")
    text = "".join(answer).strip()
    if not text:
        raise ValueError(f"the model returned no answer (finish reason {finish or 'unknown'})")
    return ExtractedPolicy.model_validate(json.loads(text))


def _agreement(candidate: ExtractedPolicy, reference: ExtractedPolicy) -> float:
    """Share of the approved template's checks that Gemini reproduced (check recall in ``score_extraction``)."""
    return score_extraction(candidate, reference)["checks"]["recall"]
