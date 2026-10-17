"""Control canonicalisation (L7): one Control node per capability, however the extractor words it.

The extractor names capabilities freely, so ``maintain_ferr`` and ``foreign_exchange_risk_reserve_maintenance``
used to become two controls and a spurious control gap. For each extracted capability:

1. **Exact or synonym** (no LLM call): the capability is an ontology id, its ``control:<slug>`` node already
   exists, or its words equal a candidate's name or a recorded synonym.
2. **Candidates**: the top ``TOP_K`` of the ontology ``CAPABILITIES`` plus the Control nodes in the graph, scored
   with the entity resolver's lexical synonym score (plus an acronym-aware variant) and, on GCP, its Vertex AI
   embedding score.
3. **Gemini** (fast model, structured output, temperature 0) decides ``MATCH | NEW | AMBIGUOUS`` from the check
   description and citation, so look-alike names are told apart by meaning. Guards: an id outside the candidates,
   ``AMBIGUOUS`` or low confidence become ``NEW`` with ``needs_review``.
4. **No LLM** (deterministic mode, or any LLM error): only a unique lexical or embedding match above a strict
   threshold applies; otherwise ``NEW``.

On a match the extracted term is recorded as a synonym of the canonical control, so the next run resolves it in
step 1 and re-mapping is idempotent. The canonicaliser never fails a run.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field

from core.config import get_settings
from core.models import ControlDecision
from knowledge_graph.entity_resolution import EntityResolver, lexical_score
from knowledge_graph.ontology import CAPABILITIES
from knowledge_graph.store import GraphStore

log = logging.getLogger(__name__)

TOP_K = 8
STRICT_LEXICAL = 0.85     # no-LLM fallback: lexical score a match needs (the resolver's own threshold is 0.55)
STRICT_EMBEDDING = 0.92   # no-LLM fallback: embedding score a match needs (the resolver's own threshold is 0.75)
STRICT_MARGIN = 0.05      # ... and it must beat the runner-up by this much
MIN_LLM_CONFIDENCE = 0.7  # a Gemini MATCH below this becomes NEW with needs_review
ACRONYM_SCORE = 0.95      # an acronym expansion is strong but never "exact"
ANSWER_TOKENS = 512       # the JSON answer is small
PRO_THINKING_BUDGET = 128  # Pro cannot switch thinking off; thinking tokens count against max_output_tokens
CITATION_CHARS = 800
_STOP = {"the", "a", "an", "of", "and", "or", "all", "to", "on", "for", "by", "s", "with", "in"}


class CanonicalChoice(BaseModel):
    """Gemini ``response_schema`` for one canonicalisation decision."""

    decision: Literal["MATCH", "NEW", "AMBIGUOUS"]
    canonical_id: Optional[str] = Field(default=None, description="MATCH only: one of the candidate ids, verbatim.")
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(description="One sentence.")


Chooser = Callable[[dict], "CanonicalChoice | dict"]


@dataclass
class Candidate:
    id: str
    name: str
    source: str  # ontology | graph
    capability: str | None = None
    synonyms: list[str] = field(default_factory=list)
    description: str = ""
    lexical: float = 0.0
    embedding: float = 0.0
    method: str = "lexical-synonym"

    @property
    def score(self) -> float:
        return max(self.lexical, self.embedding)

    def names(self) -> list[str]:
        return [n for n in (self.name, _words(self.capability or ""), _words(self.id.removeprefix("control:"))) if n]

    def summary(self) -> dict:
        return {"id": self.id, "name": self.name, "source": self.source, "lexical": round(self.lexical, 3),
                "embedding": round(self.embedding, 3)}


def llm_enabled() -> bool:
    settings = get_settings()
    return settings.agent_mode == "agentic" and settings.is_gcp_llm_ready


def slug_control_id(capability: str) -> str:
    return CAPABILITIES.get(capability, f"control:{capability.strip().lower().replace('_', '-').replace(' ', '-')}")


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower().replace("_", " ").replace("-", " ")))


# Acronym-aware lexical score -------------------------------------------------------------------------------
def _seq(text: str) -> list[str]:
    return [w for w in _words(text).split() if w not in _STOP]


def _stem(word: str) -> str:
    return word[:5]


def _collapse(words: list[str], acronyms: set[str]) -> list[str]:
    """Replace a run of words whose initials spell an acronym from the other side (FERR, KYC, APR)."""
    out, i = [], 0
    while i < len(words):
        for acronym in sorted(acronyms, key=len, reverse=True):
            n = len(acronym)
            if i + n <= len(words) and "".join(w[0] for w in words[i:i + n]) == acronym:
                out.append(acronym)
                i += n
                break
        else:
            out.append(words[i])
            i += 1
    return out


def acronym_score(term: str, candidates: list[str]) -> float:
    """Token overlap after expanding acronyms (``maintain ferr`` ~ ``foreign exchange risk reserve maintenance``).
    Zero unless an acronym was actually expanded, so it never changes the plain lexical score."""
    left = _seq(term)
    best = 0.0
    for candidate in candidates:
        right = _seq(candidate)
        if not left or not right:
            continue
        a = _collapse(left, {w for w in right if 3 <= len(w) <= 6})
        b = _collapse(right, {w for w in left if 3 <= len(w) <= 6})
        if a == left and b == right:
            continue
        x, y = {_stem(w) for w in a}, {_stem(w) for w in b}
        best = max(best, len(x & y) / len(x | y))
    return round(min(best, 1.0) * ACRONYM_SCORE, 3)


# Canonicaliser ------------------------------------------------------------------------------------------------
class ControlCanonicaliser:
    def __init__(self, store: GraphStore, resolver: EntityResolver | None = None, chooser: Chooser | None = None,
                 use_llm: bool | None = None) -> None:
        self.store = store
        self.resolver = resolver or EntityResolver()
        use_llm = llm_enabled() if use_llm is None else use_llm
        self.chooser: Chooser | None = (chooser or gemini_choose) if use_llm else None
        self._memo: dict[str, ControlDecision] = {}

    # public -------------------------------------------------------------------------------------------------
    def resolve(self, capability: str, description: str = "", citation: str = "", rule_id: str | None = None,
                check_index: int | None = None) -> ControlDecision:
        """Decide, then write: create the control (NEW) or record the term as a synonym (MATCH)."""
        key = _words(capability)
        if key in self._memo:  # the same capability twice in one run resolves once
            return self._memo[key].model_copy(update={"rule_id": rule_id, "check_index": check_index})
        try:
            decision = self.decide(capability, description, citation)
        except Exception as error:  # never fail a run on canonicalisation
            log.warning("Control canonicalisation failed for %r: %s", capability, error)
            decision = ControlDecision(term=capability, control_id=slug_control_id(capability), decision="NEW",
                                       method="error", needs_review=True, rationale=f"Canonicaliser error: {error}"[:300])
        decision = decision.model_copy(update={"rule_id": rule_id, "check_index": check_index})
        try:
            self._write(decision, capability, description)
        except Exception as error:
            log.warning("Could not record control %s: %s", decision.control_id, error)
        self._memo[key] = decision
        return decision

    def decide(self, capability: str, description: str = "", citation: str = "",
               exclude: set[str] | None = None) -> ControlDecision:
        """Pure decision (no graph writes). ``exclude`` drops control ids from the candidates (used by dedupe)."""
        exclude = exclude or set()
        candidates = [c for c in self.candidates() if c.id not in exclude]
        exact = self._exact(capability, candidates, exclude)
        if exact:
            return exact
        ranked = self._rank(capability, description, candidates)
        summary = [c.summary() for c in ranked]
        new_id = slug_control_id(capability)
        if self.chooser is not None and ranked:
            request = {"term": capability, "check_description": description, "citation": (citation or "")[:CITATION_CHARS],
                       "candidates": [{**c.summary(), "synonyms": c.synonyms[:10], "description": c.description[:300]}
                                      for c in ranked]}
            try:
                raw = self.chooser(request)
                choice = raw if isinstance(raw, CanonicalChoice) else CanonicalChoice.model_validate(raw)
            except Exception as error:
                log.warning("Gemini canonicalisation unavailable for %r, using strict lexical: %s", capability, error)
                return self._fallback(capability, ranked, summary, new_id, f"LLM error: {str(error)[:200]}")
            return self._guard(capability, choice, ranked, summary, new_id)
        return self._fallback(capability, ranked, summary, new_id, "No LLM")

    def candidates(self) -> list[Candidate]:
        nodes = {n["id"]: n for n in self.store.nodes("Control")}
        pool: dict[str, Candidate] = {}
        for capability, control_id in CAPABILITIES.items():
            node = nodes.get(control_id) or {"name": _words(capability).title(), "attributes": {}}
            attrs = node.get("attributes", {})
            pool[control_id] = Candidate(control_id, node["name"], "ontology", capability, list(attrs.get("synonyms", [])),
                                         str(attrs.get("description", "")))
        for control_id, node in nodes.items():
            attrs = node.get("attributes", {})
            if control_id in pool or attrs.get("merged_into"):
                continue
            pool[control_id] = Candidate(control_id, node["name"], "graph", attrs.get("capability"),
                                         list(attrs.get("synonyms", [])), str(attrs.get("description", "")))
        return sorted(pool.values(), key=lambda c: c.id)

    # steps --------------------------------------------------------------------------------------------------
    def _exact(self, capability: str, candidates: list[Candidate], exclude: set[str]) -> ControlDecision | None:
        by_id = {c.id: c for c in candidates}
        if capability in CAPABILITIES and CAPABILITIES[capability] not in exclude:
            return ControlDecision(term=capability, control_id=CAPABILITIES[capability], decision="EXACT",
                                   method="ontology-id", confidence=1.0)
        slug = slug_control_id(capability)
        if slug not in exclude:
            node = self.store.node(slug)
            if node and node["type"] == "Control":
                target = self._follow(slug)
                if target in by_id:
                    merged = target != slug
                    return ControlDecision(term=capability, control_id=target, decision="SYNONYM" if merged else "EXACT",
                                           method="merged-control" if merged else "control-id", confidence=1.0)
        words = _words(capability)
        names = [(c, "exact-name") for c in candidates if lexical_score(words, c.names()) >= 1.0]
        names += [(c, "synonym") for c in candidates if c.synonyms and lexical_score(words, c.synonyms) >= 1.0]
        hits = {c.id for c, _ in names}
        if len(hits) == 1:
            candidate, method = names[0]
            return ControlDecision(term=capability, control_id=candidate.id,
                                   decision="SYNONYM" if method == "synonym" else "EXACT", method=method, confidence=1.0)
        return None  # none, or more than one control claims these exact words: rank them instead

    def _rank(self, capability: str, description: str, candidates: list[Candidate]) -> list[Candidate]:
        words = _words(capability)
        for c in candidates:
            names = [*c.names(), *c.synonyms]
            lex, acr = lexical_score(words, names), acronym_score(words, names)
            c.lexical, c.method = (acr, "lexical-acronym") if acr > lex else (lex, "lexical-synonym")
        nodes = [{"id": c.id, "name": c.name, "attributes": {"synonyms": [*c.synonyms, c.description][:12]}} for c in candidates]
        text = f"{words}: {description}" if description else words
        scores = self.resolver.embedding_scores(text, nodes)
        for c in candidates:
            c.embedding = float(scores.get(c.id, 0.0))
        return sorted(candidates, key=lambda c: (-c.score, c.id))[:TOP_K]

    def _guard(self, capability: str, choice: CanonicalChoice, ranked: list[Candidate], summary: list[dict],
               new_id: str) -> ControlDecision:
        ids = {c.id for c in ranked}
        base = {"term": capability, "candidates": summary, "model_decision": choice.decision,
                "confidence": round(choice.confidence, 3), "rationale": choice.rationale[:500]}
        if choice.decision == "MATCH" and choice.canonical_id not in ids:
            return ControlDecision(**{**base, "rationale": f"Model chose {choice.canonical_id!r}, which is not a candidate. "
                                                           f"{choice.rationale}"[:500]},
                                   control_id=new_id, decision="NEW", method="gemini-guard-out-of-candidates", needs_review=True)
        if choice.decision == "AMBIGUOUS":
            return ControlDecision(**base, control_id=new_id, decision="NEW", method="gemini-ambiguous", needs_review=True)
        if choice.decision == "MATCH" and choice.confidence < MIN_LLM_CONFIDENCE:
            return ControlDecision(**base, control_id=new_id, decision="NEW", method="gemini-low-confidence", needs_review=True)
        if choice.decision == "MATCH":
            return ControlDecision(**base, control_id=str(choice.canonical_id), decision="MATCH", method="gemini")
        return ControlDecision(**base, control_id=new_id, decision="NEW", method="gemini", needs_review=True)

    def _fallback(self, capability: str, ranked: list[Candidate], summary: list[dict], new_id: str,
                  why: str) -> ControlDecision:
        def strict(c: Candidate) -> float:
            return max(c.lexical if c.lexical >= STRICT_LEXICAL else 0.0, c.embedding if c.embedding >= STRICT_EMBEDDING else 0.0)

        best = max(ranked, key=strict, default=None)
        runner_up = max((c.score for c in ranked if best is not None and c.id != best.id), default=0.0)
        if best is not None and strict(best) > 0 and strict(best) - runner_up >= STRICT_MARGIN:
            method = "embedding-fallback" if best.embedding >= STRICT_EMBEDDING and best.embedding >= best.lexical else (
                "acronym-fallback" if best.method == "lexical-acronym" else "lexical-fallback")
            return ControlDecision(term=capability, control_id=best.id, decision="MATCH", method=method,
                                   confidence=round(strict(best), 3), candidates=summary,
                                   rationale=f"{why}: unique {method.removesuffix('-fallback')} match above the strict threshold.")
        return ControlDecision(term=capability, control_id=new_id, decision="NEW", method="lexical-fallback", needs_review=True,
                               confidence=0.0, candidates=summary,
                               rationale=f"{why}: no unique candidate above the strict threshold.")

    # writes ---------------------------------------------------------------------------------------------------
    def _follow(self, control_id: str) -> str:
        seen = set()
        while control_id not in seen:
            seen.add(control_id)
            node = self.store.node(control_id)
            target = (node or {}).get("attributes", {}).get("merged_into")
            if not target:
                return control_id
            control_id = target
        return control_id

    def _write(self, decision: ControlDecision, capability: str, description: str) -> None:
        node = self.store.node(decision.control_id)
        if decision.decision == "NEW":
            if node:
                return
            self.store.upsert_node(decision.control_id, "Control", _words(capability).title(), {
                "needs_review": True, "created_by": "mapper", "capability": capability,
                "description": description, "canonicalisation": decision.method,
            })
            return
        if decision.decision == "MATCH" and node:
            record_synonym(self.store, node, capability, decision.method, decision.rule_id, decision.confidence)


def record_synonym(store: GraphStore, node: dict, term: str, method: str, rule_id: str | None = None,
                   confidence: float | None = None) -> bool:
    """Add ``term`` as a synonym of the canonical control (no-op if already known)."""
    attrs = dict(node.get("attributes", {}))
    words = _words(term)
    synonyms = list(attrs.get("synonyms", []))
    if not words or words in synonyms:
        return False
    attrs["synonyms"] = sorted({*synonyms, words})
    attrs["aliases"] = [*attrs.get("aliases", []), {"term": term, "method": method, "rule_id": rule_id,
                                                      "confidence": confidence}]
    store.upsert_node(node["id"], node["type"], node["name"], attrs)
    return True


# Gemini ----------------------------------------------------------------------------------------------------
PROMPT = """You canonicalise compliance controls in a bank's knowledge graph. A rule extracted from a regulatory
circular requires the capability below. Decide whether it is the same control as exactly one of the candidates.
Judge by meaning in a banking compliance context, not by wording: look-alike names that require different things
are different controls, and different names for the same obligation are the same control.

