"""Two-pass reversible PII sanitization before any text reaches Gemini.

local: regex pass + contextual heuristic, ``{{TYPE_N}}`` tokens in the
       in-memory encrypted vault.
gcp:   Gemma (VPC) finds contextual strings -> Cloud DLP de-identifies them
       together with built-in infoTypes using a crypto-deterministic,
       KMS-wrapped key.  Re-identification is a DLP call, so no mapping is
       stored and any Cloud Run instance can restore tokens.
"""

from __future__ import annotations

import base64
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from core.config import Settings, get_settings
from privacy.detectors import contextual_findings, structured_findings
from privacy.gemma import GemmaRedactor
from privacy.vault import VAULT

log = logging.getLogger(__name__)
DLP_INFO_TYPES = [
    "PERSON_NAME", "EMAIL_ADDRESS", "PHONE_NUMBER", "STREET_ADDRESS", "CREDIT_CARD_NUMBER", "IBAN_CODE",
    "FINANCIAL_ACCOUNT_NUMBER", "INDIA_PAN_INDIVIDUAL", "INDIA_AADHAAR_INDIVIDUAL",
]
SURROGATE = "YUKDHIS_TOKEN"


@dataclass
class SanitizedText:
    text: str
    entity_counts: dict[str, int] = field(default_factory=dict)
    engine: str = "local"

    @property
    def redacted(self) -> int:
        return sum(self.entity_counts.values())


class Sanitizer:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.gemma = GemmaRedactor(self.settings)

    def sanitize(self, text: str, session_id: str, local: bool = False) -> SanitizedText:
        if not text or not text.strip():
            return SanitizedText(text=text)
        if self.settings.privacy_backend == "gcp" and not local:
            try:
                return self._dlp_deidentify(text)
            except Exception as error:  # fail closed to local masking, never to unmasked text
                log.warning("Cloud DLP unavailable, masking locally: %s", error)
                return self._local(text, session_id, structured_findings(text) + contextual_findings(text), "local-fallback")
        return self._local(text, session_id, structured_findings(text) + (self.gemma.find(text) if not local else []),
                           "local-regex" if local else "local-regex+heuristic")

    def _local(self, text: str, session_id: str, findings: list, engine: str) -> SanitizedText:
        counts: Counter[str] = Counter()
        # Longest first so "Rahul Sharma" is replaced before "Rahul".
        for finding in sorted({f.text: f for f in findings}.values(), key=lambda f: -len(f.text)):
            if finding.text in text:
                text = text.replace(finding.text, VAULT.tokenize(session_id, finding.text, finding.entity_type))
                counts[finding.entity_type] += 1
        return SanitizedText(text=text, entity_counts=dict(counts), engine=engine)

    def restore(self, text: str, session_id: str) -> str:
        """Swap tokens back - only ever called inside the action engine."""
        if self.settings.privacy_backend == "gcp":
            return self._dlp_reidentify(text)
        return VAULT.detokenize(session_id, text)

    def sanitize_value(self, value: Any, session_id: str) -> Any:
        """Structured tool output: produced PII-free by the secure engine, so a local
        regex scrub is the defence-in-depth layer (no per-leaf DLP calls)."""
        if isinstance(value, str):
            return self.sanitize(value, session_id, local=True).text
        if isinstance(value, dict):
            return {k: self.sanitize_value(v, session_id) for k, v in value.items()}
        if isinstance(value, list):
            return [self.sanitize_value(v, session_id) for v in value]
        return value

    # --- Cloud DLP -------------------------------------------------------
    def _transform(self) -> dict:
        self.settings.require("project", "dlp_kms_key", "dlp_wrapped_key_b64")
        return {"info_type_transformations": {"transformations": [{"primitive_transformation": {
            "crypto_deterministic_config": {
                "crypto_key": {"kms_wrapped": {"wrapped_key": base64.b64decode(self.settings.dlp_wrapped_key_b64),
                                               "crypto_key_name": self.settings.dlp_kms_key}},
                "surrogate_info_type": {"name": SURROGATE},
            }}}]}}

    def _dlp_deidentify(self, text: str) -> SanitizedText:
        from google.cloud import dlp_v2

        contextual = sorted({f.text for f in self.gemma.find(text)})
        inspect = {"info_types": [{"name": n} for n in DLP_INFO_TYPES], "min_likelihood": dlp_v2.Likelihood.POSSIBLE}
        if contextual:
            inspect["custom_info_types"] = [{"info_type": {"name": "CONTEXTUAL_PII"}, "dictionary": {"word_list": {"words": contextual}}}]
        response = _dlp().deidentify_content(request={
            "parent": f"projects/{self.settings.project}/locations/global",
            "deidentify_config": self._transform(), "inspect_config": inspect, "item": {"value": text},
        })
        counts = {s.info_type.name: sum(r.count for r in s.results) for s in response.overview.transformation_summaries}
        return SanitizedText(text=response.item.value, entity_counts=counts, engine="cloud-dlp+gemma")

    def _dlp_reidentify(self, text: str) -> str:
        response = _dlp().reidentify_content(request={
            "parent": f"projects/{self.settings.project}/locations/global",
            "reidentify_config": self._transform(),
            "inspect_config": {"custom_info_types": [{"info_type": {"name": SURROGATE}, "surrogate_type": {}}]},
            "item": {"value": text},
        })
        return response.item.value


_DLP_CLIENT = None


def _dlp():
    global _DLP_CLIENT
    if _DLP_CLIENT is None:
        from google.cloud import dlp_v2

        _DLP_CLIENT = dlp_v2.DlpServiceClient()
    return _DLP_CLIENT


_SANITIZER: Sanitizer | None = None


def get_sanitizer() -> Sanitizer:
    global _SANITIZER
    if _SANITIZER is None:
        _SANITIZER = Sanitizer()
    return _SANITIZER


def reset_sanitizer() -> None:
    global _SANITIZER
    _SANITIZER = None
