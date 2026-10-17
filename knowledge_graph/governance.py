"""Governed ontology growth: the LLM proposes, a reviewer promotes.

The canonicaliser never extends the vocabulary silently. A capability it cannot match to an existing control becomes a
*new* ``Control`` node flagged ``needs_review``. This module lists those proposals with the rules and documents that
produced them, and turns an approved one into an entry of an ontology pack (a bank's tenant overlay by default), or marks
it rejected. Rejected controls stay in the graph for audit but are not canonicalisation candidates.

Everything here is read-only unless ``apply=True``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from knowledge_graph import packs
from knowledge_graph.ontology import get_ontology
from knowledge_graph.store import GraphStore

LATEST = "9999-12-30"  # "now and always": reads every open-ended edge regardless of its valid_from


class GovernanceError(ValueError):
    """The requested promotion or rejection is not allowed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _capability_id(node: dict) -> str:
    raw = node["attributes"].get("capability") or node["id"].removeprefix("control:")
    slug = re.sub(r"[^a-z0-9]+", "_", str(raw).lower()).strip("_")
    return slug if slug and slug[0].isalpha() else f"c_{slug}"


# proposals ------------------------------------------------------------------------------------------------------
def _is_pending(node: dict) -> bool:
    attrs = node["attributes"]
    return bool(attrs.get("needs_review")) and not attrs.get("rejected") and not attrs.get("merged_into")


def _sources(store: GraphStore, control_id: str) -> list[dict]:
    """Rules (and their policy and document) that require the control."""
    sources = []
    for edge in store.neighbours([control_id], ["REQUIRES"], LATEST, "in"):
        rule = edge["node"]
        policies = store.neighbours([rule["id"]], ["CONTAINS"], LATEST, "in")
        policy = policies[0]["node"] if policies else None
        sources.append({
            "rule_id": rule["id"], "rule_title": rule["name"],
            "policy_id": policy["id"] if policy else None, "policy_title": policy["name"] if policy else None,
            "document_id": (edge.get("edge_attributes") or {}).get("document_id")
            or rule["attributes"].get("citation", {}).get("document_id"),
        })
    return sorted(sources, key=lambda s: s["rule_id"])


def pending_proposals(store: GraphStore, detail: bool = True) -> list[dict]:
    out = []
    for node in store.nodes("Control"):
        if not _is_pending(node):
            continue
        attrs = node["attributes"]
        entry = {
            "id": node["id"], "name": node["name"], "capability": attrs.get("capability"),
            "first_seen": attrs.get("first_seen"), "source_rule": attrs.get("source_rule"),
            "method": attrs.get("canonicalisation"), "description": attrs.get("description", ""),
        }
        if detail:
            rules = _sources(store, node["id"])
            entry["rules"] = rules
            entry["documents"] = sorted({r["document_id"] for r in rules if r["document_id"]})
            entry["source_rule"] = entry["source_rule"] or (rules[0]["rule_id"] if rules else None)
        out.append(entry)
    return sorted(out, key=lambda e: (e["first_seen"] or "", e["id"]))


# overview -------------------------------------------------------------------------------------------------------
def describe_ontology(store: GraphStore | None = None, profile: dict | None = None,
                      pack_dir: str | Path | None = None) -> dict:
    """Enabled packs with versions and counts, the merged vocabulary summary and the pending proposals."""
    profile = packs.load_profile() if profile is None else profile
    tenants = (store.tenant_id,) if store is not None else ()
    enabled = packs.enabled_packs(profile, pack_dir, tenants)
    ids = {p.pack_id for p in enabled}
    ontology = get_ontology(profile, pack_dir, tenants)
    return {
        "packs": [{"pack_id": p.pack_id, "version": p.version, "title": p.title, "domain": p.domain,
                   "business_line": p.business_line, "tenant_overlay": p.is_tenant_overlay, "counts": p.counts()}
                  for p in enabled],
        "available_packs": sorted(set(packs.load_all_packs(pack_dir)) - ids),
        "vocabulary": ontology.summary(),
        "counts": {"capabilities": len(ontology.capabilities), "record_flags": len(ontology.record_flags),
                   "event_types": len(ontology.event_types), "views": len(ontology.sanitized_views),
                   "workflows": len(ontology.workflows), "glossary_terms": len(ontology.glossary)},
        "pending_proposals": pending_proposals(store, detail=False) if store is not None else [],
    }


