"""Service-to-service OIDC: Eventarc signs for the legacy Cloud Run URL, so both URLs must be accepted audiences."""

from types import SimpleNamespace

from api.auth import service_audiences


def _request(host: str, path: str = "/api/events/gcs"):
    return SimpleNamespace(headers={"host": host}, url=SimpleNamespace(path=path))


def test_audiences_include_configured_and_the_url_used(monkeypatch):
    monkeypatch.setenv("SERVICE_AUDIENCE", "https://yukdhis-api-123.us-central1.run.app, ")
    audiences = service_audiences(_request("yukdhis-api-abc-uc.a.run.app"))
    assert audiences == ["https://yukdhis-api-123.us-central1.run.app", "https://yukdhis-api-abc-uc.a.run.app",
                         "https://yukdhis-api-abc-uc.a.run.app/api/events/gcs"]


def test_audiences_are_never_empty_and_deduplicated(monkeypatch):
    monkeypatch.delenv("SERVICE_AUDIENCE", raising=False)
    assert service_audiences(_request("svc.run.app", "/")) == ["https://svc.run.app", "https://svc.run.app/"]
    monkeypatch.setenv("SERVICE_AUDIENCE", "https://svc.run.app")
    assert service_audiences(_request("svc.run.app", "/x")) == ["https://svc.run.app", "https://svc.run.app/x"]
