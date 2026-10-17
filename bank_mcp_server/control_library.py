"""Read access to the bank's internal policy and control library (``data/bank/controls.json``, ``policies.json``).

Answers the question the eval suite and, later, the report layer ask of a regulation: "which of the bank's controls
satisfy this instrument, and which of them are absent, planned or only partial on the analysis date?". For an instrument
that is not yet in force the answer is a *readiness gap*, not a compliance failure.

The library is data about the bank's own controls, not customer data; it never reads the operational database.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

from core.config import DATA_DIR

BANK_DIR = DATA_DIR / "bank"
READY = "implemented"


@lru_cache(maxsize=4)
def _load(directory: str) -> tuple[list[dict], dict, list[dict]]:
    base = Path(directory)
    controls = json.loads((base / "controls.json").read_text())["controls"]
    policies = json.loads((base / "policies.json").read_text())
    return controls, policies["instruments"], policies["policies"]


def controls(directory: Path | None = None) -> list[dict]:
    return _load(str(directory or BANK_DIR))[0]


def instruments(directory: Path | None = None) -> dict[str, dict]:
    return _load(str(directory or BANK_DIR))[1]


def policies(directory: Path | None = None) -> list[dict]:
    return _load(str(directory or BANK_DIR))[2]


def resolve_instrument(ref: str, directory: Path | None = None) -> str | None:
    """Instrument key for a library key, an RBI reference ("RBI/DOR/2026-27/398") or a corpus file name."""
    table = instruments(directory)
    if ref in table:
        return ref
    lowered = ref.lower()
    for key, spec in table.items():
        if lowered in (spec["reference"].lower(), spec.get("corpus_file", "").lower()):
            return key
    return None


def status_on(control: dict, as_of: str) -> str:
    """Implementation status of a control on a date: a control that went live after ``as_of`` was not yet there."""
    status = control["implementation_status"]
    start = control.get("implemented_from")
    if status in ("implemented", "partial") and start and as_of < start:
        return "absent"
    return status


def controls_for_instrument(instrument: str, as_of: str, directory: Path | None = None) -> list[dict]:
    """Every control that satisfies at least one paragraph of the instrument, with its status on ``as_of``."""
    key = resolve_instrument(instrument, directory)
    if key is None:
        return []
    found = []
    for control in controls(directory):
        paragraphs = [s["paragraph"] for s in control["satisfies"] if s["instrument"] == key]
        if paragraphs:
            found.append({"control_id": control["id"], "name": control["name"], "status": status_on(control, as_of),
                          "owner_team": control["owner_team"], "paragraphs": paragraphs,
                          "planned_go_live": control.get("planned_go_live"), "last_tested": control.get("last_tested")})
    return sorted(found, key=lambda c: c["control_id"])


def readiness(instrument: str, as_of: str, directory: Path | None = None) -> dict:
    """Controls behind an instrument and the ones not ready on ``as_of``.

    ``phase`` is ``upcoming`` when the instrument takes effect after ``as_of`` (the gaps are readiness gaps) and ``in_force``
    otherwise (the gaps are control gaps)."""
    key = resolve_instrument(instrument, directory)
    if key is None:
        raise KeyError(instrument)
    spec = instruments(directory)[key]
    items = controls_for_instrument(key, as_of, directory)
    gaps = [c for c in items if c["status"] != READY]
    phase = "upcoming" if spec["effective_date"] > as_of else "in_force"
    return {"instrument": key, "reference": spec["reference"], "effective_date": spec["effective_date"], "phase": phase,
            "controls": items, "ready": [c["name"] for c in items if c["status"] == READY],
            "gaps": [{"control": c["name"], "status": c["status"], "planned_go_live": c["planned_go_live"], "paragraphs": c["paragraphs"]}
                     for c in gaps]}


def policy_for_control(control_id: str, directory: Path | None = None) -> list[str]:
    """Internal policies that enforce a control."""
    return sorted(p["policy_id"] for p in policies(directory) for c in p["clauses"] if control_id in c["controls"])


def days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days