# promote / reject -----------------------------------------------------------------------------------------------
def _bump_patch(version: str) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    return f"{major}.{minor}.{patch + 1}"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w") as out:
            json.dump(data, out, indent=2, ensure_ascii=False)
            out.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _proposal(store: GraphStore, control_id: str) -> dict:
    node = store.node(control_id)
    if node is None or node["type"] != "Control":
        raise GovernanceError(f"{control_id}: no such Control node in the graph")
    attrs = node["attributes"]
    if attrs.get("rejected"):
        raise GovernanceError(f"{control_id}: was rejected; it is kept for audit and cannot be promoted")
    if not attrs.get("needs_review"):
        raise GovernanceError(f"{control_id}: is not awaiting review (needs_review is not set)")
    return node


def promote(store: GraphStore, control_id: str, pack_id: str | None = None, tenant_id: str | None = None,
            process: str | None = None, apply: bool = False, pack_dir: str | Path | None = None) -> dict:
    """Write an approved proposal into an ontology pack and clear ``needs_review``.

    ``pack_id`` must name an existing pack; without it the proposal goes to the tenant overlay
    (``tenant_<tenant_id>.json``), which is created when missing. Dry-run unless ``apply``.
    """
    directory = Path(pack_dir or packs.PACK_DIR)
    node = _proposal(store, control_id)
    available = packs.load_all_packs(directory)
    if pack_id is not None:
        if pack_id not in available:
            raise GovernanceError(f"unknown pack id {pack_id!r}; available: {sorted(available)}")
        target = available[pack_id]
        path = directory / f"{pack_id}.json"
        data = json.loads(path.read_text())
        created = False
    else:
        tenant_id = tenant_id or getattr(store, "tenant_id", None)
        if not tenant_id:
            raise GovernanceError("no tenant id: pass --pack or --tenant-id")
        pack_id = packs.tenant_pack_id(tenant_id)
        path = packs.tenant_pack_path(tenant_id, directory)
        created = pack_id not in available
        data = ({"pack_id": pack_id, "version": "1.0.0", "title": f"Tenant overlay for {tenant_id}", "domain": "tenant_overlay",
                 "capabilities": {}} if created else json.loads(path.read_text()))
    if process is not None:
        packs.CapabilitySpec(control=control_id, process=process)  # validates the process id
    attrs = node["attributes"]
    capability = _capability_id(node)
    entry = {"control": control_id, "description": attrs.get("description", "") or "",
             "process": process, "synonyms": sorted(attrs.get("synonyms", []))}
    existing = data.setdefault("capabilities", {}).get(capability)
    if existing is not None and existing != entry:
        raise GovernanceError(f"pack {pack_id!r} already defines capability {capability!r} differently")
    data["capabilities"][capability] = entry
    if not created and existing is None:
        data["version"] = _bump_patch(data["version"])
    new_pack = packs.parse_pack(data, path.name)  # schema errors surface before anything is written
    # The whole enabled set must still merge: a duplicate capability or control in another pack is a conflict.
    others = [p for p in available.values() if p.pack_id != pack_id and not p.is_tenant_overlay]  # tenants are isolated
    packs.merge_packs([*others, new_pack])

    plan = {"control_id": control_id, "capability": capability, "pack_id": pack_id, "pack_file": str(path),
            "pack_created": created, "version": new_pack.version, "entry": entry, "applied": False,
            "graph_update": {"needs_review": False, "promoted_to": pack_id}}
    if not apply:
        return plan
    _write_json(path, data)
    packs.clear_cache()
    updated = {k: v for k, v in attrs.items() if k != "needs_review"}
    updated.update({"needs_review": False, "promoted_to": pack_id, "promoted_at": _now()})
    store.upsert_node(node["id"], node["type"], node["name"], updated)
    plan["applied"] = True
    return plan


def reject(store: GraphStore, control_id: str, reason: str = "", apply: bool = False) -> dict:
    """Mark a proposal rejected: kept for audit, excluded from canonicalisation candidates and from the pending list."""
    node = _proposal(store, control_id)
    plan = {"control_id": control_id, "action": "reject", "reason": reason, "applied": False}
    if not apply:
        return plan
    updated = {k: v for k, v in node["attributes"].items() if k != "needs_review"}
    updated.update({"needs_review": False, "rejected": True, "rejected_at": _now(), "rejection_reason": reason})
    store.upsert_node(node["id"], node["type"], node["name"], updated)
    plan["applied"] = True
    return plan

