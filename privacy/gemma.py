"""Gemma contextual PII detector running inside the bank VPC.

Serves Gemma-2 9B-IT either from a Vertex AI endpoint (Model Garden, vLLM
container, private endpoint) or from GKE (vLLM OpenAI-compatible server on an
internal load balancer).  It only *finds* contextual PII strings; masking is
done by Cloud DLP so one reversible transform covers both passes.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from core.config import Settings, get_settings
from privacy.detectors import Finding, contextual_findings

log = logging.getLogger(__name__)
SYSTEM_PROMPT = (
    "You are a highly secure Data Privacy Officer AI. Your only task is to identify Personally Identifiable "
    "Information (PII) in the provided text: names, physical addresses, account numbers or fragments, phone "
    "numbers, and unofficial financial identifiers, including PII expressed indirectly (\"my wife Sarah\", "
    "\"account ending in four-two-seven\"). Return ONLY JSON: "
    '{"entities": [{"original_text": "exact text", "entity_type": "PERSON|ADDRESS|ACCOUNT|PHONE|OTHER"}]}'
)
MAX_CHARS = 6000


class GemmaRedactor:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def find(self, text: str) -> list[Finding]:
        if self.settings.gemma_backend != "gcp":
            return contextual_findings(text)
        try:
            raw = self._call_gke(text) if self.settings.gemma_gke_url else self._call_vertex(text)
            return _parse(raw, text)
        except Exception as error:  # Fail closed to the deterministic heuristic, never to "no redaction".
            log.warning("Gemma redactor unavailable, using contextual heuristic: %s", error)
            return contextual_findings(text)

    def _call_vertex(self, text: str) -> str:
        self.settings.require("gemma_vertex_endpoint")
        from google.cloud import aiplatform

        endpoint = aiplatform.Endpoint(self.settings.gemma_vertex_endpoint)
        prompt = f"<start_of_turn>user\n{SYSTEM_PROMPT}\n\nTEXT:\n{text[:MAX_CHARS]}<end_of_turn>\n<start_of_turn>model\n"
        dedicated = {"use_dedicated_endpoint": True} if getattr(endpoint, "dedicated_endpoint_enabled", False) else {}
        prediction = endpoint.predict(instances=[{"prompt": prompt, "max_tokens": 512, "temperature": 0.0}], **dedicated).predictions[0]
        return prediction if isinstance(prediction, str) else json.dumps(prediction)

    def _call_gke(self, text: str) -> str:
        response = httpx.post(
            f"{self.settings.gemma_gke_url.rstrip('/')}/v1/chat/completions",
            json={"model": "google/gemma-2-9b-it", "temperature": 0, "max_tokens": 512,
                  "messages": [{"role": "user", "content": f"{SYSTEM_PROMPT}\n\nTEXT:\n{text[:MAX_CHARS]}"}]},
            timeout=15.0,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


def _parse(raw: str, text: str) -> list[Finding]:
    # vLLM containers may echo the prompt; take the last JSON object.
    candidates = re.findall(r"\{.*\}", raw.split("<start_of_turn>model")[-1], re.DOTALL)
    if not candidates:
        return contextual_findings(text)
    entities = json.loads(candidates[-1]).get("entities", [])
    findings = []
    for entity in entities:
        original = str(entity.get("original_text", "")).strip()
        if original and original in text:  # never trust a span the model invented
            findings.append(Finding(original, f"{entity.get('entity_type', 'OTHER').upper()}_CONTEXT", "gemma"))
    return findings
