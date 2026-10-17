"""Gemini on Vertex AI: client factory and retries for transient errors (google-genai SDK and ADK).

Vertex AI answers 429 RESOURCE_EXHAUSTED when shared capacity for a model is busy in a region, even far below the
project's quota. Every Gemini call therefore retries 429 and 5xx with exponential backoff and jitter:
- plain requests through the SDK's ``HttpRetryOptions`` on the client;
- streams through ``retry_stream`` / ``RetryingGemini``, which retry only before the first chunk arrives, so
  nothing is ever delivered twice.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from functools import lru_cache
from typing import Any, TypeVar

from core.config import get_settings

log = logging.getLogger("yukdhis.llm")
T = TypeVar("T")

RETRYABLE_STATUS = (429, 500, 502, 503, 504)
ATTEMPTS, INITIAL_DELAY_S, MAX_DELAY_S = 6, 2.0, 45.0  # about 2 minutes of waiting in the worst case


def retry_options():
    from google.genai import types

    return types.HttpRetryOptions(attempts=ATTEMPTS, initial_delay=INITIAL_DELAY_S, max_delay=MAX_DELAY_S, exp_base=2.0,
                                  jitter=1.0, http_status_codes=list(RETRYABLE_STATUS))


def status_of(error: BaseException) -> int | None:
    for attr in ("code", "status_code"):
        value = getattr(error, attr, None)
        if isinstance(value, int):
            return value
    return None


def is_retryable(error: BaseException) -> bool:
    return status_of(error) in RETRYABLE_STATUS


def backoff_delay(attempt: int) -> float:
    """Delay before retry number ``attempt`` (1-based): 2, 4, 8, 16, 32, 45 s, each with up to 1 s of jitter."""
    return min(MAX_DELAY_S, INITIAL_DELAY_S * 2 ** (attempt - 1)) + random.uniform(0, 1)


def retry_stream(make: Callable[[], Iterable[T]], *, attempts: int = ATTEMPTS, sleep: Callable[[float], None] = time.sleep,
                 label: str = "gemini") -> Iterator[T]:
    """Iterate ``make()``; on a retryable error before the first item, back off and start again."""
    for attempt in range(1, attempts + 1):
        started = False
        try:
            for item in make():
                started = True
                yield item
            return
        except Exception as error:
            if started or not is_retryable(error) or attempt == attempts:
                raise
            delay = backoff_delay(attempt)
            log.warning("%s: %s on attempt %s/%s; retrying in %.1fs", label, status_of(error), attempt, attempts, delay)
            sleep(delay)


@lru_cache(maxsize=4)
def _client(location: str):
    from google import genai
    from google.genai import types

    settings = get_settings()
    http_options = types.HttpOptions(retry_options=retry_options())
    if settings.project:
        return genai.Client(vertexai=True, project=settings.project, location=location, http_options=http_options)
    if os.getenv("GOOGLE_API_KEY"):
        return genai.Client(api_key=os.environ["GOOGLE_API_KEY"], http_options=http_options)
    raise RuntimeError("Set GOOGLE_CLOUD_PROJECT (Vertex AI) to call Gemini.")


def genai_client():
    """Client for Gemini generation, at ``GEMINI_LOCATION`` (may be "global")."""
    return _client(get_settings().gemini_location)


def regional_genai_client():
    """Client at the deployment region (``GOOGLE_CLOUD_LOCATION``), for models not served on the global endpoint,
    such as ``text-embedding-005``."""
    return _client(get_settings().location)


def adk_model(name: str):
    """The ADK model for ``name``: Gemini with retries (and ``GEMINI_LOCATION`` when it differs from the default)."""
    from google.adk.models.google_llm import Gemini

    class RetryingGemini(Gemini):
        """Retries 429/5xx raised before the first response chunk (ADK streams in SSE mode)."""

        async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncIterator[Any]:
            for attempt in range(1, ATTEMPTS + 1):
                started = False
                try:
                    async for response in super().generate_content_async(llm_request, stream):
                        started = True
                        yield response
                    return
                except Exception as error:
                    if started or not is_retryable(error) or attempt == ATTEMPTS:
                        raise
                    delay = backoff_delay(attempt)
                    log.warning("%s: %s on attempt %s/%s; retrying in %.1fs", self.model, status_of(error), attempt,
                                ATTEMPTS, delay)
                    await asyncio.sleep(delay)

    settings = get_settings()
    kwargs: dict[str, Any] = {"model": name, "retry_options": retry_options()}
    if settings.project and settings.gemini_location != os.getenv("GOOGLE_CLOUD_LOCATION", settings.location):
        kwargs["client_kwargs"] = {"location": settings.gemini_location}
    return RetryingGemini(**kwargs)
