"""Find the circulars, master directions and RBI links a document cites.

Nothing here fetches anything: references are surfaced in the extract output
so a user can analyse a linked document with ``ingestion.fetch.fetch_circular``.
PDF link annotations (``/URI``) are read from the raw bytes, including
Flate-compressed object streams, because Document AI drops hyperlinks.
"""

from __future__ import annotations

import re
import zlib
from urllib.parse import urlsplit

from core.models import DocumentReference
from ingestion.fetch import allowed_hosts

MAX_REFERENCES = 50
MAX_STREAMS = 400
MAX_INFLATED_BYTES = 8 * 1024 * 1024
CONTEXT_CHARS = 160

URL_RX = re.compile(r"https?://[^\s<>\"'()\[\]{}|\\^`]+", re.IGNORECASE)
REF_PATTERNS = (
    re.compile(r"\bRBI/\d{4}-\d{2,4}/\d+\b"),
    re.compile(r"\bA\.\s?P\.\s?\(DIR\s+Series\)\s+Circular\s+No\.?\s?\d+\b", re.IGNORECASE),
    re.compile(r"\b[A-Z][A-Za-z]{1,7}(?:\.[A-Za-z]{2,10}){0,4}\.(?:No\.\s?)?[A-Z]?-?\d+/\d{2}\.\d{2}\.\d{3}/\d{4}-\d{2}\b"),
    re.compile(r"\bPress\s+Release\s*:?\s*(?:No\.?\s*)?\d{4}-\d{2,4}/\d+\b", re.IGNORECASE),
    re.compile(r"\bMaster\s+Directions?\s*(?:[-–—:]|on)\s*[A-Z][^.;:\n()]{4,110}?(?=[.;:\n(]|,\s|\s+dated|\s+as\s+amended|$)"),
    re.compile(r"\b(?:Reserve\s+Bank\s+of\s+India|RBI)\s*\([^()\n]{4,140}\)\s*Directions?,?\s*\d{4}\b"),
)
SUPERSEDES_RX = re.compile(r"supersed|repeal|withdrawn|in\s+lieu\s+of|replac(?:e|es|ed|ing)\b|stands?\s+(?:withdrawn|rescinded)",
                           re.IGNORECASE)
AMENDS_RX = re.compile(r"\bamend(?:s|ed|ing|ment|ments)?\b|\bmodif(?:y|ies|ied|ication)|\brevis(?:e|ed|ion)\b"
                       r"|\bsubstitut(?:e|ed|ion)\b|\binserted\b", re.IGNORECASE)
SELF_URL_RX = re.compile(r"\bsource\s*:\s*$", re.IGNORECASE)
PDF_URI_RX = re.compile(rb"/URI\s*\(((?:\\.|[^\\)]){1,2000})\)")
STREAM_RX = re.compile(rb"stream\r?\n")


def pdf_link_uris(content: bytes) -> list[str]:
    """Return http(s) link-annotation targets from a PDF, raw or inside Flate streams (bounded work)."""
    if not content.lstrip()[:5].startswith(b"%PDF"):
        return []
    found: list[bytes] = PDF_URI_RX.findall(content)
    inflated_total = 0
    for count, match in enumerate(STREAM_RX.finditer(content)):
        if count >= MAX_STREAMS or inflated_total >= MAX_INFLATED_BYTES:
            break
        start = match.end()
        end = content.find(b"endstream", start)
        if end < 0:
            break
        try:
            data = zlib.decompressobj().decompress(content[start:end], MAX_INFLATED_BYTES - inflated_total)
        except zlib.error:
            continue
        inflated_total += len(data)
        if b"/URI" in data:
            found += PDF_URI_RX.findall(data)
    uris = []
    for raw in found:
        uri = re.sub(rb"\\(.)", rb"\1", raw).decode("latin-1", errors="ignore").strip()
        if uri.lower().startswith(("http://", "https://")) and uri not in uris:
            uris.append(uri)
    return uris


