"""Deterministic pipeline runner (no LLM).

Used by tests, ``scripts/run_demo.py``, and the API when ``mode=deterministic``
so the demo always works even without Vertex AI quota.  Stage order mirrors the
ADK SequentialAgent exactly.
"""

from __future__ import annotations

from typing import Callable

from pipeline import stages

STAGES: list[tuple[str, str, Callable]] = [
    ("reader", "Parse document (Document AI layout parser)", stages.stage_parse),
    ("reader", "Extract & validate rules (maker-checker)", stages.stage_extract),
    ("mapper", "Resolve entities and upsert bitemporal graph edges", stages.stage_map),
    ("graphrag", "Traverse blast radius in Spanner Graph", stages.stage_blast_radius),
    ("precedent", "Recall prior findings and open actions", stages.stage_precedents),
    ("data_analyst", "Profile scoped portfolio via guarded SQL", stages.stage_profile),
    ("evaluator", "Evaluate checks against operational data", stages.stage_evaluate),
]


def run_pipeline(source: dict, as_of: str | None = None, emit: Callable[[str, str, dict], None] | None = None) -> dict:
    state: dict = {"source": source}
    if as_of:
        state["as_of"] = as_of
    emit = emit or (lambda *_: None)
    for agent, label, stage in STAGES:
        emit(agent, "stage_started", {"label": label})
        output = stage(state)
        emit(agent, "stage_completed", {"label": label, "output": output})
    emit("orchestrator", "stage_started", {"label": "Compile impact report and remediation plan"})
    output = stages.stage_finalize(state, narrative="", mode="deterministic")
    emit("orchestrator", "stage_completed", {"label": "Compile impact report and remediation plan", "output": output})
    return state
