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


def verify_service_caller(request: Request) -> str:
    """Google OIDC token from Eventarc/Scheduler/Pub/Sub push."""
    settings = get_settings()
    if settings.auth_mode != "firebase":
        return "local-service"
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_oauth2_token(_bearer(request), google_requests.Request(), audience=os.getenv("SERVICE_AUDIENCE"))
    except Exception as error:
        raise HTTPException(status_code=401, detail="Invalid service token") from error
    allowed = {e for e in (settings.eventarc_sa_email, os.getenv("ACTION_ENGINE_INVOKER_SA")) if e}
    if claims.get("email") not in allowed:
        raise HTTPException(status_code=403, detail="Caller is not an authorised service account")
    return claims["email"]
