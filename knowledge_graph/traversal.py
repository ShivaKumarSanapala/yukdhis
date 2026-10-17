"""GraphRAG blast-radius traversal.

Walks Rule -> Product/Process/Segment/Control -> Process/Control/Team at a
given business date.  Output is topology only: it defines *where* to look and
never touches customer records.
"""

from __future__ import annotations

from datetime import date

from core.models import BlastRadius
from knowledge_graph.store import GraphStore


def blast_radius(store: GraphStore, rule_id: str, as_of: str | date | None = None) -> BlastRadius:
    as_of = (as_of.isoformat() if isinstance(as_of, date) else as_of) or date.today().isoformat()
    store.query_log.clear()
    hop1 = store.neighbours([rule_id], ["GOVERNS", "APPLIES_TO", "TARGETS", "REQUIRES"], as_of)
    by_type: dict[str, list[dict]] = {}
    for edge in hop1:
        by_type.setdefault(edge["edge_type"], []).append(edge["node"])
    rule_node = store.node(rule_id)
    rule_attrs = (rule_node or {}).get("attributes", {})
    scope_status = rule_attrs.get("scope_status", "RESOLVED")
    # An UNRESOLVED rule governs nothing, even if an earlier run left GOVERNS edges behind.
    products = [] if scope_status == "UNRESOLVED" else by_type.get("GOVERNS", [])
    processes = {p["id"]: p for p in by_type.get("APPLIES_TO", [])}
    required = sorted(c["id"] for c in by_type.get("REQUIRES", []))
    product_ids = [p["id"] for p in products]

    hop2 = store.neighbours(product_ids, ["USES", "PROVIDES", "OWNED_BY"], as_of)
    provided: dict[str, set[str]] = {pid: set() for pid in product_ids}
    owners: dict[str, dict] = {}
    paths: list[list[str]] = []
    for edge in hop2:
        src, target = edge["source_id"], edge["node"]
        if edge["edge_type"] == "PROVIDES":
            provided[src].add(target["id"])
        elif edge["edge_type"] == "OWNED_BY":
            owners[src] = {"owner_of": src, "team_id": target["id"], "team": target["name"]}
        elif edge["edge_type"] == "USES" and target["id"] in processes:
            paths.append([rule_id, "GOVERNS", src, "USES", target["id"]])
    for edge in store.neighbours(list(processes), ["OWNED_BY"], as_of):
        owners[edge["source_id"]] = {"owner_of": edge["source_id"], "team_id": edge["node"]["id"], "team": edge["node"]["name"]}

    gaps = {pid: [c for c in required if c not in provided[pid]] for pid in product_ids}
    for pid, missing in gaps.items():
        paths += [[rule_id, "REQUIRES", control, "NOT_PROVIDED_BY", pid] for control in missing]
    return BlastRadius(
        rule_id=rule_id, as_of=as_of,
        products=[{"id": p["id"], "name": p["name"], "owner": owners.get(p["id"], {}).get("team"),
                   **{k: p["attributes"].get(k) for k in ("rate_type", "secured", "category")}} for p in products],
        processes=[{"id": p["id"], "name": p["name"], "bian_domain": p["attributes"].get("bian_domain"),
                    "owner": owners.get(p["id"], {}).get("team")} for p in processes.values()],
        controls_required=required,
        control_gaps={pid: missing for pid, missing in gaps.items() if missing},
        owners=sorted(owners.values(), key=lambda o: o["owner_of"]),
        segments=[{"id": s["id"], "name": s["name"]} for s in by_type.get("TARGETS", [])],
        scope_status=scope_status, scope_notes=list(rule_attrs.get("scope_notes", [])),
        paths=paths, query_language=store.query_language, queries=list(store.query_log),
    )
