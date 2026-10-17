"""Mapper: wire extracted rules into the BIAN knowledge graph.

For each rule it resolves product/process/segment wording to canonical nodes,
then upserts bitemporal ``CONTAINS``/``GOVERNS``/``APPLIES_TO``/``TARGETS``/
``REQUIRES`` edges.  Which products a rule governs is decided by explicit
attribute filters and scored entity resolution, never by free LLM inference.
"""

from __future__ import annotations

from core.models import EntityMatch, Policy, Rule, RuleMapping
from knowledge_graph.entity_resolution import EntityResolver, lexical_score
from knowledge_graph.ontology import (
    CAPABILITIES, CAPABILITY_PROCESSES, EVENT_PROCESSES, PRODUCT_ATTRIBUTES, REGULATORY_GLOSSARY,
)
from knowledge_graph.store import GraphStore

GLOSSARY_THRESHOLD = 0.9


class PolicyMapper:
    def __init__(self, store: GraphStore, resolver: EntityResolver | None = None) -> None:
        self.store = store
        self.resolver = resolver or EntityResolver()

    def upsert_policy(self, policy: Policy) -> list[dict]:
        written = []
        self.store.upsert_node(policy.policy_id, "Policy", policy.title, {
            "issuer": policy.issuer, "reference": policy.reference, "issued_on": policy.issued_on,
            "effective_date": policy.effective_date, "source_uri": policy.source_uri,
        })
        for old in policy.supersedes:
            self.store.upsert_edge(policy.policy_id, old, "SUPERSEDES", policy.effective_date)
            written.append({"source": policy.policy_id, "type": "SUPERSEDES", "target": old})
            for contained in self.store.neighbours([old], ["CONTAINS"], policy.effective_date):
                for edge_type in ("GOVERNS", "APPLIES_TO", "TARGETS", "REQUIRES"):
                    self.store.end_edges(contained["target_id"], edge_type, policy.effective_date)
        return written

    def map_rule(self, policy: Policy, rule: Rule) -> RuleMapping:
        products = self.store.nodes("Product")
        resolutions: list[EntityMatch] = []
        filters = {attr: getattr(rule.scope, attr) for attr in PRODUCT_ATTRIBUTES if getattr(rule.scope, attr) is not None}
        resolved_products: set[str] = set()
        unresolved_products: list[str] = []
        for term in rule.scope.product_terms:
            glossary = self._glossary(term)
            if glossary:
                filters.update({k: v for k, v in glossary.items() if k not in filters})
                resolutions.append(EntityMatch(term=term, node_id=None, node_type="AttributeFilter",
                                               score=1.0, method=f"regulatory-glossary {glossary}"))
                continue
            matches = self.resolver.resolve(term, products, "Product")
            resolutions += matches
            found = {m.node_id for m in matches if m.node_id}
            resolved_products |= found
            if not found:
                unresolved_products.append(term)
        scope_notes: list[str] = []
        scope_status = "RESOLVED"
        if unresolved_products:
            scope_notes.append(f"Unresolved product term(s): {', '.join(repr(t) for t in unresolved_products)}.")
        if rule.scope.product_terms and not resolved_products and not filters:
            # Fail closed: wording that matched nothing must never widen to every product.
            scope_status = "UNRESOLVED"
            product_ids: list[str] = []
        else:
            candidates = [p for p in products if not resolved_products or p["id"] in resolved_products]
            product_ids = sorted(p["id"] for p in candidates if all(p["attributes"].get(k) == v for k, v in filters.items()))

        process_nodes = self.store.nodes("Process")
        process_ids: set[str] = set()
        for term in rule.processes:
            matches = self.resolver.resolve(term, process_nodes, "Process")
            resolutions += matches
            process_ids |= {m.node_id for m in matches if m.node_id}
        for check in rule.checks:
            for event in (check.trigger_event, check.required_event):
                if event in EVENT_PROCESSES:
                    process_ids.add(EVENT_PROCESSES[event])
            if check.capability in CAPABILITY_PROCESSES:
                process_ids.add(CAPABILITY_PROCESSES[check.capability])

        segment_nodes = self.store.nodes("CustomerSegment")
        segment_ids: set[str] = set()
        for term in rule.segments:
            matches = self.resolver.resolve(term, segment_nodes, "CustomerSegment")
            resolutions += matches
            segment_ids |= {m.node_id for m in matches if m.node_id}
        if not rule.segments:
            segment_ids = {e["node"]["id"] for e in self.store.neighbours(product_ids, ["ELIGIBLE_FOR"], rule.effective_date, "in")}
        elif not segment_ids:
            scope_notes.append(f"Unresolved segment term(s): {', '.join(repr(t) for t in rule.segments)}; no segments targeted.")

        control_ids: set[str] = set()
        for check in rule.checks:
            if check.capability:
                control_id = CAPABILITIES.get(check.capability, f"control:{check.capability.replace('_', '-')}")
                if not self.store.node(control_id):
                    self.store.upsert_node(control_id, "Control", check.capability.replace("_", " ").title(),
                                           {"needs_review": True, "created_by": "mapper"})
                control_ids.add(control_id)

        self.store.upsert_node(rule.rule_id, "Rule", rule.title, {
            "description": rule.description, "effective_date": rule.effective_date, "status": rule.status,
            "penalty_risk": rule.penalty_risk, "confidence": rule.confidence,
            "checks": [c.model_dump(exclude_none=True) for c in rule.checks],
            "scope_status": scope_status, "scope_notes": scope_notes,
            "citation": rule.citation.model_dump(exclude_none=True),
        })
        provenance = {"status": rule.status, "document_id": rule.citation.document_id, "snippet": rule.citation.snippet[:240]}
        edges = [(policy.policy_id, rule.rule_id, "CONTAINS")]
        edges += [(rule.rule_id, pid, "GOVERNS") for pid in product_ids]
        edges += [(rule.rule_id, pid, "APPLIES_TO") for pid in sorted(process_ids)]
        edges += [(rule.rule_id, sid, "TARGETS") for sid in sorted(segment_ids)]
        edges += [(rule.rule_id, cid, "REQUIRES") for cid in sorted(control_ids)]
        written = [{"source": s, "type": t, "target": d, "result": self.store.upsert_edge(s, d, t, rule.effective_date, None, provenance)}
                   for s, d, t in edges]
        return RuleMapping(
            rule_id=rule.rule_id, product_ids=product_ids, process_ids=sorted(process_ids),
            segment_ids=sorted(segment_ids), control_ids=sorted(control_ids), resolutions=resolutions,
            edges_written=written, verification_query=verification_gql(rule.rule_id),
            scope_status=scope_status, scope_notes=scope_notes,
        )

    @staticmethod
    def _glossary(term: str) -> dict | None:
        best, filters = 0.0, None
        for phrase, attrs in REGULATORY_GLOSSARY.items():
            score = lexical_score(term, [phrase])
            if score > best:
                best, filters = score, attrs
        return filters if best >= GLOSSARY_THRESHOLD else None


