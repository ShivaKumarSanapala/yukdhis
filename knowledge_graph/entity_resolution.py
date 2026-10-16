"""Canonical entity resolution: map policy wording to BIAN graph nodes.

"four-wheeler advances" must resolve to ``product:auto-loan-floating`` rather
than create a new disconnected node.  Lexical/synonym scoring always runs; on
GCP, Vertex AI text embeddings add semantic similarity.  Every match reports
its score and method so the Mapper's decision is auditable.
"""

from __future__ import annotations

import logging
import math
import re
from functools import lru_cache

from core.config import get_settings
from core.models import EntityMatch

log = logging.getLogger(__name__)
THRESHOLD = 0.55
_STOP = {"the", "a", "an", "of", "and", "or", "all", "to", "on", "for", "based", "loans", "loan", "by", "rate", "s"}
_PLURAL = re.compile(r"(?<=[a-z])s\b")


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", _PLURAL.sub("", text.lower().replace("-", " ")))
    return {w for w in words if w not in _STOP}


def lexical_score(term: str, candidates: list[str]) -> float:
    target = _tokens(term)
    if not target:
        return 0.0
    best = 0.0
    norm_term = " ".join(sorted(target))
    for candidate in candidates:
        tokens = _tokens(candidate)
        if not tokens:
            continue
        if " ".join(sorted(tokens)) == norm_term:
            return 1.0
        overlap = len(target & tokens) / len(target | tokens)
        best = max(best, overlap)
    return round(best, 3)


class EntityResolver:
    def __init__(self, use_embeddings: bool | None = None) -> None:
        settings = get_settings()
        self.use_embeddings = settings.graph_backend == "gcp" if use_embeddings is None else use_embeddings
        self.model = settings.embedding_model

    def resolve(self, term: str, nodes: list[dict], node_type: str) -> list[EntityMatch]:
        scored: list[EntityMatch] = []
        embed_scores = self._embedding_scores(term, nodes) if self.use_embeddings else {}
        for node in nodes:
            names = [node["name"], *node["attributes"].get("synonyms", [])]
            lex = lexical_score(term, names)
            emb = embed_scores.get(node["id"], 0.0)
            score, method = (emb, "vertex-embedding") if emb > lex else (lex, "lexical-synonym")
            if score >= THRESHOLD:
                scored.append(EntityMatch(term=term, node_id=node["id"], node_type=node_type, score=score, method=method))
        scored.sort(key=lambda m: (-m.score, m.node_id))
        if not scored:
            return [EntityMatch(term=term, node_id=None, node_type=node_type, score=0.0, method="unresolved")]
        top = scored[0].score
        return [m for m in scored if m.score >= top - 0.05]

    def _embedding_scores(self, term: str, nodes: list[dict]) -> dict[str, float]:
        try:
            vectors = _embed(self.model, (term, *(f"{n['name']}: {', '.join(n['attributes'].get('synonyms', []))}" for n in nodes)))
        except Exception as error:  # Network/IAM failures degrade to lexical matching.
            log.warning("Embedding resolution unavailable, using lexical only: %s", error)
            return {}
        query, rest = vectors[0], vectors[1:]
        return {node["id"]: round(_cosine(query, vector), 3) for node, vector in zip(nodes, rest)}


@lru_cache(maxsize=512)
def _embed(model: str, texts: tuple[str, ...]) -> list[list[float]]:
    from core.llm import genai_client

    response = genai_client().models.embed_content(model=model, contents=list(texts))
    return [list(item.values) for item in response.embeddings]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0
