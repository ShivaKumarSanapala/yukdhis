"""Propose (and optionally apply) merges of near-duplicate Control nodes in the graph store (L7).

Controls created before canonicalisation, or by wording the canonicaliser could not place, can mean the same thing
(``control:maintain-ferr`` and ``control:foreign-exchange-risk-reserve-maintenance``). For each non-ontology
control this asks the canonicaliser, against every other live control, whether it is a duplicate: the same ladder
as mapping (exact or synonym, then Gemini with guards, or a strict lexical or embedding match with no LLM).

Dry run by default: prints the proposals and writes nothing. ``--apply`` merges each one bitemporally:

* every current ``REQUIRES`` (and ``PROVIDES``) edge into the duplicate is re-written with ``valid_to = today``
  (the old row is superseded in system time, never deleted), and a new edge to the canonical control starts
  ``valid_from = today`` (or the old edge's own start, if that is later);
* the duplicate node gets ``merged_into`` and its names become synonyms of the canonical control, so later runs
  resolve the old wording straight to the canonical control.

    python scripts/dedupe_controls.py            # dry run against the configured graph store
    python scripts/dedupe_controls.py --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from knowledge_graph.canonicaliser import ControlCanonicaliser, record_synonym  # noqa: E402
from knowledge_graph.ontology import CAPABILITIES  # noqa: E402
from knowledge_graph.store import GraphStore, get_graph_store  # noqa: E402

MOVED_EDGES = ("REQUIRES", "PROVIDES")
FAR_FUTURE = "9999-12-30"  # also catches edges that start after today (rules not yet effective)


@dataclass
class Proposal:
    duplicate: str
    canonical: str
    method: str
    confidence: float
    rationale: str
    edges: int


def _incoming(store: GraphStore, control_id: str, as_of: str) -> list[dict]:
    seen, edges = set(), []
    for when in (as_of, FAR_FUTURE):
        for edge in store.neighbours([control_id], list(MOVED_EDGES), when, "in"):
            key = (edge["source_id"], edge["edge_type"], edge["valid_from"])
            if key not in seen:
                seen.add(key)
                edges.append(edge)
    return edges


def propose_merges(store: GraphStore, canonicaliser: ControlCanonicaliser | None = None,
                   as_of: str | None = None) -> list[Proposal]:
    """Read-only: no graph writes (the canonicaliser's ``decide`` never writes)."""
    as_of = as_of or date.today().isoformat()
    canonicaliser = canonicaliser or ControlCanonicaliser(store)
    ontology = set(CAPABILITIES.values())
    controls = {n["id"]: n for n in store.nodes("Control") if not n["attributes"].get("merged_into")}
    merged: set[str] = set()
    canonicals: set[str] = set()
    proposals: list[Proposal] = []
    for control_id in sorted(controls):
        if control_id in ontology or control_id in merged or control_id in canonicals:
            continue
        node = controls[control_id]
        attrs = node["attributes"]
        term = attrs.get("capability") or control_id.removeprefix("control:").replace("-", "_")
        description = " ".join(p for p in (node["name"], attrs.get("description", "")) if p)
        decision = canonicaliser.decide(term, description, exclude={control_id, *merged})
        if decision.decision == "NEW" or decision.control_id == control_id or decision.control_id not in controls:
            continue
        duplicate, canonical = _orient(store, control_id, decision.control_id, ontology, as_of)
        proposals.append(Proposal(duplicate, canonical, decision.method, decision.confidence, decision.rationale,
                                  len(_incoming(store, duplicate, as_of))))
        merged.add(duplicate)
        canonicals.add(canonical)
    return proposals


def _orient(store: GraphStore, a: str, b: str, ontology: set[str], as_of: str) -> tuple[str, str]:
    """(duplicate, canonical): an ontology control wins, then the one more rules require, then the earlier id."""
    if a in ontology or b in ontology:
        return (b, a) if a in ontology else (a, b)
    used_a, used_b = len(_incoming(store, a, as_of)), len(_incoming(store, b, as_of))
    if used_a != used_b:
        return (b, a) if used_a > used_b else (a, b)
    return (max(a, b), min(a, b))


def apply_merges(store: GraphStore, proposals: list[Proposal], as_of: str | None = None) -> dict:
    """Bitemporal merge: end the duplicate's edges (old rows superseded, kept) and open edges to the canonical control."""
    today = as_of or date.today().isoformat()
    moved = 0
    for proposal in proposals:
        dup, canonical = proposal.duplicate, proposal.canonical
        for edge in _incoming(store, dup, today):
            source, edge_type, attrs = edge["source_id"], edge["edge_type"], dict(edge["edge_attributes"])
            start = edge["valid_from"]
            ends = max(today, start)  # an edge that starts after today ends where it starts: it never applied
            store.upsert_edge(source, dup, edge_type, start, ends, {**attrs, "merged_into": canonical})
            existing = {e["source_id"] for e in _incoming(store, canonical, today) if e["edge_type"] == edge_type}
            if source not in existing:
                store.upsert_edge(source, canonical, edge_type, ends, edge["valid_to"], {**attrs, "merged_from": dup})
            moved += 1
        dup_node, canonical_node = store.node(dup), store.node(canonical)
        if dup_node is None or canonical_node is None:
            continue
        for term in {dup_node["name"], dup_node["attributes"].get("capability") or "", dup.removeprefix("control:"),
                     *dup_node["attributes"].get("synonyms", [])}:
            if term:
                record_synonym(store, canonical_node, term, f"merge:{proposal.method}")
                canonical_node = store.node(canonical) or canonical_node
        store.upsert_node(dup, "Control", dup_node["name"], {**dup_node["attributes"], "merged_into": canonical,
                                                              "merged_on": today})
    return {"merged": len(proposals), "edges_moved": moved, "as_of": today}


def main(argv: list[str] | None = None, store: GraphStore | None = None,
         canonicaliser: ControlCanonicaliser | None = None) -> int:
    parser = argparse.ArgumentParser(description="Propose merges of near-duplicate Control nodes (dry run by default).")
    parser.add_argument("--apply", action="store_true", help="Apply the proposed merges bitemporally")
    parser.add_argument("--as-of", help="Business date for the merge (default: today)")
    parser.add_argument("--no-llm", action="store_true", help="Lexical and embedding matching only")
    args = parser.parse_args(argv)
    store = store or get_graph_store()
    canonicaliser = canonicaliser or ControlCanonicaliser(store, use_llm=False if args.no_llm else None)
    proposals = propose_merges(store, canonicaliser, args.as_of)
    print(json.dumps({"tenant_id": store.tenant_id, "mode": "apply" if args.apply else "dry-run",
                      "proposals": [asdict(p) for p in proposals]}, indent=2))
    if args.apply and proposals:
        print(json.dumps(apply_merges(store, proposals, args.as_of), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
