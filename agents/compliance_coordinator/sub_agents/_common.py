"""Shared settings for every pipeline LlmAgent."""

from __future__ import annotations

from google.genai import types

from core.config import get_settings
from privacy.callbacks import mask_pii_before_model

GUARDRAILS = (
    "\n\nNon-negotiable rules: numbers come only from tool results - never estimate or invent counts, products, "
    "edges or penalties. You never see customer PII and must never ask for it. You do not execute changes to bank "
    "systems; you only propose actions for human approval. If a tool returns status=error, report it plainly."
)


def static(text: str):
    """InstructionProvider: bypasses ADK {state} templating so literal braces are safe."""
    return lambda _context: text + GUARDRAILS


def llm_options(reasoning: bool = False) -> dict:
    settings = get_settings()
    return {
        "model": settings.reasoning_model if reasoning else settings.fast_model,
        "generate_content_config": types.GenerateContentConfig(temperature=0.1, max_output_tokens=4096),
        "before_model_callback": mask_pii_before_model,
    }
