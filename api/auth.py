"""Authentication.

AUTH_MODE=firebase: browser requests carry a Firebase Authentication ID token
(Google sign-in), verified server-side; optional ALLOWED_EMAIL_DOMAIN gate.
Eventarc / Cloud Scheduler / Pub/Sub push calls carry a Google-signed OIDC
token for a known service account.  AUTH_MODE=none is for local development.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import HTTPException, Request

from core.config import get_settings


@dataclass(frozen=True)
class User:
    user_id: str
    email: str


LOCAL_USER = User(user_id="local-officer", email="officer@localhost")


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return header[7:].strip()


def verify_firebase_token(token: str) -> User:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    settings = get_settings()
    try:
        claims = id_token.verify_firebase_token(token, google_requests.Request(), audience=settings.project)
    except Exception as error:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from error
    email = (claims or {}).get("email", "")
    domain = os.getenv("ALLOWED_EMAIL_DOMAIN")
    if not email or not claims.get("email_verified") or (domain and not email.lower().endswith("@" + domain.lower())):
        raise HTTPException(status_code=403, detail="Account is not authorised for this workspace")
    return User(user_id=claims["sub"], email=email)


def current_user(request: Request) -> User:
    if get_settings().auth_mode != "firebase":
        return LOCAL_USER
    return verify_firebase_token(_bearer(request))


def _log_rejected_service_token(request: Request, error: Exception) -> None:
    """Why a service token failed: the unverified ``aud``/``email`` claims and the error type (never the token)."""
    import base64
    import json
    import logging

    claims: dict = {}
    try:
        payload = _bearer(request).split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception:  # noqa: BLE001 - diagnostics only
        pass
    logging.getLogger("yukdhis.auth").warning(
        "Rejected service token on %s: %s (%s); aud=%s email=%s expected=%s", request.url.path, type(error).__name__,
        str(error)[:160], claims.get("aud"), claims.get("email"), service_audiences(request))


def service_audiences(request: Request) -> list[str]:
    """Audiences a Google OIDC token for this service may carry (never empty, so the audience is always checked).

    ``SERVICE_AUDIENCE`` (comma-separated) plus the URL the caller actually used: Cloud Run serves each service on two
    URLs (``<name>-<project-number>.<region>.run.app`` and the legacy ``<name>-<hash>-<region>.a.run.app``) and Eventarc
    signs for the legacy one, so pinning only the new URL rejected every Eventarc delivery with 401. The Host header
    can only name this service (Cloud Run routes by it), so accepting it does not widen trust; the caller's
    service-account email is still checked below.
    """
    configured = [a.strip() for a in (os.getenv("SERVICE_AUDIENCE") or "").split(",") if a.strip()]
    host = request.headers.get("host", "")
    used = [f"https://{host}", f"https://{host}{request.url.path}"] if host else []
    return list(dict.fromkeys(configured + used))


def verify_service_caller(request: Request) -> str:
    """Google OIDC token from Eventarc/Scheduler/Pub/Sub push."""
    settings = get_settings()
    if settings.auth_mode != "firebase":
        return "local-service"
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_oauth2_token(_bearer(request), google_requests.Request(), audience=service_audiences(request))
    except Exception as error:
        _log_rejected_service_token(request, error)
        raise HTTPException(status_code=401, detail="Invalid service token") from error
    allowed = {e for e in (settings.eventarc_sa_email, os.getenv("ACTION_ENGINE_INVOKER_SA")) if e}
    if claims.get("email") not in allowed:
        raise HTTPException(status_code=403, detail="Caller is not an authorised service account")
    return claims["email"]
