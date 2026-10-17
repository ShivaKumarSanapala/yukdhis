"""Gemini on Vertex AI: client factory and retries for transient errors (google-genai SDK and ADK).

Vertex AI answers 429 RESOURCE_EXHAUSTED when shared capacity for a model is busy in a region, even far below the
project's quota. Every Gemini call therefore retries 429 and 5xx with exponential backoff and jitter:
- plain requests through the SDK's ``HttpRetryOptions`` on the client;
- streams through ``retry_stream`` / ``RetryingGemini``, which retry only before the first chunk arrives, so
  nothing is ever delivered twice.

Load control: at most ``GEMINI_MAX_CONCURRENCY`` (default 4) Gemini calls are in flight per instance, whatever
mix of parallel runs, parallel agents and chat is running, so a burst cannot trip shared-capacity 429s itself.
Every call logs one JSON line (``llm_call``: model, location, duration, tokens, attempt) so usage per run can be
measured in Cloud Logging.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import random
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Generator
from functools import lru_cache
from typing import Any, TypeVar

from core.config import get_settings

log = logging.getLogger("yukdhis.llm")
T = TypeVar("T")

RETRYABLE_STATUS = (429, 500, 502, 503, 504)
ATTEMPTS, INITIAL_DELAY_S, MAX_DELAY_S = 6, 2.0, 45.0  # about 2 minutes of waiting in the worst case


_MAX_CONCURRENT = max(1, int(os.getenv("GEMINI_MAX_CONCURRENCY", "4") or 4))
_SLOTS = threading.BoundedSemaphore(_MAX_CONCURRENT)  # one pool for threads (sync SDK calls) and event loops (ADK)


@contextlib.contextmanager
def slot() -> Generator[None, None, None]:
    """Hold one of the per-instance Gemini call slots (blocking, for threads)."""
    _SLOTS.acquire()
    try:
        yield
    finally:
        _SLOTS.release()


@contextlib.asynccontextmanager
async def async_slot() -> AsyncIterator[None]:
    """Hold a call slot without blocking the event loop."""
    while not _SLOTS.acquire(blocking=False):
        await asyncio.sleep(0.05)
    try:
        yield
    finally:
        _SLOTS.release()


def log_call(model: str, location: str, started: float, usage: Any = None, attempt: int = 1, status: int = 200) -> None:
    """One structured line per Gemini call (Cloud Logging parses JSON on stdout)."""
    def tokens(name: str) -> int | None:
        value = getattr(usage, name, None)
        return value if isinstance(value, int) else None

    print(json.dumps({"severity": "INFO" if status < 400 else "WARNING", "event": "llm_call", "model": model,
                      "location": location, "status": status, "attempt": attempt,
                      "duration_ms": round((time.monotonic() - started) * 1000),
                      "prompt_tokens": tokens("prompt_token_count"), "output_tokens": tokens("candidates_token_count"),
                      "thought_tokens": tokens("thoughts_token_count")}), flush=True)


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
            with slot():  # released before any backoff sleep
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
        client = genai.Client(vertexai=True, project=settings.project, location=location, http_options=http_options)
    elif os.getenv("GOOGLE_API_KEY"):
        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"], http_options=http_options)
    else:
        raise RuntimeError("Set GOOGLE_CLOUD_PROJECT (Vertex AI) to call Gemini.")
    _limit(client.models, location)
    return client


def _limit(models: Any, location: str) -> None:
    """Route the SDK's blocking calls through the shared slots and log them. Streams are limited by ``retry_stream``."""
    for name in ("generate_content", "embed_content"):
        call = getattr(models, name)

        def limited(*args: Any, __call=call, __name=name, **kwargs: Any) -> Any:
            started = time.monotonic()
            model = kwargs.get("model") or (args[0] if args else "?")
            try:
                with slot():
                    response = __call(*args, **kwargs)
            except Exception as error:
                log_call(str(model), location, started, status=status_of(error) or 500)
                raise
            log_call(str(model), location, started, getattr(response, "usage_metadata", None))
            return response

        setattr(models, name, limited)


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
            location = get_settings().gemini_location
            for attempt in range(1, ATTEMPTS + 1):
                started, began, usage = False, time.monotonic(), None
                try:
                    async with async_slot():  # released before any backoff sleep
                        async for response in super().generate_content_async(llm_request, stream):
                            started = True
                            usage = getattr(response, "usage_metadata", None) or usage
                            yield response
                    log_call(self.model, location, began, usage, attempt)
                    return
                except Exception as error:
                    if started:
                        log_call(self.model, location, began, usage, attempt, status_of(error) or 500)
                    if started or not is_retryable(error) or attempt == ATTEMPTS:
                        if not started:
                            log_call(self.model, location, began, None, attempt, status_of(error) or 500)
                        raise
                    log_call(self.model, location, began, None, attempt, status_of(error) or 500)
                    delay = backoff_delay(attempt)
                    log.warning("%s: %s on attempt %s/%s; retrying in %.1fs", self.model, status_of(error), attempt,
                                ATTEMPTS, delay)
                    await asyncio.sleep(delay)

    settings = get_settings()
    kwargs: dict[str, Any] = {"model": name, "retry_options": retry_options()}
    if settings.project and settings.gemini_location != os.getenv("GOOGLE_CLOUD_LOCATION", settings.location):
        kwargs["client_kwargs"] = {"location": settings.gemini_location}
    return RetryingGemini(**kwargs)