def is_rbi_url(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return host in allowed_hosts()


def _relation(context: str) -> str:
    if SUPERSEDES_RX.search(context):
        return "supersedes"
    if AMENDS_RX.search(context):
        return "amends"
    return "refers_to"


def _clean_url(url: str) -> str:
    return url.rstrip(".,;:!?")


def extract_references(text: str, links: list[str] | None = None, self_refs: set[str] | None = None) -> list[DocumentReference]:
    """References in ``text`` (plus PDF link targets), excluding the document's own identifiers.

    Each item is ``{ref, url, relation}``; relation is inferred from wording near the mention.
    """
    self_refs = {" ".join(s.split()).lower() for s in (self_refs or set()) if s}
    refs: dict[str, DocumentReference] = {}

    def add(ref: str, url: str | None, relation: str) -> None:
        key = (url or " ".join(ref.split())).lower()
        if key in self_refs or " ".join(ref.split()).lower() in self_refs or len(refs) >= MAX_REFERENCES:
            return
        existing = refs.get(key)
        if existing is None:
            refs[key] = DocumentReference(ref=" ".join(ref.split()), url=url, relation=relation)
        elif existing.relation == "refers_to" and relation != "refers_to":
            existing.relation = relation

    mentions: list[tuple[int, int, str]] = []
    for pattern in REF_PATTERNS:
        for match in pattern.finditer(text):
            mentions.append((match.start(), match.end(), match.group(0).strip()))
    url_spans: list[tuple[int, int, str]] = []
    for match in URL_RX.finditer(text):
        url = _clean_url(match.group(0))
        if not is_rbi_url(url):
            continue
        before = text[max(0, match.start() - 12): match.start()]
        if SELF_URL_RX.search(before):  # "Source: <url>" is the document itself
            continue
        url_spans.append((match.start(), match.start() + len(url), url))

    for start, end, ref in sorted(mentions):
        context = _sentence(text, start, end)
        nearby = next((u for s, _, u in url_spans if 0 <= s - end <= 200), None)
        add(ref, nearby, _relation(context))
    for start, end, url in url_spans:
        if any(r.url == url for r in refs.values()):
            continue
        add(_label_for_url(text, start, url), url, _relation(_sentence(text, start, end)))
    for url in links or []:
        url = _clean_url(url)
        if is_rbi_url(url) and not any(r.url == url for r in refs.values()):
            add(url, url, "refers_to")
    return list(refs.values())


SELF_LINE_RX = re.compile(r"^\W*(?:ref(?:erence)?\.?\s*(?:no\.?)?|our\s+ref\.?)\s*[:.-]\s*(.+)$", re.IGNORECASE | re.MULTILINE)
NUMBERED = REF_PATTERNS[:4]
NAMED = REF_PATTERNS[4:]


def self_identifiers(title: str, text: str, extra: tuple[str, ...] = ()) -> set[str]:
    """The document's own reference numbers (title, first lines, "Reference:" lines), so they are not listed as references."""
    first_lines = [line for line in text[:1500].splitlines() if line.strip()][:5]
    zone = "\n".join([title, *first_lines])
    ids = {m.group(0).strip() for p in NUMBERED for m in p.finditer(zone)}
    if not (AMENDS_RX.search(zone) or SUPERSEDES_RX.search(zone)):  # "Amendment to Master Direction X" cites X
        ids |= {m.group(0).strip() for p in NAMED for m in p.finditer(zone)}
    for line in SELF_LINE_RX.finditer(text[:3000]):
        ids |= {m.group(0).strip() for p in NUMBERED for m in p.finditer(line.group(1))}
    return ids | {e for e in extra if e}


SENTENCE_END = re.compile(r"[.;]\s+(?=[A-Z(\[])|\n")  # "2.3(iii)" and "No. 27" are not sentence ends


def _sentence(text: str, start: int, end: int) -> str:
    """The sentence around a mention (bounded to CONTEXT_CHARS each side), used to infer the relation."""
    lo = max(0, start - CONTEXT_CHARS)
    lefts = [m.end() for m in SENTENCE_END.finditer(text, lo, start)]
    right = SENTENCE_END.search(text, end, min(len(text), end + CONTEXT_CHARS))
    return text[(lefts[-1] if lefts else lo): (right.start() if right else min(len(text), end + CONTEXT_CHARS))]


def _label_for_url(text: str, start: int, url: str) -> str:
    """Markdown ``[label](url)`` keeps its label; otherwise the URL is its own label."""
    if start >= 2 and text[start - 2: start] == "](":
        open_ = text.rfind("[", max(0, start - 200), start)
        if open_ >= 0:
            label = text[open_ + 1: start - 2].strip()
            if label:
                return label
    return url


def announced_instrument(references: list[DocumentReference]) -> DocumentReference | None:
    """The binding instrument a press release most likely announces (an RBI notification or PDF, not another release)."""
    candidates = [r for r in references if r.url and "pressrelease" not in r.url.lower().replace("_", "")]
    preferred = [r for r in candidates if re.search(r"notification|circular|direction|rdocs/.*\.pdf", r.url or "", re.IGNORECASE)]
    return (preferred or candidates or [None])[0]
