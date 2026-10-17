"""Fetch an RBI circular by URL, safely, and turn it into a pipeline source dict.

Security controls (this is user-supplied URL fetching, i.e. an SSRF surface):

* HTTPS only, default port only, no credentials in the URL.
* Exact hostname allowlist (``CIRCULAR_FETCH_ALLOWED_HOSTS``, default the RBI hosts).
* DNS is resolved here and **every** address must be public; private, loopback, link-local,
  CGNAT, multicast, reserved and cloud-metadata addresses are rejected. The connection is then
  pinned to the validated IP (TLS SNI and certificate verification still use the hostname), so a
  second DNS answer cannot redirect the request (no DNS-rebinding window).
* Redirects are followed manually, at most 3, and each hop is re-checked (scheme, host, IP).
* ~15 s timeout per request, 10 MB cap on the decoded body (checked while streaming).
* Only PDF or HTML, checked against both Content-Type and magic bytes.
* Response bodies are never logged.

HTML circular pages are reduced to their main text (saved as Markdown, so the local parser can
read them); when the page links to the circular's PDF, the PDF is fetched (with the same checks)
and preferred.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
import time
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from core.config import get_settings, split_list
from ingestion.documents import archive_to_gcs, document_id_for

log = logging.getLogger(__name__)

DEFAULT_ALLOWED_HOSTS = ("rbi.org.in", "www.rbi.org.in", "rbidocs.rbi.org.in", "m.rbi.org.in")
TIMEOUT_S = 15.0
MAX_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 3
REDIRECT_CODES = {301, 302, 303, 307, 308}
PDF_TYPES = {"application/pdf", "application/x-pdf"}
HTML_TYPES = {"text/html", "application/xhtml+xml"}
METADATA_IPS = {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254")}
USER_AGENT = "Yukdhis-CircularFetcher/1.0 (+compliance analysis)"
MIN_TEXT_CHARS = 40
DOC_LINK_RX = re.compile(r"notification|circular|direction|pressrelease|rdocs/|\.pdf$", re.IGNORECASE)

_getaddrinfo = socket.getaddrinfo  # indirection so tests can stub DNS


def _transport() -> httpx.BaseTransport | None:
    """Transport for outbound requests; tests replace this with ``httpx.MockTransport``."""
    return None


def allowed_hosts() -> frozenset[str]:
    configured = split_list(os.getenv("CIRCULAR_FETCH_ALLOWED_HOSTS"))
    return frozenset(h.lower().rstrip(".") for h in (configured or DEFAULT_ALLOWED_HOSTS))


def fetch_circular(url: str) -> dict:
    """Download a circular from an allow-listed RBI URL and store it like an uploaded file.

    Returns the same source dict ``POST /api/runs`` builds for an upload, plus ``source_url`` and
    ``content_type``. Raises ``ValueError`` with a short, user-safe message on any problem.
    """
    if not isinstance(url, str) or not url.strip() or len(url) > 2048:
        raise ValueError("Provide an https:// link to an RBI circular.")
    final_url, kind, body = _download(url.strip())
    filename, content = _pdf_name(final_url), body
    if kind == "html":
        page = _MainText(final_url)
        try:
            page.feed(body.decode(_charset(body), errors="replace"))
            page.close()
        except Exception:  # malformed markup: treat as unreadable rather than crash
            raise ValueError("The circular page could not be read.") from None
        pdf_url = page.pdf_link()
        pdf = None
        if pdf_url:
            try:
                pdf_final, pdf_kind, pdf_body = _download(pdf_url)
                pdf = (pdf_final, pdf_body) if pdf_kind == "pdf" else None
            except ValueError as error:
                log.warning("Linked PDF on %s could not be fetched (%s); using the page text.", urlsplit(final_url).hostname, error)
        if pdf:
            final_url, content, filename = pdf[0], pdf[1], _pdf_name(pdf[0])
        else:
            markdown = crop_page_chrome(page.markdown())
            if len(markdown) < MIN_TEXT_CHARS:
                raise ValueError("No circular text was found on that page.")
            content = markdown.encode("utf-8")
            filename = _slug(page.title or PurePosixPath(urlsplit(final_url).path).stem or "rbi-circular") + ".md"
    return _store(content, filename, final_url)


# Download with per-hop checks ----------------------------------------------------
def _download(url: str) -> tuple[str, str, bytes]:
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        host = check_url(current)
        ip = _resolve(host)
        status, headers, body = _get(current, host, ip)
        if status in REDIRECT_CODES:
            location = headers.get("location")
            if not location:
                raise ValueError("The RBI site sent a redirect without a location.")
            current = urljoin(current, location.strip())
            continue
        if status != 200:
            raise ValueError(f"The RBI site returned HTTP {status}.")
        kind = _content_kind(headers.get("content-type", ""), body)
        log.info("Fetched circular host=%s kind=%s bytes=%d", host, kind, len(body))
        return current, kind, body
    raise ValueError("Too many redirects.")


def check_url(url: str) -> str:
    """Validate scheme, credentials, port and host; return the normalised hostname."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise ValueError("That link is not a valid URL.") from None
    if parts.scheme.lower() != "https":
        raise ValueError("Only https:// links can be fetched.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ValueError("Links containing credentials are not allowed.")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or host not in allowed_hosts():
        raise ValueError("Only links on the RBI website can be fetched.")
    if port not in (None, 443):
        raise ValueError("Only the standard HTTPS port is allowed.")
    return host


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip in METADATA_IPS:
        return False
    return ip.is_global and not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                                 or ip.is_reserved or ip.is_unspecified)


