"""Deterministic pipeline runner (no LLM).

Used by tests, ``scripts/run_demo.py``, and the API when ``mode=deterministic``
so the demo always works even without Vertex AI quota.  Stage order mirrors the
ADK SequentialAgent exactly.

Human review: ``stop_after="extract"`` returns right after rule extraction, and
``preset={"extraction": ..., ...}`` resumes with reviewer-approved rules (parse
and extract are skipped).  ``emit`` may raise to cancel the run between events.
"""

from __future__ import annotations

import contextlib
import threading
from typing import Callable

from pipeline import stages

# (key, agent, label, stage)
STAGES: list[tuple[str, str, str, Callable]] = [
    ("parse", "reader", "Parse document (Document AI layout parser)", stages.stage_parse),
    ("extract", "reader", "Extract & validate rules (maker-checker)", stages.stage_extract),
    ("map", "mapper", "Resolve entities and upsert bitemporal graph edges", stages.stage_map),
    ("blast_radius", "graphrag", "Traverse blast radius in Spanner Graph", stages.stage_blast_radius),
    ("precedents", "precedent", "Recall prior findings and open actions", stages.stage_precedents),
    ("profile", "data_analyst", "Profile scoped portfolio via guarded SQL", stages.stage_profile),
    ("evaluate", "evaluator", "Evaluate checks against operational data", stages.stage_evaluate),
]
STAGE_KEYS = [key for key, *_ in STAGES]
EXTRACTION_KEYS = ("document", "extraction", "validation_issues", "extraction_provenance", "analytical_objectives", "full_text_context")
# The blast-radius traversal clears and then reads the graph store's process-wide ``query_log``; parallel runs (and
# the agentic tool / graph endpoint) hold this lock around it so one run's queries never leak into another's report.
GRAPH_TRAVERSAL_LOCK = threading.Lock()


def run_pipeline(source: dict, as_of: str | None = None, emit: Callable[[str, str, dict], None] | None = None, *,
                 preset: dict | None = None, stop_after: str | None = None) -> dict:
    if stop_after is not None and stop_after not in STAGE_KEYS:
        raise ValueError(f"stop_after must be one of {STAGE_KEYS}")
    emit = emit or (lambda *_: None)
    sink = getattr(stages, "event_sink", None)  # lets stages emit their own events (document_classified, applicability)
    with sink(emit) if sink else contextlib.nullcontext():
        return _run(source, as_of, emit, preset, stop_after)


def _run(source: dict, as_of: str | None, emit: Callable[[str, str, dict], None], preset: dict | None,
         stop_after: str | None) -> dict:
    state: dict = {"source": source, **(preset or {})}
    if as_of:
        state["as_of"] = as_of
    supplied = "extraction" in (preset or {})
    for key, agent, label, stage in STAGES:
        if supplied and key in ("parse", "extract"):
            emit(agent, "stage_skipped", {"label": label, "reason": "Rules were supplied after human review"})
            continue
        emit(agent, "stage_started", {"label": label})
        if key == "blast_radius":
            with GRAPH_TRAVERSAL_LOCK:
                output = stage(state)
        else:
            output = stage(state)
        emit(agent, "stage_completed", {"label": label, "output": output})
        if key == stop_after:
            return state
    emit("orchestrator", "stage_started", {"label": "Compile impact report and remediation plan"})
    output = stages.stage_finalize(state, narrative="", mode="deterministic")
    emit("orchestrator", "stage_completed", {"label": "Compile impact report and remediation plan", "output": output})
    return state
