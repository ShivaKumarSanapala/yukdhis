"""The synthetic bank baseline: determinism, shape, planted exceptions, sanitized views, policy and control library, graph topology.

Everything is read from the committed files in data/bank and checked against an independent recomputation from the CSVs.
"""

import csv
import json
import os
import re
import sqlite3
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

os.environ.update(YUKDHIS_RUNTIME_DIR=tempfile.mkdtemp(prefix="yukdhis-test-"), YUKDHIS_BACKEND="local",
                  AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import pytest  # noqa: E402

from bank_mcp_server import control_library  # noqa: E402
from bank_mcp_server.operational_store import SQLITE_SCHEMA, VIEWS_SQL, build_sqlite  # noqa: E402
from bank_mcp_server.view_catalog import sanitized_views  # noqa: E402
from ingestion.documents import parse_document  # noqa: E402
from ingestion.templates import floating_rate_reset, iracp_amendment_2026, iracp_directions, property_document_release  # noqa: E402
from knowledge_graph import ontology  # noqa: E402
from knowledge_graph.store import LocalGraphStore  # noqa: E402
from scripts import generate_synthetic_bank as gen  # noqa: E402

BANK = Path("data/bank")
OPS = BANK / "operational"
PACK = Path("data/ontology/packs/provisioning_iracp.json")
CIRCULARS = Path("data/circulars")
REF = date(2026, 10, 17)


def rows(name: str) -> list[dict]:
    with (OPS / f"{name}.csv").open(newline="") as source:
        return list(csv.DictReader(source))


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((BANK / "manifest.json").read_text())


@pytest.fixture(scope="module")
def loans() -> list[dict]:
    return rows("loans")


@pytest.fixture(scope="module")
def customers() -> list[dict]:
    return rows("customers")


# ------------------------------------------------------------------------------------------------ determinism and shape
def test_generator_is_deterministic_and_the_committed_files_are_current(tmp_path):
    first, planted_first = gen.operational()
    second, planted_second = gen.operational()
    assert planted_first == planted_second
    for name, table in first.items():
        a, b = tmp_path / "a" / f"{name}.csv", tmp_path / "b" / f"{name}.csv"
        gen.write_csv(a, table)
        gen.write_csv(b, second[name])
        assert a.read_bytes() == b.read_bytes(), name  # same bytes twice
        assert a.read_bytes() == (OPS / f"{name}.csv").read_bytes(), f"{name}.csv is stale: run scripts/generate_synthetic_bank.py"
    assert json.dumps(gen.architecture(), indent=1) + "\n" == (BANK / "architecture.json").read_text()


def test_full_build_writes_identical_bytes_to_a_new_directory(tmp_path):
    manifest = gen.build(tmp_path / "bank", tmp_path / "pack.json")
    for name in ("architecture.json", "controls.json", "policies.json", "manifest.json", "operational/loans.csv", "operational/customers.csv",
                 "operational/loan_events.csv", "operational/branches.csv"):
        assert (tmp_path / "bank" / name).read_bytes() == (BANK / name).read_bytes(), name
    assert (tmp_path / "pack.json").read_bytes() == PACK.read_bytes()
    assert manifest == json.loads((BANK / "manifest.json").read_text())


def test_row_counts_and_sizes(manifest, loans, customers):
    assert manifest["rows"] == {"branches": 20, "customers": 2500, "loans": len(loans), "loan_events": len(rows("loan_events"))}
    assert 3900 <= len(loans) <= 4100 and 14000 <= manifest["rows"]["loan_events"] <= 17000
    assert len({r["region"] for r in rows("branches")}) == 4
    assert len({r["product_id"] for r in loans}) == 12
    assert sum(p.stat().st_size for p in OPS.glob("*.csv")) < 3_000_000  # sane for a repository
    assert len({r["loan_ref"] for r in loans}) == len(loans) and len({r["customer_ref"] for r in customers}) == len(customers)


def test_no_ecl_inputs_exist_anywhere():
    """PD, LGD and EAD stay absent by design: that is the planted readiness gap."""
    headers = {name: set(rows(name)[0]) for name in ("loans", "customers", "loan_events", "branches")}
    text = " ".join(c.lower() for cols in headers.values() for c in cols) + json.dumps(sanitized_views()).lower()
    for token in ("probability_of_default", "loss_given_default", "exposure_at_default", "pd_", "lgd", "ead_", "eir", "macro"):
        assert token not in re.sub(r"ecl_stage_proxy", "", text), token
    assert "ecl_stage_proxy" in headers["loans"]
    description = ontology_pack()["views"]["vw_asset_quality"]["columns"]["ecl_stage_proxy"]
    assert description.startswith("int: PROXY ONLY")
    # the proxy really is derived from days past due
    for r in rows("loans"):
        if r["status"] == "ACTIVE":
            dpd = int(r["days_past_due"])
            assert int(r["ecl_stage_proxy"]) == (1 if dpd <= 30 else 2 if dpd <= 90 else 3)


def test_synthetic_pii_only(customers):
    assert all(c["email"].endswith("@mail.example.com") for c in customers)
    assert all(re.fullmatch(r"\+91-90000\d{5}", c["phone"]) for c in customers)
    assert all(re.fullmatch(r"ZZ[A-Z]PX\d{4}Q", c["pan"]) for c in customers)
    assert all(b["manager_email"].endswith("@bank.example.com") for b in rows("branches"))


# ------------------------------------------------------------------------------------------------ IRACP rules recomputed independently
def active(loans):
    return [r for r in loans if r["status"] == "ACTIVE"]


def required(r: dict) -> int:
    """IRACP provisioning requirement, recomputed from the CSV row (paras 80, 81, 84A, 84B, 85, 86, 90, 91, 95)."""
    outstanding, security = int(r["outstanding_balance"]), int(r["security_value"])
    secured = min(security, outstanding)
    cls, months = r["asset_classification"], r["npa_age_months"]
    if cls in ("STANDARD", "SMA0", "SMA1", "SMA2"):
        base = 25 if r["exposure_class"] in ("INDIVIDUAL_HOUSING", "FARM_CREDIT", "SMALL_MICRO_ENTERPRISE") else 40
        return -(-outstanding * (base + 500 * int(r["restructure_count"])) // 10_000)
    if cls == "SUBSTANDARD":
        return -(-outstanding * (15 if r["secured"] == "true" else 25) // 100)
    if cls == "DOUBTFUL":
        rate = 25 if int(months) < 24 else 40 if int(months) < 48 else 100
        return -(-((outstanding - secured) * 100 + secured * rate) // 100)
    return outstanding


def test_baseline_is_mostly_compliant_and_the_exceptions_are_exactly_the_planted_ones(loans, customers, manifest):
    book = active(loans)
    planted = manifest["planted"]
    branch_of = {c["customer_id"]: c["branch_id"] for c in customers}
    # (1) classification follows DPD
    missed = [r for r in book if int(r["days_past_due"]) > 90 and r["asset_classification"] == "STANDARD"]
    assert len(missed) == planted["dpd_over_90_still_standard"]["count"] == 14
    assert dict(Counter(branch_of[r["customer_id"]] for r in missed)) == planted["dpd_over_90_still_standard"]["by_branch"]
    bands = {"SMA0": (1, 30), "SMA1": (31, 60), "SMA2": (61, 90)}
    mistags = [r for r in book if 1 <= int(r["days_past_due"]) <= 90
               and not bands[r["asset_classification"]][0] <= int(r["days_past_due"]) <= bands[r["asset_classification"]][1]]
    assert len(mistags) == planted["sma_band_mistags"]["count"] == 6
    # (2) provisions meet the requirement, except the shortfall accounts at two named branches
    short = [r for r in book if int(r["provision_held"]) < required(r)]
    assert len(short) == planted["npa_provision_shortfall"]["count"] == 16
    assert dict(Counter(branch_of[r["customer_id"]] for r in short)) == {"branch:pune-kothrud": 10, "branch:lko-hazratganj": 6}
    assert all(r["asset_classification"] in ("SUBSTANDARD", "DOUBTFUL") for r in short)
    assert all(int(r["provision_required"]) == required(r) for r in book), "the generator's provision_required follows the Directions"
    # (3) interest recognition: cash basis for NPAs; re-restructured accounts on cash basis except the planted five
    assert all(r["interest_recognition"] == "CASH" for r in book if r["asset_classification"] in ("SUBSTANDARD", "DOUBTFUL", "LOSS"))
    repeat_accrual = [r for r in book if int(r["restructure_count"]) >= 2 and r["interest_recognition"] != "CASH"]
    assert len(repeat_accrual) == planted["repeat_restructured_on_accrual"]["count"] == 5
    assert all(r["interest_recognition"] == "ACCRUAL" for r in book if int(r["restructure_count"]) == 1)
    assert min(r["restructure_date"] for r in book if r["restructured"] == "true") >= "2026-07-01"  # after the amendment took effect
    # (4) KYC
    overdue = [c for c in customers if date.fromisoformat(c["kyc_due_date"]) < REF]
    assert len(overdue) == planted["kyc_periodic_updation_overdue"]["count"] == 22 and all(c["kyc_status"] == "OVERDUE" for c in overdue)
    years = {"HIGH": 2, "MEDIUM": 8, "LOW": 10}
    for c in customers:
        last, due = date.fromisoformat(c["last_kyc_review"]), date.fromisoformat(c["kyc_due_date"])
        assert due.year - last.year == years[c["kyc_risk_category"]]
    # overall: well over 97 per cent of active accounts have no IRACP exception
    exceptions = {id(r) for r in missed + mistags + short + repeat_accrual}
    assert 0.97 < 1 - len(exceptions) / len(book) < 0.995  # 41 of 3,550 active accounts
    assert 0.025 < sum(r["asset_classification"] in ("SUBSTANDARD", "DOUBTFUL", "LOSS") for r in book) / len(book) < 0.04


def test_asset_classification_definitions(loans):
    for r in active(loans):
        cls, dpd, months = r["asset_classification"], int(r["days_past_due"]), r["npa_age_months"]
        if cls in ("SUBSTANDARD", "DOUBTFUL", "LOSS"):
            assert dpd > 90 and r["npa_date"] and months != ""
            assert (int(months) < 12) == (cls == "SUBSTANDARD") or cls == "LOSS"  # definitions 5(12) and 5(2)
            assert date.fromisoformat(r["npa_date"]) <= REF
        else:
            assert r["npa_date"] == "" and months == ""
        if cls == "LOSS":  # para 68(2): security below 10 per cent of the outstanding is ignored
            assert int(r["security_value"]) < 0.1 * int(r["outstanding_balance"])
        if r["secured"] == "false":
            assert int(r["security_value"]) == 0
    assert {r["asset_classification"] for r in active(loans)} == {"STANDARD", "SMA0", "SMA1", "SMA2", "SUBSTANDARD", "DOUBTFUL", "LOSS"}
    assert all(r["asset_classification"] == "" and r["provision_held"] == "0" for r in loans if r["status"] == "CLOSED")


def test_planted_rate_reset_and_document_release_scenarios_are_unchanged(manifest, loans):
    assert manifest["planted"]["rbi_2023_24_60_document_release_violations"] == 12 and manifest["planted"]["of_which_koramangala"] == 7
    scope = [r for r in loans if r["status"] == "ACTIVE" and r["rate_reset_due"] and r["product_id"] != "product:msme-term-loan"]
    reset = manifest["planted"]["rbi_2023_24_55_rate_reset"]
    assert len(scope) == reset["floating_emi_loans_in_scope"]
    assert sum(r["borrower_notified"] == "false" for r in scope) == reset["borrowers_not_notified_of_reset"] == 42
    assert sum(r["statement_available"] == "false" for r in scope) == reset["loans_without_reset_statement"]
    assert sum(r["has_fixed_rate_option"] == "false" for r in scope) == reset["auto_floating_loans_without_fixed_rate_option"]


# ------------------------------------------------------------------------------------------------ views and schemas
def ontology_pack() -> dict:
    return json.loads(PACK.read_text())


def test_views_have_no_pii_and_match_the_pack_and_the_cloud_sql_schema(tmp_path):
    db = build_sqlite(tmp_path / "bank.db")
    pack = ontology_pack()
    schema = Path("infrastructure/cloudsql/schema.sql").read_text()
    with sqlite3.connect(db) as conn:
        for view in ("vw_compliance_impact", "vw_loan_events", "vw_branch_directory", "vw_asset_quality", "vw_kyc_status"):
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({view})")]
            assert not set(columns) & ontology.PII_COLUMNS, view
            assert not {"full_name", "email", "phone", "pan", "customer_id", "manager_name", "manager_email"} & set(columns)
            body = re.sub(r"\s+", " ", re.search(rf"CREATE OR REPLACE VIEW {view} AS\s*(SELECT.*?);\n", schema, re.S).group(1))
            select_list = re.match(r"SELECT (.*?) FROM ", body).group(1)
            in_schema = [re.split(r"\s+AS\s+|\.", item.strip())[-1] for item in select_list.split(", ")]
            assert in_schema == columns, view  # the PostgreSQL view exposes the same columns, in the same order
            if view in pack["views"]:
                assert columns == list(pack["views"][view]["columns"])
        loan_columns = [row[1] for row in conn.execute("PRAGMA table_info(loans)")]
        customer_columns = [row[1] for row in conn.execute("PRAGMA table_info(customers)")]
    assert loan_columns == list(rows("loans")[0]) and customer_columns == list(rows("customers")[0])
    for column in loan_columns[15:] + customer_columns[8:]:
        assert re.search(rf"ADD COLUMN IF NOT EXISTS {column}\b", schema), f"{column} needs an ALTER TABLE for databases created earlier"
    assert "vw_asset_quality, vw_kyc_status TO compliance_analyst" in schema
    assert "GRANT SELECT" in schema and "FROM customers c" in schema and "pii_hydrator" in schema
    for forbidden in ("full_name", "email", "pan", "phone"):
        views_section = schema.split("CREATE OR REPLACE VIEW vw_compliance_impact")[1].split("DO $$")[0]
        assert not re.search(rf"\b[lc]\.{forbidden}\b", views_section)


def test_sqlite_loader_types_the_new_columns(tmp_path):
    from bank_mcp_server.operational_store import load_rows

    sample = next(r for r in load_rows("loans") if r["asset_classification"] == "DOUBTFUL")
    assert isinstance(sample["days_past_due"], int) and isinstance(sample["secured"], bool) and isinstance(sample["restructured"], bool)
    assert isinstance(sample["npa_age_months"], int) and isinstance(sample["provision_held"], int)
    performing = next(r for r in load_rows("loans") if r["asset_classification"] == "STANDARD")
    assert performing["npa_date"] is None and performing["npa_age_months"] is None
    assert "days_past_due INTEGER" in SQLITE_SCHEMA and "vw_kyc_status" in VIEWS_SQL


def test_cloud_sql_statements_split_cleanly_for_the_seed_job():
    """scripts/seed_cloudsql.py splits the schema on ';\\n' and executes each statement: none may be empty or half a statement."""
    schema = Path("infrastructure/cloudsql/schema.sql").read_text()
    statements = []
    for statement in schema.split(";\n"):
        sql = "\n".join(line for line in statement.splitlines() if not line.lstrip().startswith("--")).strip()
        if sql:
            statements.append(sql)
    assert all(re.match(r"(CREATE|ALTER|DO|REVOKE|GRANT)\b", s) for s in statements)
    assert sum(s.startswith("ALTER TABLE") for s in statements) == 19


# ------------------------------------------------------------------------------------------------ the ontology pack
def test_pack_has_the_agreed_shape_and_redefines_nothing():
    pack = ontology_pack()
    assert {"pack_id", "version", "title", "domain", "business_line", "requires_data_domains", "capabilities", "record_flags", "event_types",
            "event_processes", "product_attributes", "views", "glossary", "workflows", "applicability_signals"} <= set(pack)
    assert pack["pack_id"] == "provisioning_iracp" and pack["domain"] == "asset_quality" and pack["business_line"] == "retail_lending"
    assert set(pack["applicability_signals"]) == {"keywords", "business_line", "data_domains"}
    for cap_id, cap in pack["capabilities"].items():
        assert set(cap) == {"control", "description", "process", "synonyms"} and cap["control"].startswith("control:"), cap_id
        assert cap["process"] is None or cap["process"].startswith("process:")
    for view, spec in pack["views"].items():
        assert set(spec) == {"description", "columns"} and all(isinstance(d, str) for d in spec["columns"].values())
    # nothing already defined in the retail-lending vocabulary
    assert not set(pack["capabilities"]) & set(ontology.CAPABILITIES)
    assert not set(pack["record_flags"]) & set(ontology.RECORD_FLAGS) and not set(pack["event_types"]) & ontology.EVENT_TYPES
    assert not set(pack["views"]) & set(ontology.SANITIZED_VIEWS) and not set(pack["glossary"]) & set(ontology.REGULATORY_GLOSSARY)
    assert not set(pack["workflows"]) & set(ontology.WORKFLOWS) and not set(pack["event_processes"]) & set(ontology.EVENT_PROCESSES)
    assert set(pack["event_processes"]) <= set(pack["event_types"])
    # every capability resolves to a control and a process of the architecture
    architecture = json.loads((BANK / "architecture.json").read_text())
    ids = {n["id"] for n in architecture["nodes"]}
    assert all(c["control"] in ids for c in pack["capabilities"].values())
    assert all(c["process"] in ids for c in pack["capabilities"].values() if c["process"])
    assert all(p in ids for p in pack["event_processes"].values())
    # the record flags are real boolean columns of vw_compliance_impact
    with sqlite3.connect(":memory:") as conn:
        conn.executescript(SQLITE_SCHEMA + VIEWS_SQL)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(vw_compliance_impact)")}
    assert set(pack["record_flags"]) <= columns


def test_pack_views_are_visible_to_the_sql_guard_catalog():
    views = sanitized_views()
    assert {"vw_asset_quality", "vw_kyc_status", "vw_compliance_impact"} <= set(views)
    assert "days_past_due" in views["vw_asset_quality"]["columns"]


# ------------------------------------------------------------------------------------------------ policy and control library
@pytest.fixture(scope="module")
def library():
    return {"controls": control_library.controls(), "policies": control_library.policies(), "instruments": control_library.instruments()}


def test_control_library_shape_and_ecl_readiness_gap(library):
    controls = library["controls"]
    assert 40 <= len(controls) <= 60 and len({c["id"] for c in controls}) == len(controls)
    for c in controls:
        assert c["implementation_status"] in {"implemented", "partial", "planned", "absent"}
        assert c["owner_team"].startswith("team:") and c["description"]
        if c["implementation_status"] in {"implemented", "partial"}:
            assert c["implemented_from"] and c["last_tested"] and c["last_test_result"] in {"pass", "exceptions"}
            assert c["last_tested"] <= "2026-10-17" and c["implemented_from"] <= "2026-10-17"
        if c["implementation_status"] == "planned":
            assert c["planned_go_live"] and c["planned_go_live"] > "2026-10-17"
    by_name = {c["name"]: c for c in controls}
    for name in ("ECL Staging Engine (Stages 1, 2, 3)", "SICR Criteria Documentation", "Probability of Default Models", "Loss Given Default Models",
                 "Exposure at Default Models", "Effective Interest Rate Computation", "Macroeconomic Scenario Framework",
                 "ECL Model Validation Framework", "Prudential Floor Calculator"):
        assert by_name[name]["implementation_status"] in {"absent", "planned"}, name
    for name in ("Automated Day-end NPA Classification", "SMA Day-end Tagging (SMA-0, SMA-1, SMA-2)", "Standard Asset Provisioning Engine",
                 "NPA Provisioning Engine", "Income Recognition on Cash Basis for NPAs", "Additional Provision for Resolution Plans (5% per plan)"):
        assert by_name[name]["implementation_status"] == "implemented", name
    assert Counter(c["implementation_status"] for c in controls).keys() == {"implemented", "partial", "planned", "absent"}


def test_acpir_gives_readiness_gaps_and_the_same_controls_become_gaps_in_force():
    upcoming = control_library.readiness("RBI/DOR/2026-27/398", "2026-10-17")
    assert upcoming["phase"] == "upcoming" and len(upcoming["gaps"]) == 16 and len(upcoming["ready"]) == 6
    assert all(g["status"] in {"absent", "planned"} for g in upcoming["gaps"])
    in_force = control_library.readiness("acpir", "2027-04-02")
    assert in_force["phase"] == "in_force" and in_force["gaps"] == upcoming["gaps"]
    # the incurred-loss controls it carries over are not gaps; the old regime has no readiness gap at all
    assert control_library.readiness("iracp", "2026-10-17")["gaps"] and all(g["status"] == "partial" for g in control_library.readiness("iracp", "2026-10-17")["gaps"])
    with pytest.raises(KeyError):
        control_library.readiness("nonexistent", "2026-10-17")


def test_time_travel_of_controls():
    register = next(c for c in control_library.controls() if c["id"] == "control:resolution-plan-provisioning")
    assert control_library.status_on(register, "2026-06-30") == "absent" and control_library.status_on(register, "2026-07-01") == "implemented"
    kyc = next(c for c in control_library.controls() if c["id"] == "control:kyc-periodic-updation")
    assert kyc["implemented_from"] == "2026-01-01" and control_library.status_on(kyc, "2025-12-31") == "absent"


def test_policies_map_to_real_instruments_paragraphs_and_controls(library):
    policies, instruments = library["policies"], library["instruments"]
    control_ids = {c["id"] for c in library["controls"]}
    assert len(policies) >= 12 and len({p["policy_id"] for p in policies}) == len(policies)
    titles = {p["title"] for p in policies}
    for needed in ("Credit Policy", "Asset Classification and Provisioning Policy", "Fair Practices Code", "Rate Reset and Disclosure Policy",
                   "Collateral and Document Custody Policy", "Customer Grievance Policy", "KYC and AML Policy", "Model Risk Management Policy",
                   "Recovery and Restructuring Policy", "Outsourcing Policy"):
        assert needed in titles
    corpus = {key: (CIRCULARS / spec["corpus_file"]).read_text() for key, spec in instruments.items() if spec["in_corpus"]}
    for policy in policies:
        assert policy["owner_team"].startswith("team:") and policy["effective_date"] <= policy["approved_on"] or policy["approved_on"] < policy["effective_date"]
        assert policy["clauses"]
        for clause in policy["clauses"]:
            assert clause["clause_id"].startswith(policy["code"]) and clause["effective_from"] and clause["owner_team"].startswith("team:")
            assert set(clause["controls"]) <= control_ids
            for mapping in clause["maps_to"]:
                assert mapping["instrument"] in instruments
                if instruments[mapping["instrument"]]["in_corpus"] and instruments[mapping["instrument"]]["corpus_file"].startswith("rbi_ecl"):
                    for paragraph in mapping["paragraphs"]:  # a paragraph reference must exist in the real document
                        number = re.match(r"\d+[A-Z]?", paragraph).group(0)
                        pattern = rf"(?m)^(?:\d\[)?{number}\.(?:\s|\d)" if not paragraph.startswith("3(") else r"3\. These Amendment Directions"
                        assert re.search(pattern, corpus[mapping["instrument"]]), (policy["code"], mapping["instrument"], paragraph)


def test_every_control_satisfies_something_real_and_its_data_check_exists(library):
    instruments = library["instruments"]
    iracp_july = parse_document((CIRCULARS / instruments["iracp"]["corpus_file"]).read_bytes(), "x.md")
    rules = {r.rule_id.rsplit(":", 1)[-1]: r for r in iracp_directions(iracp_july).rules}
    for c in library["controls"]:
        assert all(s["instrument"] in instruments for s in c["satisfies"])
        check = c["data_check"]
        if not check:
            continue
        assert check["instrument"] in instruments
        if check["rule"]:
            assert check["rule"] in rules and {x.view for x in rules[check["rule"]].checks} == {check["view"]}
        else:
            assert check["expression"]["assert"] and check["view"] == "vw_kyc_status"


def test_policy_rule_mappings_name_rules_the_templates_produce(library):
    instruments = library["instruments"]
    produced = {}
    for key, build in (("iracp", iracp_directions), ("iracp_amend_2026", iracp_amendment_2026), ("floating_rate_reset", floating_rate_reset),
                       ("document_release", property_document_release)):
        path = CIRCULARS / instruments[key]["corpus_file"]
        produced[key] = {r.rule_id for r in build(parse_document(path.read_bytes(), path.name)).rules}
        assert all(rid.startswith(f"rule:{instruments[key]['reference']}:") for rid in produced[key])
    count = 0
    for policy in library["policies"]:
        for clause in policy["clauses"]:
            for mapping in clause["maps_to"]:
                for suffix in mapping["rules"]:
                    assert f"rule:{instruments[mapping['instrument']]['reference']}:{suffix}" in produced[mapping["instrument"]], (clause["clause_id"], suffix)
                    count += 1
    assert count >= 25


# ------------------------------------------------------------------------------------------------ graph topology
def test_architecture_has_internal_policies_controls_and_bitemporal_edges(tmp_path):
    architecture = json.loads((BANK / "architecture.json").read_text())
    nodes = {n["id"]: n for n in architecture["nodes"]}
    types = Counter(n["type"] for n in architecture["nodes"])
    assert types["Product"] == 12 and types["Branch"] == 20 and types["Control"] >= 40 and types["Policy"] >= 12
    internal = [n for n in nodes.values() if n["type"] == "Policy"]
    assert all(n["attributes"]["kind"] == "internal" and n["attributes"]["clauses"] for n in internal)
    edge_types = Counter(e["type"] for e in architecture["edges"])
    assert {"IMPLEMENTS", "ENFORCES", "PROVIDES", "USES", "OWNED_BY", "ELIGIBLE_FOR"} == set(edge_types)
    for e in architecture["edges"]:
        assert e["source"] in nodes, e
        if e["type"] != "IMPLEMENTS":  # IMPLEMENTS targets Rule nodes that exist once the circular has been ingested
            assert e["target"] in nodes, e
        else:
            assert e["target"].startswith("rule:") and nodes[e["source"]]["attributes"]["kind"] == "internal"
        if e["type"] == "ENFORCES":
            assert nodes[e["source"]]["type"] == "Policy" and nodes[e["target"]]["type"] == "Control"
    # absent controls are enforced by nobody; planned ones only from their go-live date
    absent = {c["id"] for c in control_library.controls() if c["implementation_status"] == "absent"}
    assert not [e for e in architecture["edges"] if e["target"] in absent and e["type"] in {"ENFORCES", "PROVIDES"}]
    planned = {c["id"]: c["planned_go_live"] for c in control_library.controls() if c["implementation_status"] == "planned"}
    assert all(e["valid_from"] == planned[e["target"]] for e in architecture["edges"] if e["type"] == "ENFORCES" and e["target"] in planned)
    # the store accepts it, and time travel works
    store = LocalGraphStore(tmp_path / "g.db", "t")
    store.load_architecture(BANK / "architecture.json")
    kyc_policy = "policy:POL-KYC-007"
    before = {e["target_id"] for e in store.neighbours([kyc_policy], ["ENFORCES"], "2025-12-31")}
    after = {e["target_id"] for e in store.neighbours([kyc_policy], ["ENFORCES"], "2026-01-02")}
    assert "control:kyc-periodic-updation" not in before and "control:kyc-periodic-updation" in after
    provided_before = {e["target_id"] for e in store.neighbours(["product:home-loan-floating"], ["PROVIDES"], "2026-06-30")}
    provided_after = {e["target_id"] for e in store.neighbours(["product:home-loan-floating"], ["PROVIDES"], "2026-07-02")}
    assert "control:resolution-plan-provisioning" not in provided_before and "control:resolution-plan-provisioning" in provided_after
    assert "control:ecl-staging-engine" not in provided_after
    assert {e["target_id"] for e in store.neighbours(["product:auto-loan-floating"], ["PROVIDES"], "2026-10-17")} & {"control:fixed-rate-option"} == set()
    assert "control:fixed-rate-option" in {e["target_id"] for e in store.neighbours(["product:auto-loan-floating"], ["PROVIDES"], "2024-01-15")}


def test_spanner_seed_accepts_the_new_node_attributes():
    """seed_spanner_graph.py json-dumps node and edge attributes: every one must serialise."""
    architecture = json.loads((BANK / "architecture.json").read_text())
    for n in architecture["nodes"]:
        assert len(n["id"]) <= 256 and len(n["name"]) <= 512
        json.dumps(n["attributes"])
    for e in architecture["edges"]:
        assert len(e["type"]) <= 64 and date.fromisoformat(e["valid_from"]) and json.dumps(e["attributes"]) is not None
        assert e["valid_to"] is None or date.fromisoformat(e["valid_to"])
    assert len(architecture["edges"]) < 5000  # one Spanner batch


def test_reference_to_other_checks_is_in_sync_with_design_of_four_checks():
    """The legacy check kinds keep working on the new bank (the old planted scenarios are asserted in test_engine.py)."""
    assert {"borrower_notified", "has_fixed_rate_option", "has_emi_tenor_choice", "statement_available"} <= set(ontology.RECORD_FLAGS)


def test_internal_policies_light_up_once_the_circular_is_ingested():
    """IMPLEMENTS edges point at Rule nodes that appear when the IRACP Directions are ingested: the blast radius then names the bank's policy."""
    from knowledge_graph.store import get_graph_store
    from pipeline.deterministic import run_pipeline

    name = "rbi_ecl_2025_11_28_iracp_directions_v2026_07_01.md"
    state = run_pipeline({"path": str(CIRCULARS / name), "filename": name}, "2026-10-17")
    store = get_graph_store()
    implementing = store.neighbours(["rule:RBI/DOR/2025-26/164:npa-classification-90-days"], ["IMPLEMENTS"], "2026-10-17", direction="in")
    assert [e["source_id"] for e in implementing] == ["policy:POL-ACP-002"] and implementing[0]["node"]["attributes"]["kind"] == "internal"
    assert all(m["scope_status"] == "RESOLVED" and len(m["product_ids"]) == 12 for m in state["mappings"])  # "loans and advances" = the whole book
    amended = store.neighbours(["rule:RBI/DOR/2025-26/164:resolution-plan-additional-provision-84a"], ["IMPLEMENTS"], "2026-06-30", direction="in")
    assert amended == []  # the clause only takes effect on 2026-07-01