def _resolve(host: str) -> str:
    try:
        infos = _getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        raise ValueError("Could not resolve the RBI host.") from None
    addresses = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        except ValueError:
            raise ValueError("The RBI host resolved to an invalid address.") from None
        if not _is_public(ip):
            raise ValueError("The link resolves to a private or reserved network address.")
        addresses.append(ip)
    if not addresses:
        raise ValueError("Could not resolve the RBI host.")
    return str(addresses[0])


def _get(url: str, host: str, ip: str) -> tuple[int, dict[str, str], bytes]:
    parts = urlsplit(url)
    netloc = f"[{ip}]" if ":" in ip else ip
    pinned = urlunsplit(("https", netloc, parts.path or "/", parts.query, ""))
    deadline = time.monotonic() + TIMEOUT_S
    try:
        with httpx.Client(timeout=httpx.Timeout(TIMEOUT_S), follow_redirects=False, trust_env=False, verify=True,
                          transport=_transport()) as client:
            request = client.build_request("GET", pinned, headers={
                "Host": host, "User-Agent": USER_AGENT, "Accept": "application/pdf, text/html;q=0.9"},
                extensions={"sni_hostname": host})
            response = client.send(request, stream=True)
            try:
                headers = {k.lower(): v for k, v in response.headers.items()}
                if response.status_code in REDIRECT_CODES or response.status_code != 200:
                    return response.status_code, headers, b""
                declared = headers.get("content-length", "")
                if declared.isdigit() and int(declared) > MAX_BYTES:
                    raise ValueError("The document is larger than 10 MB.")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError("The document is larger than 10 MB.")
                    if time.monotonic() > deadline:
                        raise ValueError("The RBI site took too long to respond.")
                    chunks.append(chunk)
                return response.status_code, headers, b"".join(chunks)
            finally:
                response.close()
    except httpx.TimeoutException:
        raise ValueError("The RBI site took too long to respond.") from None
    except httpx.HTTPError as error:
        log.warning("Circular fetch failed host=%s error=%s", host, type(error).__name__)
        raise ValueError("Could not download the circular from the RBI site.") from None


