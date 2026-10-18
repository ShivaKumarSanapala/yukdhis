"""Retrieval index over ingested circulars: chunks are embedded once and searched by meaning (RAG).

Storage is the app database (``circular_chunks``). On Cloud SQL Postgres the ``vector`` extension is used when it can be
created (``embedding_vec`` column, HNSW index, cosine ``<=>``); otherwise, and on SQLite, the embeddings are kept as JSON
and compared in Python, which is fine for a few thousand chunks. Everything is tenant-scoped and text is sanitized
before it is embedded or stored, so no direct personal identifier reaches the index.

Indexing is best effort: a failure is logged and never fails an analysis run.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Callable

from sqlalchemy import delete, func, insert, select, text

from core.app_store import circular_chunks, get_app_store, now
from core.config import get_settings
from core.models import ParsedDocument

log = logging.getLogger(__name__)
DIMENSIONS = 768          # text-embedding-005
BATCH = 16
MAX_EMBED_CHARS = 6000    # about 1.5k tokens, under the model's input limit
MAX_RESULTS = 10
_pgvector: dict[int, bool] = {}   # per engine: is the vector column usable


def enabled() -> bool:
    """On when embeddings are available (GCP) or forced with ``CIRCULAR_INDEX=1`` (tests inject an embedder)."""
    import os

    flag = os.getenv("CIRCULAR_INDEX")
    return flag == "1" if flag in {"0", "1"} else get_settings().graph_backend == "gcp"


def embed_texts(texts: list[str], query: bool = False) -> list[list[float]]:
    """Vertex AI embeddings (regional endpoint). Tests replace this function."""
    from google.genai import types

    from core.llm import regional_genai_client

    config = types.EmbedContentConfig(task_type="RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT")
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH):
        batch = [t[:MAX_EMBED_CHARS] or " " for t in texts[start:start + BATCH]]
        response = regional_genai_client().models.embed_content(model=get_settings().embedding_model, contents=batch, config=config)
        vectors += [list(item.values) for item in response.embeddings]
    return vectors


def _literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vector) + "]"


def _has_pgvector(engine) -> bool:
    key = id(engine)
    if key in _pgvector:
        return _pgvector[key]
    ok = False
    if engine.dialect.name == "postgresql":
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                conn.execute(text(f"ALTER TABLE circular_chunks ADD COLUMN IF NOT EXISTS embedding_vec vector({DIMENSIONS})"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_circular_chunks_vec ON circular_chunks "
                                  "USING hnsw (embedding_vec vector_cosine_ops)"))
            ok = True
        except Exception as error:  # noqa: BLE001 - no privilege to create the extension, or it is not installed
            log.warning("pgvector unavailable (%s); circular search will compare embeddings in Python", type(error).__name__)
    _pgvector[key] = ok
    return ok


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _clean(content: str, document_id: str) -> str:
    from privacy.sanitizer import get_sanitizer

    return get_sanitizer().sanitize(content, f"index-{document_id}", local=True).text


def index_document(document: ParsedDocument, force: bool = False) -> int:
    """Embed and store a parsed document's chunks; returns how many were indexed (0 when already indexed or disabled)."""
    if not enabled() or not document.chunks:
        return 0
    store = get_app_store()
    with store.engine.connect() as conn:
        have = conn.execute(select(func.count()).select_from(circular_chunks).where(
            circular_chunks.c.tenant_id == store.tenant_id, circular_chunks.c.document_id == document.document_id)).scalar() or 0
    if have == len(document.chunks) and not force:
        return 0
    contents = [_clean(c.content, document.document_id) for c in document.chunks]
    vectors = embed_texts(contents)
    if len(vectors) != len(contents):
        raise RuntimeError("embedding count does not match chunk count")
    use_vec = _has_pgvector(store.engine)
    with store.engine.begin() as conn:
        conn.execute(delete(circular_chunks).where(circular_chunks.c.tenant_id == store.tenant_id,
                                                   circular_chunks.c.document_id == document.document_id))
        for chunk, content, vector in zip(document.chunks, contents, vectors):
            conn.execute(insert(circular_chunks).values(
                tenant_id=store.tenant_id, chunk_id=chunk.chunk_id, document_id=document.document_id, title=document.title[:512],
                headings=json.dumps(chunk.headings), page_start=chunk.page_start, page_end=chunk.page_end, content=content,
                embedding=json.dumps([round(v, 6) for v in vector]), model=get_settings().embedding_model,
                source_uri=document.source_uri[:1024], created_at=now()))
            if use_vec:
                conn.execute(text("UPDATE circular_chunks SET embedding_vec = CAST(:v AS vector) "
                                  "WHERE tenant_id = :t AND chunk_id = :c"), {"v": _literal(vector), "t": store.tenant_id, "c": chunk.chunk_id})
    return len(contents)


def index_document_safely(document: ParsedDocument) -> int:
    try:
        return index_document(document)
    except Exception:  # noqa: BLE001 - the analysis must not depend on the index
        log.warning("Could not index circular %s", document.document_id, exc_info=True)
        return 0


def _row(row, score: float) -> dict:
    return {"document_id": row["document_id"], "chunk_id": row["chunk_id"], "title": row["title"],
            "headings": json.loads(row["headings"] or "[]"), "pages": [row["page_start"], row["page_end"]],
            "score": round(score, 3), "text": row["content"]}


def search(query: str, top_k: int = 5, document_id: str = "", embed: Callable[..., list[list[float]]] | None = None) -> list[dict]:
    """The chunks closest in meaning to ``query`` (tenant-scoped), best first."""
    top_k = max(1, min(int(top_k), MAX_RESULTS))
    vector = (embed or embed_texts)([query], query=True)[0]
    store = get_app_store()
    where = [circular_chunks.c.tenant_id == store.tenant_id]
    if document_id:
        where.append(circular_chunks.c.document_id == document_id)
    if _has_pgvector(store.engine):
        sql = ("SELECT document_id, chunk_id, title, headings, page_start, page_end, content, "
               "1 - (embedding_vec <=> CAST(:v AS vector)) AS score FROM circular_chunks "
               "WHERE tenant_id = :t AND embedding_vec IS NOT NULL" + (" AND document_id = :d" if document_id else "")
               + " ORDER BY embedding_vec <=> CAST(:v AS vector) LIMIT :k")
        params = {"v": _literal(vector), "t": store.tenant_id, "k": top_k, **({"d": document_id} if document_id else {})}
        with store.engine.connect() as conn:
            return [_row(r, float(r["score"])) for r in conn.execute(text(sql), params).mappings().all()]
    with store.engine.connect() as conn:
        rows = conn.execute(select(circular_chunks).where(*where)).mappings().all()
    scored = sorted(((_cosine(vector, json.loads(r["embedding"])), r) for r in rows), key=lambda p: p[0], reverse=True)
    return [_row(r, s) for s, r in scored[:top_k]]


def stats() -> dict:
    store = get_app_store()
    with store.engine.connect() as conn:
        rows = conn.execute(select(circular_chunks.c.document_id, circular_chunks.c.title, func.count()).where(
            circular_chunks.c.tenant_id == store.tenant_id).group_by(circular_chunks.c.document_id, circular_chunks.c.title)).all()
    return {"documents": [{"document_id": d, "title": t, "chunks": n} for d, t, n in rows], "pgvector": _pgvector.get(id(store.engine), False)}
