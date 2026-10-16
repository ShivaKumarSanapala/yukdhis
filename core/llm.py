"""Gemini on Vertex AI client factory (google-genai SDK)."""

from __future__ import annotations

import os
from functools import lru_cache

from core.config import get_settings


@lru_cache(maxsize=1)
def genai_client():
    from google import genai

    settings = get_settings()
    if settings.project:
        return genai.Client(vertexai=True, project=settings.project, location=settings.location)
    if os.getenv("GOOGLE_API_KEY"):
        return genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    raise RuntimeError("Set GOOGLE_CLOUD_PROJECT (Vertex AI) to call Gemini.")
