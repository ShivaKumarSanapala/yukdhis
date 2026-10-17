"""Document loading and layout-aware semantic chunking.

gcp:   Cloud Document AI Layout Parser (PDF/DOCX/HTML) with ancestor-heading
       chunking, so each clause keeps its section context and page span.
local: Markdown/plain-text circulars chunked on structural boundaries
       (headings, then bullet clauses).  PDFs require Document AI.
"""

from __future__ import annotations

import hashlib
import io
import mimetypes
import re
from pathlib import Path

from core.config import Settings, get_settings
from core.models import DocumentChunk, ParsedDocument

TEXT_TYPES = {"text/markdown", "text/plain", "text/x-markdown"}
DOCAI_TYPES = {"application/pdf", "text/html", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
MAX_CHUNK_CHARS = 1800


# Explicit map: slim container images ship no /etc/mime.types, so ``mimetypes`` cannot be relied on for .docx.
KNOWN_TYPES = {".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain", ".pdf": "application/pdf",
               ".html": "text/html", ".htm": "text/html",
               ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def guess_mime(filename: str) -> str:
    known = KNOWN_TYPES.get(Path(filename).suffix.lower())
    return known or mimetypes.guess_type(filename)[0] or "application/octet-stream"


def document_id_for(content: bytes) -> str:
    return "doc-" + hashlib.sha256(content).hexdigest()[:16]


def parse_document(content: bytes, filename: str, source_uri: str | None = None,
                   settings: Settings | None = None) -> ParsedDocument:
    settings = settings or get_settings()
    mime = guess_mime(filename)
    doc_id = document_id_for(content)
    uri = source_uri or f"upload://{filename}"
    if mime in TEXT_TYPES:
        text = content.decode("utf-8", errors="strict")
        chunks = chunk_markdown(text, doc_id)
        title = next((c.headings[0] for c in chunks if c.headings), filename)
        return ParsedDocument(document_id=doc_id, source_uri=uri, mime_type=mime, title=title, parser="local-markdown", chunks=chunks)
    if mime in DOCAI_TYPES:
        if settings.document_backend != "gcp":
            raise ValueError("PDF/DOCX/HTML parsing uses Cloud Document AI; set DOCUMENT_BACKEND=gcp or upload Markdown.")
        return _document_ai(content, mime, doc_id, uri, filename, settings)
    raise ValueError(f"Unsupported document type {mime!r}. Upload PDF, DOCX, HTML, Markdown or text.")


def chunk_markdown(text: str, doc_id: str) -> list[DocumentChunk]:
    sections: list[tuple[list[str], list[str]]] = []
    headings: list[str] = []
    body: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match:
            if body:
                sections.append((list(headings), body))
                body = []
            level = len(match.group(1))
            headings = headings[: level - 1] + [match.group(2).strip()]
        elif line.strip():
            body.append(line.rstrip())
    if body or not sections:
        sections.append((list(headings), body))
    chunks: list[DocumentChunk] = []
    for heads, lines in sections:
        buffer = ""
        for line in lines:
            if len(buffer) + len(line) > MAX_CHUNK_CHARS and buffer:
                chunks.append(_chunk(doc_id, len(chunks), heads, buffer))
                buffer = ""
            buffer += line + "\n"
        if buffer or not lines:
            chunks.append(_chunk(doc_id, len(chunks), heads, buffer))
    return chunks


def _chunk(doc_id: str, index: int, headings: list[str], body: str) -> DocumentChunk:
    context = " > ".join(headings)
    return DocumentChunk(chunk_id=f"{doc_id}:c{index:03d}", content=(f"[{context}]\n" if context else "") + body.strip(),
                         headings=headings)


# Document AI online (synchronous) processing accepts at most 30 pages per request for the layout parser.
DOCAI_PAGES_PER_REQUEST = 30
MAX_PDF_PAGES = 300


def pdf_parts(content: bytes, per_part: int = DOCAI_PAGES_PER_REQUEST, max_pages: int = MAX_PDF_PAGES) -> list[tuple[int, bytes]]:
    """Split a PDF into ``(first_page_offset, bytes)`` parts of at most ``per_part`` pages (one part if it fits)."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("The PDF is password-protected; upload an unprotected copy.")
        total = len(reader.pages)
    except PdfReadError as error:
        raise ValueError(f"The PDF could not be read: {error}") from error
    if total > max_pages:
        raise ValueError(f"The PDF has {total} pages; the limit is {max_pages}. Upload the relevant part of the circular.")
    if total <= per_part:
        return [(0, content)]
    parts = []
    for start in range(0, total, per_part):
        writer = PdfWriter()
        for index in range(start, min(start + per_part, total)):
            writer.add_page(reader.pages[index])
        buffer = io.BytesIO()
        writer.write(buffer)
        parts.append((start, buffer.getvalue()))
    return parts


def _document_ai(content: bytes, mime: str, doc_id: str, uri: str, filename: str, settings: Settings) -> ParsedDocument:
    settings.require("documentai_processor")
    from google.api_core.client_options import ClientOptions
    from google.api_core.exceptions import InvalidArgument
    from google.cloud import documentai

    location = settings.documentai_processor.split("/locations/")[1].split("/")[0]
    client = documentai.DocumentProcessorServiceClient(
        client_options=ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com"))
    options = documentai.ProcessOptions(layout_config=documentai.ProcessOptions.LayoutConfig(
        chunking_config=documentai.ProcessOptions.LayoutConfig.ChunkingConfig(chunk_size=500, include_ancestor_headings=True)))
    parts = pdf_parts(content) if mime == "application/pdf" else [(0, content)]
    chunks: list[DocumentChunk] = []
    for part_index, (offset, part) in enumerate(parts):
        request = documentai.ProcessRequest(name=settings.documentai_processor, process_options=options,
                                            raw_document=documentai.RawDocument(content=part, mime_type=mime))
        try:
            document = client.process_document(request=request).document
        except InvalidArgument as error:
            if "pages exceed the limit" in str(error):
                raise ValueError(f"{filename} has more pages than Document AI accepts in one request "
                                 f"({DOCAI_PAGES_PER_REQUEST}). Export it as PDF (PDFs are split automatically).") from error
            raise
        prefix = f"{doc_id}:" if len(parts) == 1 else f"{doc_id}:p{part_index}-"  # single part keeps the old ids
        chunks += [
            DocumentChunk(chunk_id=f"{prefix}{chunk.chunk_id or index}", content=chunk.content,
                          page_start=(chunk.page_span.page_start + offset) if chunk.page_span.page_start else None,
                          page_end=(chunk.page_span.page_end + offset) if chunk.page_span.page_end else None)
            for index, chunk in enumerate(document.chunked_document.chunks)
        ]
    if not chunks:
        raise ValueError("Document AI returned no text chunks.")
    first = chunks[0].content.strip().splitlines()[0][:200]
    return ParsedDocument(document_id=doc_id, source_uri=uri, mime_type=mime, title=first or filename,
                          parser="document-ai-layout-parser", chunks=chunks)


def archive_to_gcs(content: bytes, filename: str, settings: Settings | None = None) -> str | None:
    """Keep the original upload in the circulars bucket (Watcher also reads it)."""
    settings = settings or get_settings()
    if settings.document_backend != "gcp" or not settings.circulars_bucket:
        return None
    from google.cloud import storage

    name = f"archive/{document_id_for(content)}/{Path(filename).name}"
    blob = storage.Client(project=settings.project).bucket(settings.circulars_bucket).blob(name)
    if not blob.exists():
        blob.upload_from_string(content, content_type=guess_mime(filename))
    return f"gs://{settings.circulars_bucket}/{name}"
