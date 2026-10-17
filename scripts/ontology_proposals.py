"""Review the ontology growth the LLM proposed: list, promote into a pack, or reject.

The canonicaliser never extends the vocabulary on its own. A capability it cannot place becomes a new ``Control`` node
flagged ``needs_review``. A reviewer decides what happens to it:

    python scripts/ontology_proposals.py                                   # list pending proposals, with rules and documents
    python scripts/ontology_proposals.py --promote control:foo             # dry run: preview the tenant-overlay entry
    python scripts/ontology_proposals.py --promote control:foo --apply     # write data/ontology/packs/tenant_<tenant>.json
    python scripts/ontology_proposals.py --promote control:foo --pack retail_lending --apply   # into an existing pack
    python scripts/ontology_proposals.py --reject control:foo --reason "duplicate of X" --apply

Promoting writes the capability into the pack JSON (a new tenant overlay starts at 1.0.0; an existing pack gets its patch
version bumped), clears ``needs_review`` on the graph node, and refuses unknown pack ids. Rejecting keeps the node for
audit and drops it from the canonicalisation candidates. Everything is a dry run unless ``--apply`` is given.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from knowledge_graph import governance  # noqa: E402
from knowledge_graph.store import GraphStore, get_graph_store  # noqa: E402


def main(argv: list[str] | None = None, store: GraphStore | None = None) -> int:
    parser = argparse.ArgumentParser(description="List, promote or reject LLM-proposed ontology controls (dry run by default).")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--promote", metavar="CONTROL_ID", help="Write the approved proposal into a pack")
    action.add_argument("--reject", metavar="CONTROL_ID", help="Mark the proposal rejected (kept for audit)")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--pack", metavar="PACK_ID", help="Existing pack to extend (must exist)")
    target.add_argument("--tenant", action="store_true", help="Tenant overlay data/ontology/packs/tenant_<tenant>.json (default)")
    parser.add_argument("--tenant-id", help="Tenant for the overlay (default: the graph store's tenant)")
    parser.add_argument("--process", metavar="PROCESS_ID", help="process:<slug> the new capability belongs to")
    parser.add_argument("--reason", default="", help="Why a proposal is rejected")
    parser.add_argument("--pack-dir", help="Pack directory (default: data/ontology/packs)")
    parser.add_argument("--apply", action="store_true", help="Write the pack file and update the graph")
    args = parser.parse_args(argv)

    store = store or get_graph_store()
    mode = "apply" if args.apply else "dry-run"
    try:
        if args.promote:
            result = governance.promote(store, args.promote, pack_id=args.pack, tenant_id=args.tenant_id,
                                        process=args.process, apply=args.apply, pack_dir=args.pack_dir)
        elif args.reject:
            result = governance.reject(store, args.reject, args.reason, apply=args.apply)
        else:
            result = {"pending": governance.pending_proposals(store)}
    except ValueError as error:  # GovernanceError and PackError
        print(json.dumps({"tenant_id": store.tenant_id, "error": str(error)}, indent=2), file=sys.stderr)
        return 2
    print(json.dumps({"tenant_id": store.tenant_id, "mode": mode, **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
