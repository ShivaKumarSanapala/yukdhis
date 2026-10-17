"""fetch_circular: SSRF-safe RBI fetching with DNS and HTTP mocked (no network)."""

import os
import socket
import tempfile
from pathlib import Path

os.environ.update(YUKDHIS_RUNTIME_DIR=tempfile.mkdtemp(prefix="yukdhis-test-"), YUKDHIS_BACKEND="local",
                  AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import httpx  # noqa: E402
import pytest  # noqa: E402

from core.config import get_settings  # noqa: E402
from ingestion import fetch  # noqa: E402
from ingestion.fetch import MAX_BYTES, fetch_circular  # noqa: E402

PUBLIC_IP = "203.0.113.10"  # TEST-NET-3 is not global, so DNS answers below are patched per host
PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
PDF_URL = "https://rbidocs.rbi.org.in/rdocs/notification/PDFs/NT27AP.PDF"
PAGE_URL = "https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=13001&Mode=0"


@pytest.fixture
def net(monkeypatch):
    """Stub DNS (host -> ip) and HTTP (url -> response factory). Records the requests sent."""
    dns: dict[str, str] = {}
    routes: dict[str, callable] = {}
    sent: list[httpx.Request] = []

    def getaddrinfo(host, port, *args, **kwargs):
        if host not in dns:
            raise socket.gaierror("unknown host")
        family = socket.AF_INET6 if ":" in dns[host] else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (dns[host], port))]

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        host = request.headers["host"]
        url = f"https://{host}{request.url.raw_path.decode()}"
        if url not in routes:
            return httpx.Response(404)
        return routes[url]()

    monkeypatch.setattr(fetch, "_getaddrinfo", getaddrinfo)
    monkeypatch.setattr(fetch, "_transport", lambda: httpx.MockTransport(handler))
    monkeypatch.setattr(fetch, "_is_public", lambda ip: str(ip) == PUBLIC_IP or _real_is_public(ip))
    return dns, routes, sent


_real_is_public = fetch._is_public


def _pdf_response(body: bytes = PDF, ctype: str = "application/pdf"):
    return lambda: httpx.Response(200, headers={"content-type": ctype}, content=body)


def test_rejects_http_credentials_ports_and_unlisted_hosts(net):
    for url, message in [
        ("http://www.rbi.org.in/x.pdf", "https"),
        ("https://user:pw@www.rbi.org.in/x.pdf", "credentials"),
        ("https://www.rbi.org.in:8443/x.pdf", "port"),
        ("https://evil.example.com/x.pdf", "RBI website"),
        ("https://rbi.org.in.evil.example.com/x.pdf", "RBI website"),
        ("ftp://www.rbi.org.in/x.pdf", "https"),
        ("not a url", "https"),
    ]:
        with pytest.raises(ValueError, match=message):
            fetch_circular(url)
    assert net[2] == []  # nothing was requested


@pytest.mark.parametrize("ip", ["10.0.0.1", "169.254.169.254", "127.0.0.1", "192.168.1.5", "100.64.0.1", "::1",
                                "::ffff:10.0.0.1", "fd00:ec2::254", "0.0.0.0"])
def test_rejects_hosts_resolving_to_private_or_metadata_ips(net, ip):
    dns, routes, sent = net
    dns["www.rbi.org.in"] = ip
    routes[PAGE_URL] = _pdf_response()
    with pytest.raises(ValueError, match="private or reserved"):
        fetch_circular(PAGE_URL)
    assert sent == []


def test_real_public_check_blocks_metadata_and_private():
    import ipaddress
    for ip in ["169.254.169.254", "10.0.0.1", "::ffff:127.0.0.1", "172.16.0.1", "fe80::1"]:
        assert not _real_is_public(ipaddress.ip_address(ip))
    assert _real_is_public(ipaddress.ip_address("8.8.8.8"))


