"""Gold-set evaluation of rule extraction: precision, recall and F1 per field.

The approved template-of-record extractors (``ingestion/templates.py``) are the gold set for the sample circulars.
``score_extraction(candidate, gold)`` reports:

* ``rules``  - P/R/F1 after a one-to-one match of candidate to gold rules by normalised title, citation overlap or
  content (title + description) overlap, whichever is strongest (``RULE_MATCH_THRESHOLD``).
* ``checks`` - P/R/F1 over the policy's checks as a multiset of executable signatures (kind, capability, record
  field and expected value, events and deadline), so wording of the description does not matter.
* ``fields`` - accuracy over matched rule pairs of ``modality``, ``effective_date`` and the set of required
  capabilities (``None`` when no matched pair has the field).
"""

from __future__ import annotations

import re
from collections import Counter

from core.models import ComplianceCheck, ExtractedPolicy, Rule

RULE_MATCH_THRESHOLD = 0.5
_STOP = {"the", "a", "an", "of", "and", "or", "to", "on", "for", "by", "in", "with", "at", "be", "is", "must", "shall"}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _STOP}


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _overlap(a: str, b: str) -> float:
    x, y = _tokens(a), _tokens(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def rule_similarity(candidate: Rule, gold: Rule) -> tuple[float, str]:
    """(score, basis): 1.0 for an equal normalised title; else the best of citation and content overlap."""
    if _norm(candidate.title) and _norm(candidate.title) == _norm(gold.title):
        return 1.0, "title"
    a, b = _norm(candidate.citation.snippet), _norm(gold.citation.snippet)
    citation = 1.0 if a and b and (a in b or b in a) else _overlap(a, b)
    content = _overlap(f"{candidate.title} {candidate.description}", f"{gold.title} {gold.description}")
    return (round(citation, 3), "citation") if citation >= content else (round(content, 3), "content")


def match_rules(candidate: ExtractedPolicy, gold: ExtractedPolicy) -> list[dict]:
    """Greedy one-to-one matching by descending similarity."""
    pairs = []
    for i, c in enumerate(candidate.rules):
        for j, g in enumerate(gold.rules):
            score, basis = rule_similarity(c, g)
            if score >= RULE_MATCH_THRESHOLD:
                pairs.append((score, i, j, basis))
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
    used_c, used_g, matches = set(), set(), []
    for score, i, j, basis in pairs:
        if i in used_c or j in used_g:
            continue
        used_c.add(i)
        used_g.add(j)
        matches.append({"candidate": candidate.rules[i].rule_id, "gold": gold.rules[j].rule_id, "score": score,
                        "basis": basis, "_c": i, "_g": j})
    return sorted(matches, key=lambda m: m["_g"])


def check_signature(check: ComplianceCheck) -> tuple:
    return (check.kind, check.capability, check.record_field, check.expected if check.kind == "record_flag" else None,
            check.trigger_event, check.required_event, check.deadline_days)


def _prf(true_positive: int, predicted: int, actual: int) -> dict:
    precision = true_positive / predicted if predicted else (1.0 if not actual else 0.0)
    recall = true_positive / actual if actual else (1.0 if not predicted else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "true_positive": true_positive, "predicted": predicted, "gold": actual}


def _capabilities(rule: Rule) -> frozenset[str]:
    return frozenset(c.capability for c in rule.checks if c.kind == "control_required" and c.capability)


def _accuracy(values: list[bool]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def score_extraction(candidate: ExtractedPolicy, gold: ExtractedPolicy) -> dict:
    matches = match_rules(candidate, gold)
    candidate_checks = Counter(check_signature(c) for r in candidate.rules for c in r.checks)
    gold_checks = Counter(check_signature(c) for r in gold.rules for c in r.checks)
    pairs = [(candidate.rules[m["_c"]], gold.rules[m["_g"]]) for m in matches]
    capability_pairs = [(c, g) for c, g in pairs if _capabilities(c) or _capabilities(g)]
    return {
        "rules": _prf(len(matches), len(candidate.rules), len(gold.rules)),
        "checks": _prf(sum((candidate_checks & gold_checks).values()), sum(candidate_checks.values()),
                       sum(gold_checks.values())),
        "fields": {
            "modality": _accuracy([c.modality == g.modality for c, g in pairs]),
            "effective_date": _accuracy([c.effective_date == g.effective_date for c, g in pairs]),
            "capability": _accuracy([_capabilities(c) == _capabilities(g) for c, g in capability_pairs]),
        },
        "matched_rules": [{k: v for k, v in m.items() if not k.startswith("_")} for m in matches],
    }
