"""Ontology packs: domain content as data, a stable meta-model in code, governed growth, pack-driven applicability.

No network, no wall-clock assumptions, no reliance on a pre-existing ``.data``: graphs live in ``tmp_path`` and tests that
change packs work on a private copy of ``data/ontology/packs``.
"""

import json
import os
import shutil
import tempfile
import types
from pathlib import Path

os.environ.update(YUKDHIS_RUNTIME_DIR=tempfile.mkdtemp(prefix="yukdhis-test-"), YUKDHIS_BACKEND="local",
                  AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import pytest  # noqa: E402

from core.models import ComplianceCheck, Policy, ProductScope, Rule, SourceCitation  # noqa: E402
from knowledge_graph import canonicaliser, governance, ontology, packs  # noqa: E402
from knowledge_graph.canonicaliser import ControlCanonicaliser  # noqa: E402
from knowledge_graph.entity_resolution import EntityResolver  # noqa: E402
from knowledge_graph.mapper import PolicyMapper  # noqa: E402
from knowledge_graph.store import LocalGraphStore  # noqa: E402
from pipeline import applicability  # noqa: E402
from scripts import ontology_proposals  # noqa: E402

# Golden values: the constants of knowledge_graph/ontology.py on origin/main (efcaa1f), captured before they moved to
# data/ontology/packs/retail_lending.json. The merged packs must reproduce them exactly.
OLD_CAPABILITIES = {'borrower_notification': 'control:borrower-notification',
 'emi_or_tenor_choice': 'control:emi-tenor-choice',
 'fixed_rate_option': 'control:fixed-rate-option',
 'reset_statement': 'control:reset-statement',
 'document_release_sla': 'control:document-release-sla',
 'delay_compensation': 'control:delay-compensation',
 'document_collection_choice': 'control:document-collection-choice'}

OLD_RECORD_FLAGS = {'borrower_notified': 'Borrower was told the EMI/tenor impact of the last reset.',
 'has_fixed_rate_option': 'Loan offers a switch to a fixed rate.',
 'has_emi_tenor_choice': 'Borrower may choose EMI increase or tenor extension.',
 'statement_available': 'Reset statement with principal, interest and APR issued.'}

OLD_EVENT_TYPES = set(['DOCUMENTS_RELEASED', 'LOAN_CLOSED', 'LOAN_DISBURSED', 'RATE_RESET', 'RATE_RESET_NOTICE_SENT'])

OLD_PRODUCT_ATTRIBUTES = ('rate_type', 'instalment_based', 'secured', 'collateral_type', 'category')

OLD_SANITIZED_VIEWS = {'vw_compliance_impact': {'description': 'One row per loan. Opaque refs only; no names, contact or account '
                                         'numbers.',
                          'columns': {'loan_ref': 'opaque loan reference',
                                      'customer_ref': 'opaque customer reference',
                                      'product_id': 'canonical product node id',
                                      'segment_id': 'customer segment node id',
                                      'branch_id': 'branch node id',
                                      'status': 'ACTIVE | CLOSED',
                                      'rate_type': 'FLOATING | FIXED',
                                      'emi_amount': 'INR',
                                      'outstanding_balance': 'INR',
                                      'sanctioned_on': 'date',
                                      'closed_on': 'date or null',
                                      'rate_reset_due': 'date or null',
                                      'borrower_notified': 'boolean - Borrower was told the EMI/tenor impact '
                                                           'of the last reset.',
                                      'has_fixed_rate_option': 'boolean - Loan offers a switch to a fixed '
                                                               'rate.',
                                      'has_emi_tenor_choice': 'boolean - Borrower may choose EMI increase or '
                                                              'tenor extension.',
                                      'statement_available': 'boolean - Reset statement with principal, '
                                                             'interest and APR issued.'}},
 'vw_loan_events': {'description': 'Operational event log per loan, tokenized.',
                    'columns': {'loan_ref': 'opaque loan reference',
                                'customer_ref': 'opaque customer reference',
                                'product_id': 'canonical product node id',
                                'branch_id': 'branch node id',
                                'segment_id': 'customer segment node id',
                                'event_type': 'DOCUMENTS_RELEASED|LOAN_CLOSED|LOAN_DISBURSED|RATE_RESET|RATE_RESET_NOTICE_SENT',
                                'occurred_on': 'date'}},
 'vw_branch_directory': {'description': 'Branch metadata without manager contact details.',
                         'columns': {'branch_id': 'branch node id',
                                     'branch_name': 'name',
                                     'city': 'city',
                                     'region': 'region'}}}

OLD_PII_COLUMNS = set(['aadhaar',
 'account_number',
 'address',
 'customer_id',
 'date_of_birth',
 'dob',
 'email',
 'full_name',
 'manager_email',
 'manager_name',
 'mobile',
 'name',
 'pan',
 'phone'])

OLD_REGULATORY_GLOSSARY = {'personal loans': {'category': 'RETAIL_LOAN'},
 'retail loans': {'category': 'RETAIL_LOAN'},
 'loans to individuals': {'category': 'RETAIL_LOAN'},
 'emi based loans': {'instalment_based': True},
 'equated instalment loans': {'instalment_based': True},
 'floating rate loans': {'rate_type': 'FLOATING'},
 'secured loans': {'secured': True},
 'movable immovable property': {'secured': True}}

OLD_REGULATED_ENTITY_TERMS = set(['ad bank',
 'ad banks',
 'authorised dealer',
 'authorised dealer category i',
 'authorised dealer category i banks',
 'authorised dealers',
 'authorized dealer',
 'authorized dealers',
 'bank',
 'banks',
 'co operative bank',
 'co operative banks',
 'commercial bank',
 'commercial banks',
 'cooperative bank',
 'cooperative banks',
 'financial institution',
 'financial institutions',
 'lender',
 'lenders',
 'nbfc',
 'nbfcs',
 'payments bank',
 'payments banks',
 'primary dealer',
 'primary dealers',
 'regulated entities',
 'regulated entity',
 'scheduled commercial bank',
 'scheduled commercial banks',
 'small finance bank',
 'small finance banks'])

OLD_EVENT_PROCESSES = {'LOAN_CLOSED': 'process:loan-closure',
 'DOCUMENTS_RELEASED': 'process:collateral-release',
 'RATE_RESET': 'process:rate-reset',
 'RATE_RESET_NOTICE_SENT': 'process:borrower-notification'}

OLD_CAPABILITY_PROCESSES = {'borrower_notification': 'process:borrower-notification',
 'emi_or_tenor_choice': 'process:rate-reset',
 'fixed_rate_option': 'process:rate-reset',
 'reset_statement': 'process:borrower-notification',
 'document_release_sla': 'process:collateral-release',
 'delay_compensation': 'process:collateral-release',
 'document_collection_choice': 'process:document-custody'}

OLD_WORKFLOWS = {'SEND_BORROWER_NOTICE': 'Customer Communications sends a templated notice to each borrower in the worklist.',
 'BRANCH_MANAGER_ALERT': 'Alert branch managers with the per-branch worklist.',
 'PRODUCT_CONTROL_CHANGE': 'Raise a change request with the product owner to add a missing control.',
 'COMPENSATION_PAYOUT_REVIEW': 'Finance reviews and approves statutory delay compensation.'}

OLD_NODE_TYPES = {'Policy': 'Regulatory or internal policy document (BIAN: Regulatory Compliance).',
 'Rule': 'Actionable clause extracted from a policy, with executable checks.',
 'Product': 'Banking product (BIAN: Product Directory / Consumer Loan).',
 'Process': 'Business process mapped to a BIAN service domain.',
 'Control': 'Capability or control a product provides to satisfy obligations.',
 'CustomerSegment': 'Abstract customer grouping; never individual customers.',
 'Team': 'Accountable owner of a product or process.',
 'Branch': 'Operational unit; manager contact details stay in the PII vault.'}

OLD_EDGE_TYPES = {'CONTAINS': ('Policy', 'Rule'),
 'SUPERSEDES': ('Policy', 'Policy'),
 'GOVERNS': ('Rule', 'Product'),
 'APPLIES_TO': ('Rule', 'Process'),
 'TARGETS': ('Rule', 'CustomerSegment'),
 'REQUIRES': ('Rule', 'Control'),
 'USES': ('Product', 'Process'),
 'PROVIDES': ('Product', 'Control'),
 'OWNED_BY': ('Product|Process', 'Team'),
 'ELIGIBLE_FOR': ('CustomerSegment', 'Product'),
 'OFFERED_AT': ('Product', 'Branch')}



# ---------------------------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------------------------
NAMES = ["CAPABILITIES", "RECORD_FLAGS", "EVENT_TYPES", "PRODUCT_ATTRIBUTES", "SANITIZED_VIEWS", "PII_COLUMNS",
         "REGULATORY_GLOSSARY", "REGULATED_ENTITY_TERMS", "EVENT_PROCESSES", "CAPABILITY_PROCESSES", "WORKFLOWS"]
NEW_EDGES = {"AMENDS": ("Policy", "Policy"), "REFERS_TO": ("Policy", "Policy"), "IMPLEMENTS": ("Policy", "Rule"),
             "ENFORCES": ("Policy", "Control")}
LENDING_PACK_FILES = {"retail_lending", "msme_lending"}
FERR = ("AD Category-I banks shall maintain a Foreign Exchange Risk Reserve of 20% of the INR-equivalent notional of each "
        "FX derivative contract undertaken to hedge their own assets and liabilities.")
POLICY = Policy(policy_id="policy:FERR/1", title="FERR", issuer="RBI", reference="FERR/1", effective_date="2026-10-12")


def profile(**over) -> dict:
    base = {"tenant_id": "t-one", "entity_type": "scheduled_commercial_bank", "licences": {},
            "business_lines": {"retail_lending": "active", "deposits": "active", "treasury_derivatives": "not_onboarded"},
            "onboarded_data_domains": ["loans", "customers"], "not_onboarded_data_domains": ["deposits", "treasury_trades"]}
    return {**base, **over}


def pack_data(pack_id: str, **over) -> dict:
    return {"pack_id": pack_id, "version": "1.0.0", "title": pack_id.title(), "domain": pack_id, **over}


def write_pack(directory: Path, data: dict) -> Path:
    path = directory / f"{data['pack_id']}.json"
    path.write_text(json.dumps(data))
    return path


def capability(slug: str, **over) -> dict:
    return {"control": f"control:{slug}", "description": f"{slug} description", "process": None, "synonyms": [], **over}


@pytest.fixture
def pack_dir(tmp_path, monkeypatch):
    """A private copy of the real packs that the default loaders use for the duration of the test."""
    directory = tmp_path / "packs"
    shutil.copytree(packs.PACK_DIR, directory)
    for leftover in directory.glob("tenant_*.json"):
        leftover.unlink()
    monkeypatch.setattr(packs, "PACK_DIR", directory)
    packs.clear_cache()
    yield directory
    packs.clear_cache()


@pytest.fixture
def store(tmp_path):
    graph = LocalGraphStore(tmp_path / "graph.db", "t-one")
    graph.load_architecture()
    return graph


def propose(store, capability_id: str = "rate_switch_window", suffix: str = "a") -> str:
    """Map a rule whose capability matches nothing: the canonicaliser creates a NEW control flagged needs_review."""
    mapper = PolicyMapper(store, EntityResolver(use_embeddings=False),
                          ControlCanonicaliser(store, EntityResolver(use_embeddings=False), use_llm=False))
    mapper.upsert_policy(POLICY)
    rule = Rule(rule_id=f"rule:FERR/1:{suffix}", title="Rate switch window", description=FERR, effective_date="2026-10-12",
                scope=ProductScope(product_terms=["four-wheeler advances"]),
                checks=[ComplianceCheck(kind="control_required", description="Offer a rate switch window",
                                        capability=capability_id)],
                citation=SourceCitation(document_id="doc:ferr", snippet=FERR))
    mapping = mapper.map_rule(POLICY, rule)
    return mapping.control_ids[0]


# ---------------------------------------------------------------------------------------------------------------
# Golden equality: the packs reproduce the constants that used to live in Python
# ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", NAMES)
def test_module_constants_equal_the_old_python_constants(name):
    old, new = globals()[f"OLD_{name}"], getattr(ontology, name)
    assert new == (tuple(old) if name == "PRODUCT_ATTRIBUTES" else old)


@pytest.mark.parametrize("profile_over", [{}, {"ontology_packs": None}])
def test_get_ontology_equals_the_old_constants_with_and_without_the_packs_key(profile_over):
    prof = dict(packs.load_profile())
    if profile_over.get("ontology_packs", 1) is None:
        prof.pop("ontology_packs")  # key absent: every pack onboarded for the profile is enabled
    vocabulary = ontology.get_ontology(prof)
    assert vocabulary.capabilities == OLD_CAPABILITIES
    assert vocabulary.record_flags == OLD_RECORD_FLAGS
    assert set(vocabulary.event_types) == OLD_EVENT_TYPES
    assert vocabulary.product_attributes == tuple(OLD_PRODUCT_ATTRIBUTES)
    assert vocabulary.sanitized_views == OLD_SANITIZED_VIEWS
    assert vocabulary.glossary == OLD_REGULATORY_GLOSSARY
    assert vocabulary.event_processes == OLD_EVENT_PROCESSES
    assert vocabulary.capability_processes == OLD_CAPABILITY_PROCESSES
    assert vocabulary.workflows == OLD_WORKFLOWS
    assert {p for p, _ in vocabulary.packs} == LENDING_PACK_FILES


def test_meta_model_keeps_the_old_types_and_adds_the_lineage_and_enforcement_edges():
    assert ontology.NODE_TYPES == OLD_NODE_TYPES
    for edge, ends in OLD_EDGE_TYPES.items():
        assert ontology.EDGE_TYPES[edge] == ends
    assert {k: ontology.EDGE_TYPES[k] for k in NEW_EDGES} == NEW_EDGES
    assert set(ontology.EDGE_TYPES) == set(OLD_EDGE_TYPES) | set(NEW_EDGES)
    assert ontology.POLICY_KINDS == ("regulatory", "internal")
    summary = ontology.ontology_summary("lending")
    assert summary["edge_types"]["AMENDS"] == "Policy -> Policy" and summary["capabilities"] == OLD_CAPABILITIES
    assert {p["pack_id"] for p in summary["packs"]} == LENDING_PACK_FILES


def test_old_import_names_still_exist():
    for name in NAMES + ["NODE_TYPES", "EDGE_TYPES", "ontology_summary"]:
        assert hasattr(ontology, name), name


def test_policy_nodes_get_a_kind_and_circular_lineage_edges(store):
    mapper = PolicyMapper(store, EntityResolver(use_embeddings=False))
    internal = types.SimpleNamespace(policy_id="policy:internal/1", title="Internal FX policy", issuer="Bank", reference="IP/1",
                                     issued_on=None, effective_date="2026-10-12", source_uri=None, supersedes=[],
                                     kind="internal", amends=[POLICY.policy_id], refers_to=["policy:other"])
    mapper.upsert_policy(POLICY)
    written = mapper.upsert_policy(internal)
    assert store.node(POLICY.policy_id)["attributes"]["kind"] == "regulatory"
    assert store.node("policy:internal/1")["attributes"]["kind"] == "internal"
    assert {(w["type"], w["target"]) for w in written} == {("AMENDS", POLICY.policy_id), ("REFERS_TO", "policy:other")}
    assert [e["target_id"] for e in store.neighbours(["policy:internal/1"], ["AMENDS"], "2026-10-17")] == [POLICY.policy_id]
    internal.kind = "memo"
    with pytest.raises(ValueError, match="kind"):
        mapper.upsert_policy(internal)


# ---------------------------------------------------------------------------------------------------------------
# Every pack file in the directory (also validates packs merged in from other branches)
# ---------------------------------------------------------------------------------------------------------------
def test_every_pack_file_loads_matches_the_profile_and_merges_without_conflict():
    files = sorted(packs.PACK_DIR.glob("*.json"))
    assert {f.stem for f in files} >= {"retail_lending", "msme_lending", "treasury_derivatives", "capital_adequacy",
                                         "deposits_kyc", "liquidity_management"}
    loaded = [packs.load_pack(f) for f in files]  # raises PackError naming file and path
    demo = packs.load_profile()
    for pack in loaded:
        assert packs.check_against_profile(pack, demo) == [], pack.pack_id
    shared = [p for p in loaded if not p.is_tenant_overlay]
    merged = packs.merge_packs(shared)  # raises PackConflictError on any clash
    assert set(merged.capabilities) >= set(OLD_CAPABILITIES)
    assert len({c for c in merged.capabilities.values()}) == len(merged.capabilities)  # one control per capability
    assert all(p.version.count(".") == 2 for p in loaded)


def test_signal_only_packs_declare_signals_but_no_vocabulary():
    for pack_id in ("treasury_derivatives", "capital_adequacy", "deposits_kyc"):
        pack = packs.load_pack(packs.PACK_DIR / f"{pack_id}.json")
        assert pack.signals and pack.business_line and pack.requires_data_domains
        assert not pack.capabilities and not pack.views and not pack.workflows


# ---------------------------------------------------------------------------------------------------------------
# Validation and conflicts
# ---------------------------------------------------------------------------------------------------------------
def test_unknown_keys_are_rejected_with_file_name_and_path(tmp_path):
    top = write_pack(tmp_path, pack_data("alpha", typo_key=1))
    with pytest.raises(packs.PackError, match=r"alpha\.json: typo_key: unknown key"):
        packs.load_pack(top)
    nested = write_pack(tmp_path, pack_data("beta", capabilities={"x": {**capability("x"), "colour": "red"}}))
    with pytest.raises(packs.PackError, match=r"beta\.json: capabilities\.x\.colour: unknown key"):
        packs.load_pack(nested)
    signal = write_pack(tmp_path, pack_data("gamma", applicability_signals={"keywords": ["x"], "business_lines": "a"}))
    with pytest.raises(packs.PackError, match=r"gamma\.json: .*business_lines"):
        packs.load_pack(signal)


def test_bad_values_report_the_path(tmp_path):
    bad = write_pack(tmp_path, pack_data("delta", capabilities={"x": {"control": "ctrl:x"}}, event_types=["lower"],
                                         version="1.0", applicability_signals={"keywords": ["("]}))
    with pytest.raises(packs.PackError) as raised:
        packs.load_pack(bad)
    message = str(raised.value)
    assert "delta.json: capabilities.x.control" in message and "delta.json: version" in message
    assert "delta.json: event_types" in message and "regular expression" in message
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    with pytest.raises(packs.PackError, match=r"broken\.json: invalid JSON"):
        packs.load_pack(broken)
    wrong = write_pack(tmp_path, pack_data("epsilon"))
    wrong.rename(tmp_path / "zeta.json")
    with pytest.raises(packs.PackError, match=r"zeta\.json: pack_id"):
        packs.load_pack(tmp_path / "zeta.json")


def test_a_bad_pack_in_the_directory_fails_the_load_with_its_name(tmp_path):
    write_pack(tmp_path, pack_data("good"))
    write_pack(tmp_path, pack_data("bad", extra=True))
    with pytest.raises(packs.PackError, match=r"bad\.json"):
        packs.load_all_packs(tmp_path)


def test_same_id_with_different_content_is_a_load_error_but_identical_content_merges(tmp_path):
    one = packs.parse_pack(pack_data("one", capabilities={"c": capability("c")}, record_flags={"f": "flag"},
                                     views={"vw": {"description": "d", "columns": {"a": "x"}}}, workflows={"WF": "wf"},
                                     glossary={"term": {"secured": True}}, event_types=["EV"], event_processes={"EV": "process:p"}))
    same = packs.parse_pack(pack_data("two", capabilities={"c": capability("c")}, record_flags={"f": "flag"},
                                      views={"vw": {"description": "d", "columns": {"a": "x"}}}, workflows={"WF": "wf"},
                                      glossary={"term": {"secured": True}}, event_types=["EV"], event_processes={"EV": "process:p"}))
    assert packs.merge_packs([one, same]).capabilities == {"c": "control:c"}
    clash = packs.parse_pack(pack_data("three", capabilities={"c": capability("c", description="different")},
                                       record_flags={"f": "another"}, views={"vw": {"description": "d", "columns": {"a": "y"}}},
                                       workflows={"WF": "other"}, glossary={"term": {"secured": False}},
                                       event_types=["EV"], event_processes={"EV": "process:q"}))
    with pytest.raises(packs.PackConflictError) as raised:
        packs.merge_packs([one, clash])
    message = str(raised.value)
    for item in ("capability 'c'", "record flag 'f'", "view 'vw'", "workflow 'WF'", "glossary term 'term'", "event process 'EV'"):
        assert item in message
    assert "'one'" in message and "'three'" in message


def test_conflicting_packs_in_a_profile_fail_get_ontology(tmp_path):
    write_pack(tmp_path, pack_data("one", capabilities={"c": capability("c")}))
    write_pack(tmp_path, pack_data("two", capabilities={"c": capability("c", process="process:x")}))
    with pytest.raises(packs.PackConflictError):
        ontology.get_ontology(profile(ontology_packs=["one", "two"]), tmp_path)
    assert ontology.get_ontology(profile(ontology_packs=["one"]), tmp_path).capabilities == {"c": "control:c"}


# ---------------------------------------------------------------------------------------------------------------
# Enabling packs
# ---------------------------------------------------------------------------------------------------------------
def test_a_subset_of_packs_gives_a_subset_vocabulary(tmp_path):
    write_pack(tmp_path, pack_data("one", capabilities={"c1": capability("c1", process="process:p1")}, record_flags={"f1": "one"},
                                   event_types=["E1"], product_attributes=["a1"], workflows={"WF1": "w"}))
    write_pack(tmp_path, pack_data("two", capabilities={"c2": capability("c2")}, record_flags={"f2": "two"},
                                   event_types=["E2"], product_attributes=["a1", "a2"], glossary={"g": {"secured": True}}))
    only_one = ontology.get_ontology(profile(ontology_packs=["one"]), tmp_path)
    assert only_one.capabilities == {"c1": "control:c1"} and only_one.record_flags == {"f1": "one"}
    assert set(only_one.event_types) == {"E1"} and only_one.glossary == {} and only_one.workflows == {"WF1": "w"}
    assert only_one.capability_processes == {"c1": "process:p1"}
    both = ontology.get_ontology(profile(ontology_packs=["one", "two"]), tmp_path)
    assert set(both.capabilities) == {"c1", "c2"} and both.product_attributes == ("a1", "a2")
    none = ontology.get_ontology(profile(ontology_packs=[]), tmp_path)
    assert none.capabilities == {} and none.sanitized_views == {} and none.packs == ()


def test_without_the_key_every_onboarded_pack_is_enabled(tmp_path):
    write_pack(tmp_path, pack_data("lending", business_line="retail_lending", requires_data_domains=["loans"],
                                   capabilities={"c1": capability("c1")}))
    write_pack(tmp_path, pack_data("trading", business_line="treasury_derivatives", requires_data_domains=["treasury_trades"],
                                   capabilities={"c2": capability("c2")}))
    write_pack(tmp_path, pack_data("needs_data", business_line="deposits", requires_data_domains=["deposits"],
                                   capabilities={"c3": capability("c3")}))
    write_pack(tmp_path, pack_data("general", capabilities={"c4": capability("c4")}))
    enabled = {p.pack_id for p in packs.enabled_packs(profile(), tmp_path)}
    assert enabled == {"lending", "general"}  # trading line not onboarded; deposits data domain not onboarded
    onboarded = profile(business_lines={"retail_lending": "active", "treasury_derivatives": "active"},
                        onboarded_data_domains=["loans", "treasury_trades"])
    assert {p.pack_id for p in packs.enabled_packs(onboarded, tmp_path)} == {"lending", "trading", "general"}


def test_unknown_pack_id_in_the_profile_is_an_error(tmp_path):
    write_pack(tmp_path, pack_data("one"))
    with pytest.raises(packs.PackError, match="unknown pack id"):
        packs.enabled_packs(profile(ontology_packs=["one", "nope"]), tmp_path)


def test_tenant_overlay_is_enabled_only_for_its_tenant(tmp_path):
    write_pack(tmp_path, pack_data("one", capabilities={"c1": capability("c1")}))
    write_pack(tmp_path, pack_data("tenant_t_one", capabilities={"mine": capability("mine")}))
    write_pack(tmp_path, pack_data("tenant_other", capabilities={"theirs": capability("theirs")}))
    mine = ontology.get_ontology(profile(ontology_packs=["one"]), tmp_path)
    assert set(mine.capabilities) == {"c1", "mine"}
    assert "theirs" not in ontology.get_ontology(profile(), tmp_path).capabilities


def test_pack_files_are_re_read_when_they_change(tmp_path):
    path = write_pack(tmp_path, pack_data("one", capabilities={"c1": capability("c1")}))
    assert set(ontology.get_ontology(profile(ontology_packs=["one"]), tmp_path).capabilities) == {"c1"}
    path.write_text(json.dumps(pack_data("one", capabilities={"c1": capability("c1"), "c2": capability("c2")})))
    assert set(ontology.get_ontology(profile(ontology_packs=["one"]), tmp_path).capabilities) == {"c1", "c2"}


# ---------------------------------------------------------------------------------------------------------------
# Applicability reads its keyword signals from the packs
# ---------------------------------------------------------------------------------------------------------------
def gate_rule(text: str, **checks) -> Rule:
    return Rule(rule_id="rule:gate", title=text[:50], description=text, effective_date="2026-10-12", scope=ProductScope(),
                checks=[ComplianceCheck(kind="control_required", description=text[:30], capability="x", **checks)],
                citation=SourceCitation(document_id="doc:g", snippet=text))


def test_default_signals_come_from_the_packs_in_the_old_order():
    expected = ["nddc", "ifsc_banking_unit", "ferr", "fx_derivatives", "capital", "liquidity", "deposits", "kyc_aml",
                "msme_loans", "loans"]
    names = [s.name for s in applicability.signals()]
    assert [n for n in names if n in expected] == expected  # packs added later may add signals, never reorder these
    assert {s.pack_id for s in applicability.signals()} >= {"treasury_derivatives", "capital_adequacy", "deposits_kyc",
                                                            "liquidity_management", "msme_lending", "retail_lending"}


def test_pack_signals_drive_the_gate_including_for_packs_that_are_not_enabled(tmp_path, monkeypatch):
    write_pack(tmp_path, pack_data(
        "space", business_line="space_mining", requires_data_domains=["asteroids"],
        applicability_signals={"keywords": [r"\bregolith\b"], "business_line": "space_mining", "data_domains": ["asteroids"]}))
    monkeypatch.setattr(packs, "PACK_DIR", tmp_path)
    packs.clear_cache()
    try:
        text = "Banks shall report regolith holdings."
        prof = profile(ontology_packs=[], business_lines={"space_mining": "not_onboarded"})
        unknown = applicability.assess_rule(gate_rule(text), None, prof)  # pack is not enabled, signal still fires
        assert unknown.applicable is None and unknown.domains == ["asteroids"]
        assert any("space_mining is not onboarded" in r for r in unknown.reasons)
        absent = applicability.assess_rule(gate_rule(text), None, profile(ontology_packs=[], business_lines={"space_mining": "none"}))
        assert absent.applicable is False and any("no space_mining business" in r for r in absent.reasons)
        silent = applicability.assess_rule(gate_rule("Banks shall report holdings."), None, prof)
        assert silent.applicable is None and silent.domains == []  # no keyword, no signal
        # Editing the pack changes the behaviour: nothing is hard-coded in applicability.py any more.
        write_pack(tmp_path, pack_data("space", business_line="space_mining", applicability_signals={
            "keywords": [r"\bmoon dust\b"], "business_line": "space_mining", "data_domains": ["asteroids"]}))
        assert applicability.assess_rule(gate_rule(text), None, prof).domains == []
    finally:
        packs.clear_cache()


def test_licence_signal_in_a_pack_marks_the_rule_not_applicable(tmp_path, monkeypatch):
    write_pack(tmp_path, pack_data("fx", applicability_signals=[{
        "name": "ibu", "label": "IFSC activity", "keywords": [r"\bgift city\b"], "licence": "ifsc_banking_unit"}]))
    monkeypatch.setattr(packs, "PACK_DIR", tmp_path)
    packs.clear_cache()
    try:
        result = applicability.assess_rule(gate_rule("Units in GIFT City shall hedge."), None, profile(ontology_packs=[]))
        assert result.applicable is False and any("IFSC Banking Unit" in r for r in result.reasons)
    finally:
        packs.clear_cache()


def test_ontology_checks_use_the_data_domains_of_the_pack_that_owns_the_vocabulary():
    known = gate_rule("The customer shall be informed.")
    known.checks[0].capability = "fixed_rate_option"
    assert applicability.assess_rule(known, None, packs.load_profile()).domains == ["loans"]
    flag = gate_rule("x")
    flag.checks = [ComplianceCheck(kind="record_flag", description="d", record_field="borrower_notified", expected=True)]
    assert applicability.assess_rule(flag, None, packs.load_profile()).domains == ["loans"]
    event = gate_rule("x")
    event.checks = [ComplianceCheck(kind="event_deadline", description="d", trigger_event="LOAN_CLOSED",
                                    required_event="DOCUMENTS_RELEASED", deadline_days=30)]
    assert applicability.assess_rule(event, None, packs.load_profile()).domains == ["loan_events", "loans"]
    off = applicability.assess_rule(known, None, {**packs.load_profile(), "ontology_packs": []})
    assert off.domains == [] and off.applicable is None  # vocabulary not enabled: nothing links the rule to a domain


# ---------------------------------------------------------------------------------------------------------------
# Extraction vocabulary and canonicaliser candidates follow the enabled packs
# ---------------------------------------------------------------------------------------------------------------
def test_canonicaliser_candidates_and_ids_follow_the_enabled_packs(pack_dir, store):
    write_pack(pack_dir, pack_data("tenant_t_one", capabilities={
        "kyc_refresh": capability("kyc-refresh", synonyms=["periodic kyc update"], description="Refresh KYC periodically")}))
    canon = ControlCanonicaliser(store, EntityResolver(use_embeddings=False), use_llm=False)
    by_id = {c.id: c for c in canon.candidates()}
    assert {"control:borrower-notification", "control:kyc-refresh"} <= set(by_id)
    assert by_id["control:kyc-refresh"].source == "ontology" and by_id["control:kyc-refresh"].synonyms == ["periodic kyc update"]
    assert canonicaliser.slug_control_id("kyc_refresh") == "control:kyc-refresh"
    decision = canon.decide("kyc_refresh")
    assert (decision.decision, decision.control_id) == ("EXACT", "control:kyc-refresh")
    assert "kyc_refresh" in ontology.get_ontology(tenant_ids=["t-one"]).capabilities  # what the extractor prompt lists


def test_extraction_prompt_lists_the_enabled_vocabulary(pack_dir):
    from ingestion import extractor

    write_pack(pack_dir, pack_data("tenant_t_one", capabilities={"kyc_refresh": capability("kyc-refresh")},
                                   record_flags={"kyc_current": "KYC is current."}, event_types=["KYC_REFRESHED"]))
    vocabulary = ontology.get_ontology(profile(tenant_id="t-one", ontology_packs=["retail_lending"]))
    prompt = extractor.EXTRACTION_PROMPT.format(
        capabilities=sorted(vocabulary.capabilities), flags=sorted(vocabulary.record_flags), events=sorted(vocabulary.event_types),
        document_id="d", parser="p", chunks="")
    assert "kyc_refresh" in prompt and "kyc_current" in prompt and "KYC_REFRESHED" in prompt and "borrower_notification" in prompt


# ---------------------------------------------------------------------------------------------------------------
# Governed growth: proposals, promote, reject
# ---------------------------------------------------------------------------------------------------------------
def snapshot(directory: Path) -> dict:
    return {p.name: p.read_text() for p in sorted(directory.glob("*.json"))}


def test_new_controls_are_pending_proposals_with_their_sources(store):
    control_id = propose(store)
    pending = governance.pending_proposals(store)
    assert [p["id"] for p in pending] == [control_id]
    entry = pending[0]
    assert entry["capability"] == "rate_switch_window" and entry["first_seen"] and entry["source_rule"] == "rule:FERR/1:a"
    assert entry["rules"][0]["policy_id"] == POLICY.policy_id and entry["documents"] == ["doc:ferr"]
    assert "rule:FERR/1:a" in {r["rule_id"] for r in entry["rules"]}
    assert not any(p["id"] == "control:borrower-notification" for p in pending)  # seed controls are not proposals


def test_promote_dry_run_writes_nothing_and_apply_writes_the_tenant_overlay(pack_dir, store, capsys):
    control_id = propose(store)
    before = snapshot(pack_dir)
    assert ontology_proposals.main(["--promote", control_id], store) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "dry-run" and out["applied"] is False and out["pack_id"] == "tenant_t_one" and out["pack_created"]
    assert snapshot(pack_dir) == before and store.node(control_id)["attributes"]["needs_review"] is True

    assert ontology_proposals.main(["--promote", control_id, "--process", "process:rate-reset", "--apply"], store) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "apply" and out["applied"] is True
    overlay = pack_dir / "tenant_t_one.json"
    written = json.loads(overlay.read_text())
    assert written["version"] == "1.0.0" and written["capabilities"]["rate_switch_window"]["control"] == control_id
    assert written["capabilities"]["rate_switch_window"]["process"] == "process:rate-reset"
    assert snapshot(pack_dir).keys() == before.keys() | {"tenant_t_one.json"}
    assert {k: v for k, v in snapshot(pack_dir).items() if k != "tenant_t_one.json"} == before  # other packs untouched
    attrs = store.node(control_id)["attributes"]
    assert attrs["needs_review"] is False and attrs["promoted_to"] == "tenant_t_one"
    assert governance.pending_proposals(store) == []
    vocabulary = ontology.get_ontology(packs.load_profile(), tenant_ids=["t-one"])  # the overlay is live without a restart
    assert vocabulary.capabilities["rate_switch_window"] == control_id
    assert vocabulary.capability_processes["rate_switch_window"] == "process:rate-reset"
    # A promoted control is now an ontology candidate, and re-promoting it is refused (no longer pending review).
    canon = ControlCanonicaliser(store, EntityResolver(use_embeddings=False), use_llm=False)
    assert next(c for c in canon.candidates() if c.id == control_id).source == "ontology"
    assert ontology_proposals.main(["--promote", control_id, "--apply"], store) == 2


def test_promote_into_an_existing_pack_bumps_its_patch_version(pack_dir, store, capsys):
    control_id = propose(store)
    assert ontology_proposals.main(["--promote", control_id, "--pack", "retail_lending", "--apply"], store) == 0
    capsys.readouterr()
    written = json.loads((pack_dir / "retail_lending.json").read_text())
    assert written["version"] == "1.0.1" and "rate_switch_window" in written["capabilities"]
    assert set(OLD_CAPABILITIES) <= set(written["capabilities"])
    assert not (pack_dir / "tenant_t_one.json").exists()
    assert packs.load_all_packs()["retail_lending"].version == "1.0.1"


def test_promote_refuses_unknown_packs_missing_and_non_pending_controls(pack_dir, store, capsys):
    control_id = propose(store)
    before = snapshot(pack_dir)
    assert ontology_proposals.main(["--promote", control_id, "--pack", "no_such_pack", "--apply"], store) == 2
    assert "unknown pack id" in capsys.readouterr().err
    assert ontology_proposals.main(["--promote", "control:does-not-exist", "--apply"], store) == 2
    assert ontology_proposals.main(["--promote", "control:borrower-notification", "--apply"], store) == 2  # seed, not pending
    assert ontology_proposals.main(["--promote", control_id, "--process", "rate-reset", "--apply"], store) == 2  # bad process id
    assert snapshot(pack_dir) == before and store.node(control_id)["attributes"]["needs_review"] is True


def test_promote_refuses_a_capability_that_clashes_with_another_pack(pack_dir, store, capsys):
    store.upsert_node("control:fixed-rate-option-2", "Control", "Fixed Rate Option 2",
                      {"needs_review": True, "capability": "fixed_rate_option", "description": "a duplicate id with other content"})
    before = snapshot(pack_dir)
    assert ontology_proposals.main(["--promote", "control:fixed-rate-option-2", "--pack", "msme_lending", "--apply"], store) == 2
    assert "conflict" in capsys.readouterr().err.lower()
    assert snapshot(pack_dir) == before


def test_reject_keeps_the_node_for_audit_but_drops_it_from_candidates(pack_dir, store, capsys):
    control_id = propose(store)
    assert ontology_proposals.main(["--reject", control_id, "--reason", "not a control"], store) == 0
    assert json.loads(capsys.readouterr().out)["applied"] is False
    assert store.node(control_id)["attributes"]["needs_review"] is True  # dry run
    assert ontology_proposals.main(["--reject", control_id, "--reason", "not a control", "--apply"], store) == 0
    capsys.readouterr()
    attrs = store.node(control_id)["attributes"]
    assert attrs["rejected"] is True and attrs["rejection_reason"] == "not a control" and not attrs["needs_review"]
    assert governance.pending_proposals(store) == []
    canon = ControlCanonicaliser(store, EntityResolver(use_embeddings=False), use_llm=False)
    assert control_id not in {c.id for c in canon.candidates()}
    assert ontology_proposals.main(["--promote", control_id, "--apply"], store) == 2  # rejected stays rejected
    assert not (pack_dir / "tenant_t_one.json").exists()


def test_list_mode_prints_pending_proposals(store, capsys):
    control_id = propose(store)
    assert ontology_proposals.main([], store) == 0
    out = json.loads(capsys.readouterr().out)
    assert [p["id"] for p in out["pending"]] == [control_id] and out["tenant_id"] == "t-one"


# ---------------------------------------------------------------------------------------------------------------
# GET /api/ontology
# ---------------------------------------------------------------------------------------------------------------
def test_ontology_endpoint_lists_packs_vocabulary_and_pending_proposals(tmp_path):
    from fastapi.testclient import TestClient

    from api.auth import current_user
    from api.main import app
    from core.config import get_settings
    from knowledge_graph.store import set_graph_store

    store = LocalGraphStore(tmp_path / "api.db", get_settings().tenant_id)  # get_graph_store() keys on the settings tenant
    store.load_architecture()
    control_id = propose(store)
    route = next(r for r in app.routes if getattr(r, "path", "") == "/api/ontology")
    assert route.methods == {"GET"} and current_user in {d.call for d in route.dependant.dependencies}
    set_graph_store(store)
    try:
        with TestClient(app) as client:
            response = client.get("/api/ontology")
    finally:
        set_graph_store(None)
    assert response.status_code == 200
    body = response.json()
    by_id = {p["pack_id"]: p for p in body["packs"]}
    assert set(by_id) == LENDING_PACK_FILES and by_id["retail_lending"]["version"] == "1.0.0"
    assert by_id["retail_lending"]["counts"]["capabilities"] == 7 and by_id["msme_lending"]["counts"]["capabilities"] == 0
    assert "treasury_derivatives" in body["available_packs"]
    assert body["vocabulary"]["capabilities"] == OLD_CAPABILITIES and body["counts"]["capabilities"] == 7
    assert body["vocabulary"]["edge_types"]["IMPLEMENTS"] == "Policy -> Rule"
    assert [p["id"] for p in body["pending_proposals"]] == [control_id]
    pending = body["pending_proposals"][0]
    assert pending["name"] and pending["first_seen"] and pending["source_rule"] == "rule:FERR/1:a"