def test_redirects_are_rechecked_and_capped(net):
    dns, routes, sent = net
    dns.update({"www.rbi.org.in": PUBLIC_IP, "m.rbi.org.in": "10.0.0.1"})
    hop = "https://www.rbi.org.in/r{}"
    for i in range(5):
        routes[hop.format(i)] = (lambda n=i: httpx.Response(302, headers={"location": hop.format(n + 1)}))
    with pytest.raises(ValueError, match="Too many redirects"):
        fetch_circular(hop.format(0))
    assert len(sent) == 4  # the original request plus 3 redirects

    routes["https://www.rbi.org.in/to-private"] = lambda: httpx.Response(301, headers={"location": "https://m.rbi.org.in/x.pdf"})
    with pytest.raises(ValueError, match="private or reserved"):
        fetch_circular("https://www.rbi.org.in/to-private")
    routes["https://www.rbi.org.in/to-http"] = lambda: httpx.Response(301, headers={"location": "http://www.rbi.org.in/x.pdf"})
    with pytest.raises(ValueError, match="https"):
        fetch_circular("https://www.rbi.org.in/to-http")
    routes["https://www.rbi.org.in/to-evil"] = lambda: httpx.Response(307, headers={"location": "https://evil.example.com/"})
    with pytest.raises(ValueError, match="RBI website"):
        fetch_circular("https://www.rbi.org.in/to-evil")


def test_rejects_oversize_bodies(net):
    dns, routes, _ = net
    dns["rbidocs.rbi.org.in"] = PUBLIC_IP
    routes[PDF_URL] = lambda: httpx.Response(200, headers={"content-type": "application/pdf",
                                                           "content-length": str(MAX_BYTES + 1)}, content=b"%PDF-")
    with pytest.raises(ValueError, match="larger than 10 MB"):
        fetch_circular(PDF_URL)

    def streamed():
        yield b"%PDF-1.7\n"
        for _ in range(11):
            yield b"0" * (1024 * 1024)
    routes[PDF_URL] = lambda: httpx.Response(200, headers={"content-type": "application/pdf"}, content=streamed())
    with pytest.raises(ValueError, match="larger than 10 MB"):
        fetch_circular(PDF_URL)


@pytest.mark.parametrize("ctype, body", [
    ("application/zip", b"PK\x03\x04zip"),
    ("application/pdf", b"<html><body>not a pdf</body></html>"),
    ("text/html", PDF),
    ("application/octet-stream", PDF),
    ("text/plain", b"plain text"),
])
def test_rejects_wrong_content_type_or_magic(net, ctype, body):
    dns, routes, _ = net
    dns["rbidocs.rbi.org.in"] = PUBLIC_IP
    routes[PDF_URL] = _pdf_response(body, ctype)
    with pytest.raises(ValueError, match="PDF|HTML"):
        fetch_circular(PDF_URL)


def test_accepts_a_pdf_and_pins_the_validated_ip(net):
    dns, routes, sent = net
    dns["rbidocs.rbi.org.in"] = PUBLIC_IP
    routes[PDF_URL] = _pdf_response()
    source = fetch_circular(PDF_URL)
    assert set(source) == {"path", "filename", "display_uri", "trigger", "source_url", "content_type"}
    assert source["filename"] == "NT27AP.pdf" and source["trigger"] == "fetch" and source["content_type"] == "application/pdf"
    assert source["display_uri"] == source["source_url"] == PDF_URL
    path = Path(source["path"])
    assert path.read_bytes() == PDF and path.is_relative_to(get_settings().runtime_dir / "uploads")
    request = sent[0]
    assert request.url.host == PUBLIC_IP and request.headers["host"] == "rbidocs.rbi.org.in"
    assert request.extensions["sni_hostname"] == "rbidocs.rbi.org.in"


def test_html_page_prefers_the_linked_pdf(net):
    dns, routes, sent = net
    dns.update({"www.rbi.org.in": PUBLIC_IP, "rbidocs.rbi.org.in": PUBLIC_IP})
    page = f"""<!DOCTYPE html><html><head><title>RBI Notification</title><script>var x = 1;</script></head>
    <body><nav><a href="/Scripts/Home.aspx">Home</a></nav>
    <a href="/rdocs/other.pdf">Other</a>
    <a href="{PDF_URL}">PDF</a>
    <p>Risk Management and Inter-Bank Dealings</p></body></html>""".encode()
    routes[PAGE_URL] = lambda: httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=page)
    routes[PDF_URL] = _pdf_response()
    source = fetch_circular(PAGE_URL)
    assert source["filename"].endswith(".pdf") and source["source_url"] == PDF_URL
    assert Path(source["path"]).read_bytes() == PDF
    assert [r.headers["host"] for r in sent] == ["www.rbi.org.in", "rbidocs.rbi.org.in"]