def review_rule(store: GraphStore, rule_id: str, decision: str, reviewer: str, as_of: str) -> dict:
    """Human-in-the-loop sign-off. Re-writes the rule's edges with the new status
    so the approval itself becomes a new system-time version in the graph."""
    if decision not in {"APPROVED", "REJECTED"}:
        raise ValueError("decision must be APPROVED or REJECTED")
    node = store.node(rule_id)
    if not node or node["type"] != "Rule":
        raise KeyError(rule_id)
    store.upsert_node(rule_id, "Rule", node["name"], {**node["attributes"], "status": decision, "reviewed_by": reviewer})
    changed = 0
    edges = store.neighbours([rule_id], ["GOVERNS", "APPLIES_TO", "TARGETS", "REQUIRES"], as_of)
    for edge in edges:
        attributes = {**edge["edge_attributes"], "status": decision, "reviewed_by": reviewer}
        store.upsert_edge(rule_id, edge["target_id"], edge["edge_type"], edge["valid_from"], edge["valid_to"], attributes)
        changed += 1
    if decision == "REJECTED":
        for edge_type in ("GOVERNS", "APPLIES_TO", "TARGETS", "REQUIRES"):
            store.end_edges(rule_id, edge_type, as_of)
    return {"rule_id": rule_id, "status": decision, "edges_versioned": changed}


def verification_gql(rule_id: str) -> str:
    """Parameterised GQL (bind @rule_id, @tenant_id) that re-reads what was written."""
    return (
        "GRAPH BankArchitecture\n"
        "MATCH (r:Entity {node_id: @rule_id})-[e:Relationship]->(x:Entity)\n"
        "WHERE r.tenant_id = @tenant_id AND e.superseded_at IS NULL\n"
        "RETURN e.edge_type AS edge_type, x.node_type AS node_type, x.node_id AS node_id\n"
        f"-- @rule_id = {rule_id!r}"
    )
