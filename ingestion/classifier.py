"""Document-type classifier: is this a binding instrument, a draft, or an announcement?

Cheap deterministic signals first (title and heading patterns, RBI reference
formats such as ``RBI/2026-27/293`` and ``A.P. (DIR Series) Circular No.``,
"Draft", "for public comments", "Press Release"). Gemini is asked only when
the signals are ambiguous and an LLM is configured; any LLM failure falls back
to the deterministic answer.

Only binding instruments produce obligations: drafts, consultations and press
releases are stopped before extraction (see ``ingestion.extractor``).
"""

from __future__ import annotations

import json
import logging
import re
from typing import get_args

from core.config import get_settings
from core.models import DocumentClassification, DocumentType, ParsedDocument

log = logging.getLogger(__name__)

HEADER_CHARS = 1500
TITLE_LINES = 5
BODY_CHARS = 200_000
AMBIGUOUS_CONFIDENCE = 0.65
MIN_SCORE = 2.0
NON_BINDING_TYPES = {"draft_or_consultation", "press_release"}
DOC_TYPES = set(get_args(DocumentType))


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE | re.MULTILINE)


# (doc_type, zone, weight, pattern, label). Zones: "title" = the title and the first few lines, "header" = title +
# first ~1500 chars, "body" = whole text. Body text of a press release often *describes* a draft, so the strong
# draft signal is the title itself.
SIGNALS: tuple[tuple[str, str, float, re.Pattern, str], ...] = (
    ("press_release", "header", 4.0, _rx(r"\bpress\s+release\b"), "'Press Release' in the header"),
    ("press_release", "body", 3.0, _rx(r"\bpress\s+release\s*:?\s*(?:no\.?\s*)?\d{4}-\d{2,4}/\d+"), "press release number"),
    ("press_release", "header", 1.5, _rx(r"^\W*(?:the\s+)?(?:rbi|reserve bank(?: of india)?)\s+(?:issues|releases|announces|"
                                          r"imposes|cancels|extends|notifies|publishes)\b"), "announcement headline"),
    ("press_release", "body", 1.5, _rx(r"\bdepartment\s+of\s+communication\b|\bchief\s+general\s+manager\b.{0,80}$"),
     "communication department sign-off"),
    ("draft_or_consultation", "title", 4.5, _rx(r"^\W*\(?draft\b"), "title starts with 'Draft'"),
    ("draft_or_consultation", "header", 3.0, _rx(r"\b(?:discussion|consultation|concept)\s+paper\b|\bexposure\s+draft\b"),
     "discussion or consultation paper"),
    ("draft_or_consultation", "header", 2.0, _rx(r"\bfor\s+(?:public\s+)?(?:comments|feedback|consultation)\b"),
     "'for public comments' in the header"),
    ("draft_or_consultation", "body", 1.0, _rx(r"\bfor\s+(?:public\s+)?(?:comments|feedback|consultation)\b"),
     "'for public comments' in the text"),
    ("draft_or_consultation", "body", 1.5, _rx(r"\b(?:comments|feedback|suggestions|views)\b[^.\n]{0,80}\b(?:are\s+invited|"
                                                r"is\s+invited|may\s+be\s+sent|are\s+solicited|can\s+be\s+sent|be\s+submitted|"
                                                r"may\s+be\s+forwarded)"), "comments invited"),
    ("master_direction", "header", 4.0, _rx(r"\bmaster\s+directions?\b"), "'Master Direction' in the title"),
    ("master_direction", "header", 3.0, _rx(r"\b(?:reserve\s+bank\s+of\s+india|rbi)\s*\([^()\n]{3,150}\)\s*directions?,?\s*\d{4}\b"),
     "'Reserve Bank of India (...) Directions, YYYY' title"),
    ("faq", "title", 4.5, _rx(r"\bfrequently\s+asked\s+questions\b|\bfaqs?\b"), "FAQ title"),
    ("faq", "header", 1.0, _rx(r"\bfrequently\s+asked\s+questions\b"), "FAQ wording"),
    ("notification", "header", 3.0, _rx(r"\bnotification\s+no\.?\s*\w"), "'Notification No.' reference"),
    ("notification", "header", 2.0, _rx(r"\bG\.?\s?S\.?\s?R\.?\s*\d+|\bFEMA\s*\d+\s*\(R\)"), "gazette or FEMA notification number"),
    ("circular", "header", 2.5, _rx(r"\bRBI/\d{4}-\d{2,4}/\d+\b"), "RBI/YYYY-YY/NNN reference"),
    ("circular", "header", 3.0, _rx(r"\bA\.\s?P\.\s?\(DIR\s+Series\)\s+Circular\s+No\.?"), "A.P. (DIR Series) Circular No."),
    ("circular", "header", 2.0, _rx(r"\b[A-Z][A-Za-z]{1,7}(?:\.[A-Za-z]{2,10}){0,4}\.(?:No\.\s?)?[A-Z]?-?\d+/\d{2}\.\d{2}\.\d{3}/\d{4}-\d{2}\b"),
     "department reference number"),
    ("circular", "header", 2.0, _rx(r"\bmaster\s+circular\b"), "'Master Circular' in the title"),
    ("circular", "header", 1.0, _rx(r"\bcircular\b"), "'circular' in the header"),
    ("circular", "body", 1.0, _rx(r"\bdear\s+sir\b|\bmadam\b|\byours\s+faithfully\b"), "letter salutation"),
)
# A final instrument that mentions its earlier consultation is not a draft.
FINAL_AFTER_CONSULTATION = _rx(r"\b(?:after|on)\s+(?:considering|examining|taking\s+into\s+account|review\s+of)\s+(?:the\s+)?"
                               r"(?:feedback|comments|responses)\b|\bfinal\s+directions\b")