def test_html_page_without_pdf_becomes_markdown_text(net):
    dns, routes, _ = net
    dns["www.rbi.org.in"] = PUBLIC_IP
    page = b"""<html><head><title>FERR circular</title><style>p{}</style></head><body>
    <h2>RBI/2026-27/293 A.P. (DIR Series) Circular No. 27</h2>
    <p>AD Category-I banks shall maintain a Foreign Exchange Risk Reserve.</p>
    <p>See <a href="/Scripts/BS_ViewMasDirections.aspx?id=10394">the Master Direction</a>.</p>
    <script>alert(1)</script></body></html>"""
    routes[PAGE_URL] = lambda: httpx.Response(200, headers={"content-type": "text/html"}, content=page)
    source = fetch_circular(PAGE_URL)
    text = Path(source["path"]).read_text()
    assert source["filename"] == "FERR-circular.md" and source["content_type"] == "text/markdown"
    assert text.startswith("## RBI/2026-27/293")  # the page's own heading beats a generic <title>
    assert "Foreign Exchange Risk Reserve" in text and "alert(1)" not in text and "p{}" not in text
    assert "https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=10394" in text


def test_allowlist_is_configurable(net, monkeypatch):
    dns, routes, _ = net
    monkeypatch.setenv("CIRCULAR_FETCH_ALLOWED_HOSTS", "rbidocs.rbi.org.in")
    with pytest.raises(ValueError, match="RBI website"):
        fetch_circular(PAGE_URL)
    dns["rbidocs.rbi.org.in"] = PUBLIC_IP
    routes[PDF_URL] = _pdf_response()
    assert fetch_circular(PDF_URL)["filename"] == "NT27AP.pdf"


def test_network_errors_are_user_safe(net):
    dns, routes, _ = net
    dns["rbidocs.rbi.org.in"] = PUBLIC_IP

    def boom():
        raise httpx.ConnectError("connection refused to 203.0.113.10")
    routes[PDF_URL] = boom
    with pytest.raises(ValueError, match="Could not download"):
        fetch_circular(PDF_URL)
    with pytest.raises(ValueError, match="resolve"):
        fetch_circular("https://m.rbi.org.in/x.pdf")


def test_aspnet_page_inside_a_form_keeps_its_text_and_drops_site_chrome(net):
    """rbi.org.in wraps every page in one ASP.NET <form>; the menu and archive lists are not the circular."""
    dns, routes, _ = net
    dns["www.rbi.org.in"] = PUBLIC_IP
    page = b"""<html><head><title>Master Directions | Official Website of Reserve Bank of India</title></head><body>
    <form id="form1" method="post"><div>Home About Us Notifications Master Directions</div>
    <table><tr><td><a href="https://rbidocs.rbi.org.in/rdocs/notification/PDFs/MD398.PDF">PDF</a> ( 871 kb )</td></tr>
    <tr><td>Reserve Bank of India (Commercial Banks - Asset Classification, Provisioning and Income Recognition)
    Directions, 2026</td></tr><tr><td>RBI/DOR/2026-27/398</td></tr>
    <tr><td>A bank shall recognise lifetime expected credit losses for Stage 2 and Stage 3 assets.</td></tr>
    <tr><td>Top</td></tr><tr><td>Previous Versions</td></tr><tr><td>2026</td></tr><tr><td>2025</td></tr></table>
    <input type="hidden" name="__VIEWSTATE" value="x"/><select><option>2025</option></select></form></body></html>"""
    routes[PAGE_URL] = lambda: httpx.Response(200, headers={"content-type": "text/html"}, content=page)
    routes["https://rbidocs.rbi.org.in/rdocs/notification/PDFs/MD398.PDF"] = lambda: httpx.Response(
        200, headers={"content-type": "text/html"}, content=b"<html>bot challenge</html>")  # rbidocs' JS challenge
    dns["rbidocs.rbi.org.in"] = PUBLIC_IP
    text = Path(fetch_circular(PAGE_URL)["path"]).read_text()
    assert "lifetime expected credit losses for Stage 2" in text and "RBI/DOR/2026-27/398" in text
    assert "About Us" not in text and "Previous Versions" not in text and "\n2025" not in text
