"""Vertex AI 429 RESOURCE_EXHAUSTED (regional shared capacity) must be retried, never fail a run on the first hit."""

import asyncio

import pytest

from core import llm


class Http(Exception):
    def __init__(self, code: int):
        super().__init__(f"{code} error")
        self.code = code


def test_retry_options_cover_429_and_5xx():
    options = llm.retry_options()
    assert options.attempts >= 5 and set(options.http_status_codes) >= {429, 500, 503}
    assert llm.is_retryable(Http(429)) and llm.is_retryable(Http(503)) and not llm.is_retryable(Http(400))
    assert not llm.is_retryable(ValueError("x"))


def test_retry_stream_retries_before_the_first_chunk_only():
    calls, slept = [], []

    def make():
        calls.append(1)
        if len(calls) < 3:
            raise Http(429)
        yield "a"
        yield "b"

    assert list(llm.retry_stream(make, sleep=slept.append)) == ["a", "b"]
    assert len(calls) == 3 and len(slept) == 2 and slept[0] < slept[1]

    def breaks_midway():
        yield "a"
        raise Http(429)

    with pytest.raises(Http):  # a chunk was already delivered: retrying would duplicate it
        list(llm.retry_stream(breaks_midway, sleep=slept.append))
    with pytest.raises(Http):  # gives up after the configured attempts
        list(llm.retry_stream(lambda: (_ for _ in ()).throw(Http(429)), attempts=2, sleep=lambda _: None))
    with pytest.raises(ValueError):  # not retryable
        list(llm.retry_stream(lambda: (_ for _ in ()).throw(ValueError("bad")), sleep=lambda _: None))


def test_adk_model_retries_429_before_any_response(monkeypatch):
    from google.adk.models.google_llm import Gemini

    attempts = []

    async def flaky(self, llm_request, stream=False):
        attempts.append(1)
        if len(attempts) == 1:
            raise Http(429)
        yield "response"

    async def no_wait(_):
        return None

    monkeypatch.setattr(Gemini, "generate_content_async", flaky)
    monkeypatch.setattr(llm.asyncio, "sleep", no_wait)
    model = llm.adk_model("gemini-2.5-pro")
    assert model.model == "gemini-2.5-pro" and model.retry_options.attempts == llm.ATTEMPTS

    async def collect():
        return [r async for r in model.generate_content_async(object(), stream=True)]

    assert asyncio.run(collect()) == ["response"] and len(attempts) == 2


def test_embeddings_stay_regional_when_gemini_goes_global(monkeypatch):
    from dataclasses import replace

    from core import config

    settings = replace(config.get_settings(), project="p", location="us-central1", gemini_location="global")
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    made = []
    monkeypatch.setattr(llm, "_client", lambda location: made.append(location) or location)
    assert llm.genai_client() == "global" and llm.regional_genai_client() == "us-central1"
