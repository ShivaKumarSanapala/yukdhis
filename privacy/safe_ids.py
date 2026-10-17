"""Business identifiers that are safe to show the model and must never be mistaken for PII.

Run, report, action and worklist ids, the opaque ``loan_ref`` / ``customer_ref`` keys and graph node ids (``rule:...``,
``product:...``) carry no personal data. Digit runs inside them can look like an Aadhaar, phone or card number to a
regex or to Cloud DLP, so the sanitizer swaps them for letter-only placeholders before detection and restores them after.
``customer_id`` (CIF-...) and ``loan_id`` are deliberately not listed: they are internal ids, not opaque refs.
"""

from __future__ import annotations

import re

SAFE_ID = re.compile(
    r"(?<![\w-])(?:(?:run|rpt|act|wl|conv|msg)-[0-9a-f]{12}|LR-[0-9A-F]{4,10}|CUST-[0-9A-F]{4,10})(?![\w-])"
    r"|(?<![\w:])(?:rule|product|process|control|team|branch|segment|policy|document|doc|workflow):[A-Za-z0-9_./:-]*[A-Za-z0-9_/]"
)
_PLACEHOLDER = re.compile(r"YKSAFE([a-j]+)X")


def _encode(index: int) -> str:
    return "".join("abcdefghij"[int(d)] for d in str(index))


def protect(text: str) -> tuple[str, list[str]]:
    """Replace safe ids with digit-free placeholders; returns the new text and the originals in order."""
    found: list[str] = []

    def swap(match: re.Match) -> str:
        found.append(match.group(0))
        return f"YKSAFE{_encode(len(found) - 1)}X"

    return SAFE_ID.sub(swap, text), found


def restore(text: str, originals: list[str]) -> str:
    def back(match: re.Match) -> str:
        index = int("".join(str("abcdefghij".index(c)) for c in match.group(1)))
        return originals[index] if index < len(originals) else match.group(0)

    return _PLACEHOLDER.sub(back, text)
