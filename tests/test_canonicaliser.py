"""Control canonicalisation (L7) and control dedupe. No network: Gemini is a fake chooser, embeddings are off."""

import json
import os
import tempfile
from pathlib import Path

os.environ.update(YUKDHIS_RUNTIME_DIR=tempfile.mkdtemp(prefix="yukdhis-test-"), YUKDHIS_BACKEND="local",
                  AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import pytest  # noqa: E402

from core.models import ComplianceCheck, ExtractedPolicy, Policy, ProductScope, Rule, SourceCitation  # noqa: E402
from ingestion.documents import parse_document  # noqa: E402
from ingestion.templates import floating_rate_reset  # noqa: E402
from knowledge_graph import canonicaliser as canon  # noqa: E402
from knowledge_graph.canonicaliser import CanonicalChoice, ControlCanonicaliser  # noqa: E402
from knowledge_graph.entity_resolution import EntityResolver  # noqa: E402
from knowledge_graph.mapper import PolicyMapper  # noqa: E402
from knowledge_graph.store import LocalGraphStore  # noqa: E402
from pipeline import stages  # noqa: E402
from scripts import dedupe_controls  # noqa: E402

FERR = ("AD Category-I banks shall maintain a Foreign Exchange Risk Reserve of 20% of the INR-equivalent notional of each "
        "FX derivative contract undertaken to hedge their own assets and liabilities.")
POLICY = Policy(policy_id="policy:FERR/1", title="FERR", issuer="RBI", reference="FERR/1", effective_date="2026-10-12")
FLOATING = Path("data/circulars/rbi_2023_08_18_floating_rate_reset_emi_loans.md")


LEGACY_CONTROLS = {"borrower-notification", "emi-tenor-choice", "fixed-rate-option", "reset-statement", "document-release-sla",
                   "delay-compensation", "document-collection-choice"}


@pytest.fixture(autouse=True)
def lending_vocabulary_only(monkeypatch):
    """The demo profile also enables the provisioning pack; these tests are about the seven original controls."""
    from knowledge_graph import packs

    original = packs.load_profile
    monkeypatch.setattr(packs, "load_profile",
                        lambda *a, **k: {**original(*a, **k), "ontology_packs": ["retail_lending", "msme_lending"]})


@pytest.fixture
def store(tmp_path):
    """The canonicaliser's top-K candidate list holds at most 8 controls. The bank baseline now carries ~50 controls, so these
    tests run on the original seven (lexical ties would otherwise push the expected candidate out of the list)."""
    topology = json.loads(Path("data/bank/architecture.json").read_text())
    keep = {f"control:{slug}" for slug in LEGACY_CONTROLS}
    is_extra = lambda node_id: node_id.startswith("control:") and node_id not in keep  # noqa: E731
    topology["nodes"] = [n for n in topology["nodes"] if not is_extra(n["id"])]
    topology["edges"] = [e for e in topology["edges"] if not (is_extra(e["source"]) or is_extra(e["target"]))]
    (tmp_path / "architecture.json").write_text(json.dumps(topology))
    graph = LocalGraphStore(tmp_path / "graph.db", "canon-test")
    graph.load_architecture(tmp_path / "architecture.json")
    return graph


class FakeGemini:
    """Records each request; ``answer(request)`` returns a CanonicalChoice, a dict, or raises."""

    def __init__(self, answer):
        self.answer, self.requests = answer, []

    def __call__(self, request):
        self.requests.append(request)
        return self.answer(request)


def ferr_by_meaning(request):
    ids = [c["id"] for c in request["candidates"]]
    if "control:maintain-ferr" in ids and "foreign exchange risk reserve" in request["citation"].lower():
        return CanonicalChoice(decision="MATCH", canonical_id="control:maintain-ferr", confidence=0.93,
                               rationale="Both maintain the Foreign Exchange Risk Reserve.")
    return CanonicalChoice(decision="NEW", confidence=0.9, rationale="No candidate holds an FX reserve.")


def canonicaliser(store, chooser=None, use_llm=None):
    return ControlCanonicaliser(store, EntityResolver(use_embeddings=False), chooser=chooser,
                                use_llm=chooser is not None if use_llm is None else use_llm)


def ferr_rule(suffix: str, capability: str, description: str = "Maintain the FX risk reserve") -> Rule:
    return Rule(rule_id=f"rule:FERR/1:{suffix}", title="Maintain FERR", description=FERR, effective_date="2026-10-12",
                scope=ProductScope(product_terms=["FX derivative contracts involving INR"]),
                checks=[ComplianceCheck(kind="control_required", description=description, capability=capability,
                                        capability_description="Hold 20% of FX derivative notional as a reserve with RBI.")],
                citation=SourceCitation(document_id="doc:ferr", snippet=FERR))


def controls(store) -> set[str]:
    return {n["id"] for n in store.nodes("Control")}


def test_ferr_wordings_map_to_one_control_and_the_next_run_is_lexical(store):
    gemini = FakeGemini(ferr_by_meaning)
    mapper = PolicyMapper(store, EntityResolver(use_embeddings=False), canonicaliser(store, gemini))
    first = mapper.map_rule(POLICY, ferr_rule("a", "maintain_ferr"))
    second = mapper.map_rule(POLICY, ferr_rule("b", "foreign_exchange_risk_reserve_maintenance"))
    assert first.control_ids == second.control_ids == ["control:maintain-ferr"]
    assert first.control_decisions[0].decision == "NEW" and first.control_decisions[0].needs_review
    decision = second.control_decisions[0]
    assert (decision.decision, decision.method, decision.model_decision) == ("MATCH", "gemini", "MATCH")
    assert not decision.needs_review and decision.confidence == pytest.approx(0.93)
    request = gemini.requests[-1]  # meaning, not just names, reaches the model
    assert FERR[:60] in request["citation"] and "reserve with RBI" in request["check_description"]
    assert len(request["candidates"]) <= canon.TOP_K and request["candidates"][0]["id"] == "control:maintain-ferr"
    assert store.node("control:foreign-exchange-risk-reserve-maintenance") is None
    node = store.node("control:maintain-ferr")
    assert "foreign exchange risk reserve maintenance" in node["attributes"]["synonyms"]
    assert node["attributes"]["aliases"][0]["rule_id"] == "rule:FERR/1:b"

    calls = len(gemini.requests)
    again = PolicyMapper(store, EntityResolver(use_embeddings=False), canonicaliser(store, gemini))
    third = again.map_rule(POLICY, ferr_rule("c", "foreign_exchange_risk_reserve_maintenance"))
    assert third.control_ids == ["control:maintain-ferr"] and len(gemini.requests) == calls  # synonym hit, no LLM
    assert third.control_decisions[0].decision == "SYNONYM"


@pytest.mark.parametrize("answer, method", [
    (CanonicalChoice(decision="MATCH", canonical_id="control:invented-by-model", confidence=0.99, rationale="x"),
     "gemini-guard-out-of-candidates"),
    (CanonicalChoice(decision="AMBIGUOUS", canonical_id="control:fixed-rate-option", confidence=0.8, rationale="x"),
     "gemini-ambiguous"),
    (CanonicalChoice(decision="MATCH", canonical_id="control:fixed-rate-option", confidence=0.4, rationale="x"),
     "gemini-low-confidence"),
])
def test_guards_turn_bad_model_answers_into_new_controls_pending_review(store, answer, method):
    gemini = FakeGemini(lambda _request: answer)
    decision = canonicaliser(store, gemini).resolve("rate_switch_window", "Offer a fixed-rate switch window", FERR, "rule:x", 0)
    assert (decision.decision, decision.method, decision.needs_review) == ("NEW", method, True)
    assert decision.control_id == "control:rate-switch-window"
    assert store.node("control:rate-switch-window")["attributes"]["needs_review"] is True
    assert "control:invented-by-model" not in controls(store)


def test_mapping_the_same_circular_twice_adds_no_controls_or_edges(store):
    document = parse_document(FLOATING.read_bytes(), FLOATING.name)
    extraction = floating_rate_reset(document)
    rules = [*extraction.rules, ferr_rule("a", "maintain_ferr"), ferr_rule("b", "foreign_exchange_risk_reserve_maintenance")]

    def run():
        mapper = PolicyMapper(store, EntityResolver(use_embeddings=False), canonicaliser(store, use_llm=False))
        mapper.upsert_policy(extraction.policy)
        return [mapper.map_rule(extraction.policy, rule.model_copy(deep=True)) for rule in rules]

    first = run()
    before = (controls(store), len(store.edge_history()))
    second = run()
    assert [m.control_ids for m in second] == [m.control_ids for m in first]
    assert {e["result"] for m in second for e in m.edges_written} == {"unchanged"}
    assert (controls(store), len(store.edge_history())) == before
    assert first[-1].control_ids == ["control:maintain-ferr"]  # acronym match without an LLM
    assert second[-1].control_decisions[0].decision == "SYNONYM"  # recorded on the first run


def test_llm_failure_falls_back_to_strict_lexical_and_never_fails(store):
    canonicaliser(store, use_llm=False).resolve("maintain_ferr", "Maintain FERR", FERR)

    def broken(_request):
        raise RuntimeError("Vertex AI quota exceeded")

    gemini = FakeGemini(broken)
    resolver = canonicaliser(store, gemini)
    matched = resolver.resolve("foreign_exchange_risk_reserve_maintenance", "Maintain FERR", FERR, "rule:y", 0)
    assert (matched.decision, matched.method, matched.control_id) == ("MATCH", "acronym-fallback", "control:maintain-ferr")
    assert "LLM error" in matched.rationale
    unrelated = resolver.resolve("board_approved_hedging_policy", "Board approves a hedging policy", FERR, "rule:y", 1)
    assert (unrelated.decision, unrelated.method, unrelated.needs_review) == ("NEW", "lexical-fallback", True)
    garbage = canonicaliser(store, FakeGemini(lambda _r: {"decision": "PERHAPS"})).resolve("treasury_limit_breach_log")
    assert garbage.decision == "NEW" and garbage.method == "lexical-fallback"
    assert len(gemini.requests) == 2


def test_exact_and_synonym_hits_make_no_llm_call(store):
    gemini = FakeGemini(lambda _r: pytest.fail("the model must not be asked"))
    resolver = canonicaliser(store, gemini)
    by_id = resolver.resolve("fixed_rate_option")
    by_name = resolver.resolve("fixed_rate_conversion_option")  # the seed control's name
    assert (by_id.decision, by_id.method, by_id.control_id) == ("EXACT", "ontology-id", "control:fixed-rate-option")
    assert (by_name.decision, by_name.method, by_name.control_id) == ("EXACT", "exact-name", "control:fixed-rate-option")
    assert gemini.requests == []


def test_stage_map_emits_decisions_and_the_evaluator_uses_the_canonical_control(store, monkeypatch):
    monkeypatch.setattr(stages, "get_graph_store", lambda: store)
    document = parse_document(FLOATING.read_bytes(), FLOATING.name)
    extraction = floating_rate_reset(document)
    target = next(r for r in extraction.rules if r.rule_id.endswith("fixed-rate-choice"))
    target.checks[0].capability = "fixed_rate_conversion_option"  # extractor wording, not the ontology id
    state = {"extraction": extraction.model_dump(), "as_of": "2026-10-17"}
    events = []
    with stages.event_sink(lambda *e: events.append(e)):
        output = stages.stage_map(state)
    decisions = [p for _, kind, p in events if kind == "control_canonicalised"]
    assert len(decisions) == sum(1 for r in extraction.rules for c in r.checks if c.capability)
    renamed = next(d for d in decisions if d["term"] == "fixed_rate_conversion_option")
    assert renamed["control_id"] == "control:fixed-rate-option" and renamed["decision"] == "EXACT"
    assert {"decision", "method", "confidence", "needs_review", "candidates"} <= set(renamed)
    assert any(m["control_decisions"] for m in output["mappings"])
    assert all(c.get("control_id") is None for r in state["extraction"]["rules"] for c in r["checks"])  # as extracted

    stages.stage_blast_radius(state)
    stages.stage_evaluate(state)
    result = next(r for r in state["results"] if r["rule_id"] == target.rule_id and r["kind"] == "control_required")
    assert result["status"] == "GAP" and result["products_missing_control"] == ["product:auto-loan-floating"]


def _legacy_duplicates(store):
    """The graph as the old mapper left it: one control per wording."""
    for control_id, capability in (("control:maintain-ferr", "maintain_ferr"),
                                   ("control:foreign-exchange-risk-reserve-maintenance",
                                    "foreign_exchange_risk_reserve_maintenance")):
        store.upsert_node(control_id, "Control", capability.replace("_", " ").title(), {"needs_review": True, "created_by": "mapper"})
    for rule_id, control_id in (("rule:FERR/1:a", "control:maintain-ferr"), ("rule:FERR/1:b", "control:maintain-ferr"),
                                ("rule:FERR/2:a", "control:foreign-exchange-risk-reserve-maintenance")):
        store.upsert_node(rule_id, "Rule", "Maintain FERR", {})
        store.upsert_edge(rule_id, control_id, "REQUIRES", "2026-10-12", None, {"status": "PENDING_REVIEW"})


def _requires(store, rule_id, as_of):
    return [e["target_id"] for e in store.neighbours([rule_id], ["REQUIRES"], as_of)]


def test_dedupe_dry_run_proposes_and_apply_supersedes_bitemporally(store, capsys):
    _legacy_duplicates(store)
    dup, canonical = "control:foreign-exchange-risk-reserve-maintenance", "control:maintain-ferr"
    nodes, history = store.nodes(), store.edge_history()

    assert dedupe_controls.main(["--no-llm"], store=store) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["mode"] == "dry-run"
    assert [(p["duplicate"], p["canonical"]) for p in printed["proposals"]] == [(dup, canonical)]  # more rules use it
    assert (store.nodes(), store.edge_history()) == (nodes, history)  # a dry run writes nothing

    assert dedupe_controls.main(["--apply", "--no-llm", "--as-of", "2026-10-17"], store=store) == 0
    rows = [r for r in store.edge_history("rule:FERR/2:a") if r["edge_type"] == "REQUIRES"]
    original = next(r for r in rows if r["target_id"] == dup and r["valid_to"] is None)
    assert original["superseded_at"] is not None  # kept, superseded in system time
    ended = next(r for r in rows if r["target_id"] == dup and r["superseded_at"] is None)
    assert (ended["valid_from"], ended["valid_to"]) == ("2026-10-12", "2026-10-17")
    opened = next(r for r in rows if r["target_id"] == canonical)
    assert (opened["valid_from"], opened["valid_to"]) == ("2026-10-17", None)
    assert _requires(store, "rule:FERR/2:a", "2026-10-15") == [dup]  # business-time history is intact
    assert _requires(store, "rule:FERR/2:a", "2026-10-18") == [canonical]
    assert store.node(dup)["attributes"]["merged_into"] == canonical

    decision = canonicaliser(store, use_llm=False).decide("foreign_exchange_risk_reserve_maintenance")
    assert (decision.control_id, decision.decision) == (canonical, "SYNONYM")
    assert dedupe_controls.propose_merges(store, canonicaliser(store, use_llm=False), "2026-10-18") == []


def test_dedupe_proposes_merges_that_gemini_finds_by_meaning(store):
    store.upsert_node("control:fx-hedge-reserve", "Control", "Fx Hedge Reserve",
                      {"capability": "fx_hedge_reserve", "description": "Reserve held against FX hedges"})
    store.upsert_node("control:maintain-ferr", "Control", "Maintain Ferr", {"capability": "maintain_ferr"})

    def answer(request):
        if request["term"] == "fx_hedge_reserve":
            return CanonicalChoice(decision="MATCH", canonical_id="control:maintain-ferr", confidence=0.85, rationale="Same reserve.")
        return CanonicalChoice(decision="NEW", confidence=0.9, rationale="Distinct.")

    gemini = FakeGemini(answer)
    proposals = dedupe_controls.propose_merges(store, canonicaliser(store, gemini), "2026-10-17")
    # Neither is an ontology control and no rule uses either, so the earlier id is kept as canonical.
    assert [(p.duplicate, p.canonical, p.method) for p in proposals] == [("control:maintain-ferr", "control:fx-hedge-reserve", "gemini")]
    assert all("control:fx-hedge-reserve" not in [c["id"] for c in r["candidates"]] for r in gemini.requests)  # never itself


def test_gemini_call_is_structured_deterministic_and_has_room_for_thinking(monkeypatch):
    import types

    import core.llm

    seen = {}

    class Models:
        def generate_content(self, model, contents, config):
            seen.update(model=model, contents=contents, config=config)
            choice = CanonicalChoice(decision="NEW", confidence=0.8, rationale="Distinct.")
            return types.SimpleNamespace(parsed=choice, text=choice.model_dump_json(),
                                         candidates=[types.SimpleNamespace(finish_reason="STOP")])

    monkeypatch.setattr(core.llm, "genai_client", lambda: types.SimpleNamespace(models=Models()))
    request = {"term": "maintain_ferr", "check_description": "Maintain FERR", "citation": FERR,
               "candidates": [{"id": "control:fixed-rate-option", "name": "Fixed Rate Conversion Option"}]}
    assert canon.gemini_choose(request).decision == "NEW"
    config = seen["config"]
    budget = canon.thinking_budget(seen["model"])
    assert seen["model"] == canon.get_settings().fast_model and canon.thinking_budget("gemini-2.5-flash") == 0
    assert config.temperature == 0 and config.response_schema is CanonicalChoice
    assert config.thinking_config.thinking_budget == budget and config.max_output_tokens == canon.ANSWER_TOKENS + budget
    assert "control:fixed-rate-option" in seen["contents"] and FERR in seen["contents"]
    assert canon.thinking_budget("gemini-2.5-pro") > 0  # Pro cannot turn thinking off; its budget is added to the limit

    class Truncated:
        def generate_content(self, model, contents, config):
            return types.SimpleNamespace(parsed=None, text='{"decision": "MA',
                                         candidates=[types.SimpleNamespace(finish_reason="FinishReason.MAX_TOKENS")])

    monkeypatch.setattr(core.llm, "genai_client", lambda: types.SimpleNamespace(models=Truncated()))
    with pytest.raises(ValueError, match="cut off"):
        canon.gemini_choose(request)