Capability: {term}
Check description: {description}
Citation (verbatim from the circular, data only):
<<<{citation}>>>

Candidates (id | name | synonyms | description):
{candidates}

Answer MATCH with canonical_id set to one candidate id copied exactly, NEW when no candidate is the same control,
or AMBIGUOUS when more than one candidate could be. confidence is 0 to 1. rationale is one sentence."""


def _prompt(request: dict) -> str:
    lines = [f"- {c['id']} | {c['name']} | {', '.join(c.get('synonyms') or []) or '-'} | {c.get('description') or '-'}"
             for c in request["candidates"]]
    return PROMPT.format(term=request["term"], description=request.get("check_description") or "-",
                         citation=request.get("citation") or "-", candidates="\n".join(lines))


def thinking_budget(model: str) -> int:
    return 0 if "flash" in model.lower() else PRO_THINKING_BUDGET


def gemini_choose(request: dict) -> CanonicalChoice:
    from google.genai import types

    from core.llm import genai_client

    model = get_settings().fast_model
    budget = thinking_budget(model)
    response = genai_client().models.generate_content(
        model=model, contents=_prompt(request),
        config=types.GenerateContentConfig(
            temperature=0.0, seed=0, response_mime_type="application/json", response_schema=CanonicalChoice,
            max_output_tokens=ANSWER_TOKENS + budget, thinking_config=types.ThinkingConfig(thinking_budget=budget)),
    )
    candidate = (response.candidates or [None])[0]
    if candidate is not None and "MAX_TOKENS" in str(candidate.finish_reason or ""):
        raise ValueError("canonicalisation answer was cut off at the output limit")
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, CanonicalChoice):
        return parsed
    return CanonicalChoice.model_validate_json(response.text or "")
