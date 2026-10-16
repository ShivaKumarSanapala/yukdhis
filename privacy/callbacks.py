"""ADK ``before_model_callback`` that masks PII in every request to Gemini.

Attached to every LlmAgent, so user chat text, uploaded documents and tool
results are all sanitized at the last hop before leaving the VPC.
"""

from __future__ import annotations

import logging
from typing import Any

from privacy.sanitizer import get_sanitizer

log = logging.getLogger(__name__)
STATE_KEY = "privacy_redactions"


def mask_pii_before_model(callback_context: Any, llm_request: Any):
    sanitizer = get_sanitizer()
    session = getattr(callback_context, "session", None)
    session_id = getattr(session, "id", None) or getattr(callback_context, "invocation_id", "default")
    redacted = 0
    for content in llm_request.contents or []:
        for part in content.parts or []:
            if getattr(part, "text", None):
                result = sanitizer.sanitize(part.text, session_id)
                redacted += result.redacted
                part.text = result.text
            response = getattr(part, "function_response", None)
            if response is not None and response.response:
                cleaned = sanitizer.sanitize_value(response.response, session_id)
                if cleaned != response.response:
                    redacted += 1
                    response.response = cleaned
    if redacted:
        try:
            callback_context.state[STATE_KEY] = int(callback_context.state.get(STATE_KEY, 0)) + redacted
        except Exception:  # state is best-effort telemetry here
            log.debug("Could not record redaction count", exc_info=True)
    return None