def _content_kind(content_type: str, body: bytes) -> str:
    mime = content_type.split(";")[0].strip().lower()
    head = body[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    if mime in PDF_TYPES:
        if not head.startswith(b"%PDF-"):
            raise ValueError("The response says it is a PDF but its content is not.")
        return "pdf"
    if mime in HTML_TYPES:
        lower = head.lower()
        if head.startswith(b"%PDF") or not (lower.startswith(b"<!doctype html") or b"<html" in lower):
            raise ValueError("The response says it is HTML but its content is not.")
        return "html"
    raise ValueError("Only PDF or HTML circulars can be fetched.")


def _charset(body: bytes) -> str:
    match = re.search(rb"charset=[\"']?([A-Za-z0-9_-]{2,20})", body[:2048])
    name = match.group(1).decode("ascii").lower() if match else "utf-8"
    try:
        "".encode(name)
        return name
    except LookupError:
        return "utf-8"


# HTML main text -----------------------------------------------------------------
_SIZE_LINE = re.compile(r"\(\s*\d+\s*kb\s*\)\s*$", re.IGNORECASE)
_END_LINES = {"Top", "Back to previous page", "Previous Versions", "More Links :"}
_ARCHIVE = re.compile(r"(?:## )?(?:19|20)\d\d|## Archives|All Months")


def crop_page_chrome(markdown: str) -> str:
    """Drop RBI site chrome around a document: the menu before the "(NNN kb)" download line and the archive and
    footer lists after "Top". Pages without that layout are returned unchanged (apart from the heading)."""
    lines = markdown.splitlines()
    start = next((i + 1 for i, line in enumerate(lines[:80]) if _SIZE_LINE.search(line)), None)
    if start is None:
        return markdown
    end = next((i for i in range(start, len(lines)) if lines[i].strip() in _END_LINES
                or _ARCHIVE.fullmatch(lines[i].strip())), len(lines))
    kept = "\n".join(lines[start:end]).strip()  # the <title> is the site's ("Master Directions | ..."), not the circular's
    return markdown if len(kept) < MIN_TEXT_CHARS else kept + "\n"

class _MainText(HTMLParser):
    # No "form": ASP.NET pages (all of rbi.org.in) wrap the whole page in one <form>, so skipping it dropped the text.
    SKIP = {"script", "style", "noscript", "nav", "header", "footer", "iframe", "svg", "button", "select", "textarea",
            "template"}
    BLOCK = {"p", "div", "li", "tr", "br", "table", "section", "article", "ul", "ol", "blockquote", "td", "th"}

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts: list[str] = []
        self.skip_depth = 0
        self.title = ""
        self._in_title = False
        self._heading: int | None = None
        self._anchor: str | None = None
        self._anchor_text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip_depth += 1
        if tag == "title":
            self._in_title = True
        if self.skip_depth:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self._heading = int(tag[1])
            self.parts.append("\n\n" + "#" * self._heading + " ")
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            self._anchor = urljoin(self.base_url, href.strip()) if href and not href.lower().startswith(("javascript:", "mailto:")) else None
            self._anchor_text = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if re.fullmatch(r"h[1-6]", tag) and self._heading:
            self._heading = None
            self.parts.append("\n\n")
        elif tag == "a" and self._anchor is not None:
            text = " ".join("".join(self._anchor_text).split())
            self.links.append((self._anchor, text))
            parts = urlsplit(self._anchor)
            if parts.scheme in ("http", "https") and (DOC_LINK_RX.search(parts.path) or DOC_LINK_RX.search(parts.query)):
                self.parts.append(f" ({self._anchor})")  # keep document links so references can be surfaced
            self._anchor = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self.skip_depth or not data:
            return
        if self._anchor is not None:
            self._anchor_text.append(data)
        self.parts.append(re.sub(r"\s+", " ", data))

    def markdown(self) -> str:
        raw = "".join(self.parts)
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        body = "\n".join(line for line in lines if line and line != "#")
        title = " ".join(self.title.split())
        if title and not body.lstrip().startswith("#"):
            body = f"# {title}\n\n{body}"
        return re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"

    def pdf_link(self) -> str | None:
        hosts = allowed_hosts()
        pdfs = [href for href, _ in self.links
                if urlsplit(href).scheme == "https" and (urlsplit(href).hostname or "").lower() in hosts
                and urlsplit(href).path.lower().endswith(".pdf")]
        preferred = [h for h in pdfs if (urlsplit(h).hostname or "").lower() == "rbidocs.rbi.org.in"]
        return (preferred or pdfs or [None])[0]


# Storage (same shape as POST /api/runs builds for an upload) ----------------------
def _slug(text: str, limit: int = 80) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-._")[:limit]
    return slug or "rbi-circular"


def _pdf_name(url: str) -> str:
    stem = PurePosixPath(urlsplit(url).path).name
    stem = re.sub(r"\.pdf$", "", stem, flags=re.IGNORECASE)
    return _slug(stem or "rbi-circular") + ".pdf"


def _store(content: bytes, filename: str, url: str) -> dict:
    mime = "application/pdf" if filename.endswith(".pdf") else "text/markdown"
    base = {"filename": filename, "display_uri": url, "trigger": "fetch", "source_url": url, "content_type": mime}
    gcs_uri = archive_to_gcs(content, filename)
    if gcs_uri:
        return {"gcs_uri": gcs_uri, **base}
    target = get_settings().runtime_dir / "uploads" / f"{document_id_for(content)}-{filename}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return {"path": str(target), **base}