def classify_document(document: ParsedDocument, use_llm: bool | None = None) -> DocumentClassification:
    result = classify_text(document.title, document.text)
    settings = get_settings()
    if use_llm is None:
        use_llm = settings.agent_mode == "agentic" and settings.is_gcp_llm_ready
    if use_llm and _ambiguous(result):
        try:
            llm = _gemini_classify(document)
            llm.signals = [*result.signals, f"deterministic guess: {result.doc_type} ({result.confidence:.2f})"]
            return llm
        except Exception as error:  # the deterministic answer stands
            log.warning("Gemini document classification failed: %s", type(error).__name__)
    return result


def classify_text(title: str, text: str) -> DocumentClassification:
    first_lines = [line.strip()[:200] for line in text[:HEADER_CHARS].splitlines() if line.strip()][:TITLE_LINES]
    zones = {"title": "\n".join([title, *first_lines]), "header": f"{title}\n{text[:HEADER_CHARS]}", "body": text[:BODY_CHARS]}
    body = zones["body"]
    scores: dict[str, float] = {t: 0.0 for t in DOC_TYPES}
    hits: dict[str, list[str]] = {t: [] for t in DOC_TYPES}
    for doc_type, zone, weight, pattern, label in SIGNALS:
        if pattern.search(zones[zone]):
            scores[doc_type] += weight
            hits[doc_type].append(label)
    if scores["draft_or_consultation"] and FINAL_AFTER_CONSULTATION.search(body):
        scores["draft_or_consultation"] = max(0.0, scores["draft_or_consultation"] - 3.0)
        hits["draft_or_consultation"].append("(discounted: final text after consultation)")
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], _PRIORITY.index(kv[0])))
    (top, top_score), (_, second_score) = ranked[0], ranked[1]
    signals = [f"{t}: {', '.join(hits[t])}" for t, s in ranked if s > 0]
    if top_score < MIN_SCORE:
        return DocumentClassification(doc_type="other", confidence=0.4, signals=signals,
                                      reason="No reliable document-type signal (no RBI reference, title pattern or header).")
    confidence = round(min(0.99, 0.5 + 0.5 * (top_score - second_score) / (top_score + 1.0)), 2)
    return DocumentClassification(doc_type=top, confidence=confidence, signals=signals,
                                  reason=f"{_LABELS[top]}: {'; '.join(hits[top])}.")


def is_binding(classification: DocumentClassification | dict | None) -> bool:
    if classification is None:
        return True
    doc_type = classification.doc_type if isinstance(classification, DocumentClassification) else classification.get("doc_type")
    return doc_type not in NON_BINDING_TYPES


def _ambiguous(result: DocumentClassification) -> bool:
    return result.doc_type == "other" or result.confidence < AMBIGUOUS_CONFIDENCE


# Ties resolve towards the more cautious (non-binding) reading.
_PRIORITY = ["draft_or_consultation", "press_release", "faq", "master_direction", "notification", "circular", "other"]
_LABELS = {"circular": "RBI circular", "master_direction": "Master Direction / Directions",
           "draft_or_consultation": "Draft or consultation", "press_release": "Press release", "faq": "FAQ",
           "notification": "Notification", "other": "Other"}

CLASSIFY_PROMPT = """Classify this regulatory document from the Reserve Bank of India into exactly one doc_type:
circular | master_direction | draft_or_consultation | press_release | faq | notification | other.
A press release announces or summarises; a draft or consultation invites comments; only circulars, master directions
and notifications are binding. Give a confidence between 0 and 1 and a one-sentence reason quoting the decisive words.

TITLE: {title}
FIRST PART OF THE TEXT:
{text}
"""


def _gemini_classify(document: ParsedDocument) -> DocumentClassification:
    from google.genai import types

    from core.llm import genai_client

    settings = get_settings()
    response = genai_client().models.generate_content(
        model=settings.fast_model, contents=CLASSIFY_PROMPT.format(title=document.title[:300], text=document.text[:6000]),
        config=types.GenerateContentConfig(temperature=0.0, seed=0, response_mime_type="application/json",
                                           response_schema=DocumentClassification, max_output_tokens=1024,
                                           # thinking tokens count against the output limit (512 truncated the JSON)
                                           thinking_config=types.ThinkingConfig(
                                               thinking_budget=0 if "flash" in settings.fast_model else 128)),
    )
    parsed = getattr(response, "parsed", None)
    result = parsed if isinstance(parsed, DocumentClassification) else DocumentClassification.model_validate(json.loads(response.text))
    result.method = f"gemini:{settings.fast_model}"
    return result
