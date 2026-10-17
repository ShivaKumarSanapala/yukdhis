"""record_assertion: the declarative per-record check. Guard (allow-list, injection attempts), validator, engine, back-compat.

No network, no LLM. The engine tests run against the synthetic bank in the local SQLite analyst database.
"""

import os
import sqlite3
import tempfile
from pathlib import Path

os.environ.update(YUKDHIS_RUNTIME_DIR=tempfile.mkdtemp(prefix="yukdhis-test-"), YUKDHIS_BACKEND="local",
                  AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import pytest  # noqa: E402

from bank_mcp_server import assertions as guard  # noqa: E402
from bank_mcp_server import secure_engine  # noqa: E402
from bank_mcp_server.assertions import AssertionRejected, compile_assertion, validate_check  # noqa: E402
from bank_mcp_server.operational_store import analyst_engine, build_sqlite  # noqa: E402
from bank_mcp_server.secure_engine import SecureEngine  # noqa: E402
from bank_mcp_server.sql_guard import SqlRejected, validate_sql  # noqa: E402
from core.config import get_settings  # noqa: E402
from core.models import (ComplianceCheck, ExtractedPolicy, Policy, ProductScope, Rule, SourceCitation)  # noqa: E402
from ingestion.documents import parse_document  # noqa: E402
from ingestion.templates import iracp_directions  # noqa: E402
from ingestion.validator import validate_extraction  # noqa: E402

AQ = "vw_asset_quality"
IRACP_JULY = Path("data/circulars/rbi_ecl_2025_11_28_iracp_directions_v2026_07_01.md")


def check(where, assertion, view=AQ, **extra) -> ComplianceCheck:
    return ComplianceCheck(kind="record_assertion", description="test", view=view, where=where, assertion=assertion, **extra)


def run(where, assertion, view=AQ, as_of="2026-10-17", **extra):
    return SecureEngine().check_data(check(where, assertion, view, **extra), as_of)


# ------------------------------------------------------------------------------------------------ the guard accepts
@pytest.mark.parametrize("where, assertion", [
    ("days_past_due > 90", "asset_classification IN ('SUBSTANDARD', 'DOUBTFUL', 'LOSS')"),
    ("asset_classification = 'DOUBTFUL' AND npa_age_months >= 24 AND npa_age_months < 48",
     "provision_held * 100 >= unsecured_portion * 100 + secured_portion * 40"),
    ("restructure_count = 1 AND exposure_class IN ('OTHER', 'MEDIUM_ENTERPRISE')",
     "provision_held * 10000 >= outstanding_balance * (40 + 500 * restructure_count)"),
    (None, "days_past_due BETWEEN 0 AND 100000"),
    ("secured = true", "unsecured_portion IS NOT NULL"),
    ("NOT (days_past_due = 0)", "overdue_amount / 100 >= 0"),
    ("npa_date IS NULL", "npa_age_months IS NULL"),
    ("restructure_date >= '2026-07-01'", "restructure_date <= :as_of"),
    ("((days_past_due > 1) OR (days_past_due < 0))", "-days_past_due <= 0"),
])
def test_guard_accepts_declarative_expressions(where, assertion):
    compiled = compile_assertion(AQ, where, assertion, as_of="2026-10-17")
    assert compiled.assertion is not None and isinstance(compiled.sql("sqlite")[1], str)


def test_as_of_is_replaced_by_a_date_literal_made_by_the_guard():
    compiled = compile_assertion("vw_kyc_status", None, "kyc_due_date >= :as_of", as_of="2026-10-17")
    assert compiled.sql("sqlite")[1] == "kyc_due_date >= '2026-10-17'"
    assert compiled.sql("postgres")[1] == "kyc_due_date >= '2026-10-17'"
    assert compiled.columns_used == {"kyc_due_date"}


def test_unknown_enum_value_is_a_warning_not_an_error():
    errors, warnings = validate_check(AQ, "asset_classification = 'NPA'", "provision_held >= 0")
    assert errors == [] and any("not a known value" in w for w in warnings)


# ------------------------------------------------------------------------------------------------ the guard rejects
INJECTIONS = [
    "1=1; DROP TABLE loans", "days_past_due > 1; SELECT 1", "days_past_due > 1 -- comment", "days_past_due > 1 /* c */",
    "days_past_due > 1 # c", "(SELECT 1) = 1", "days_past_due IN (SELECT days_past_due FROM loans)", "EXISTS (SELECT 1)",
    "days_past_due > 1 UNION SELECT 1", "SELECT 1", "DROP TABLE loans", "DELETE FROM loans", "UPDATE loans SET days_past_due = 0",
    "INSERT INTO loans VALUES (1)", "pg_sleep(10) > 0", "abs(days_past_due) > 1", "coalesce(days_past_due, 0) > 1",
    "CAST(days_past_due AS TEXT) = '1'", "days_past_due::text = '1'", "asset_classification LIKE 'S%'",
    "CASE WHEN secured THEN 1 ELSE 0 END = 1", "days_past_due % 2 = 0", "asset_classification || 'x' = 'y'",
    "loans.days_past_due > 1", "vw_asset_quality.days_past_due > 1", "main.loans.days_past_due > 1",
    "days_past_due > 1 OR load_extension('x') IS NULL", "randomblob(10) IS NOT NULL", "asset_classification = x'41'",
    "days_past_due > :other", "days_past_due > ?", "days_past_due > $1", "days_past_due > @x", "days_past_due > %(x)s",
    'days_past_due > "1"', '"days_past_due" > 1', "`days_past_due` > 1", "[days_past_due] > 1",
    "asset_classification = 'it''s'", "asset_classification = 'a;b'", "asset_classification = 'a\\b'",
    "asset_classification = '" + "x" * 41 + "'", "asset_classification = 'x(y)'", "asset_classification = 'x\ny'",
    "days_past_due > 1e999", "days_past_due > 99999999999999999999", "days_past_due > 1.5e3", "days_past_due > 0x10",
    "outstanding_balance / outstanding_balance > 1", "outstanding_balance / 0 > 1", "outstanding_balance / (0) > 1",
    "outstanding_balance / days_past_due > 1", "days_past_due", "days_past_due + 1", "'abc'", "1", "NOT", "", "   ",
    "asset_classification IN ('a', days_past_due)", "asset_classification IN ()", "days_past_due = 1 OR",
    "days_past_due IN (" + ", ".join(str(i) for i in range(21)) + ")",
    "days_past_due > 1" + " OR days_past_due > 1" * 200, "(" * 40 + "days_past_due > 1" + ")" * 40,
    "days_past_due ＞ 1", "ａsset_classification = 'x'", "days_past_due > 1\x00", "days_past_due > 1\x07",
    "days_past_due > 1 AND (SELECT 1 WHERE 1=1)", "EXTRACT(year FROM npa_date) > 2020", "npa_date + INTERVAL '1 day' > :as_of",
    "days_past_due > ALL (SELECT 1)", "days_past_due = ANY (ARRAY[1,2])", "ARRAY[1] = ARRAY[1]", "days_past_due ~ 1",
    "days_past_due @@ 1", "days_past_due IS DISTINCT FROM 1", "days_past_due = 1 ESCAPE 'x'",
]


@pytest.mark.parametrize("expression", INJECTIONS)
def test_guard_rejects_injection_and_everything_outside_the_allow_list(expression):
    if expression.strip():  # an empty `where` simply means "every row"
        with pytest.raises(AssertionRejected):
            compile_assertion(AQ, expression, "provision_held >= 0", as_of="2026-10-17")
    with pytest.raises(AssertionRejected):
        compile_assertion(AQ, None, expression, as_of="2026-10-17")


@pytest.mark.parametrize("expression", [
    "loan_ref = 'LR-0000001'", "customer_ref IS NOT NULL", "loan_id = 'LN-1001'", "full_name = 'x'", "email = 'a'", "phone = '1'",
    "pan = 'a'", "customer_id = 'x'", "aadhaar = '1'", "manager_name = 'x'", "date_of_birth > '2000-01-01'",
    "unknown_column > 1", "kyc_due_date >= :as_of",  # the second belongs to vw_kyc_status, not to the asset-quality view
])
def test_guard_rejects_pii_identifiers_and_columns_outside_the_view(expression):
    with pytest.raises(AssertionRejected):
        compile_assertion(AQ, expression, "provision_held >= 0")


@pytest.mark.parametrize("view", ["loans", "customers", "branches", "loan_events", "sqlite_master", "vw_unknown", "vw_asset_quality; DROP TABLE loans",
                                  "VW_ASSET_QUALITY", "", None, "vw_asset_quality, customers", "main.loans"])
def test_guard_only_reads_sanitized_views(view):
    with pytest.raises(AssertionRejected):
        compile_assertion(view, None, "days_past_due >= 0")


@pytest.mark.parametrize("assertion", [
    "days_past_due > 'abc'", "asset_classification > 5", "secured = 1", "secured = 'true'", "provision_held = true",
    "npa_date > 'yesterday'", "restructure_date = '2026-7-1'", "days_past_due + asset_classification > 1", "NOT days_past_due",
    "asset_classification AND secured",
])
def test_guard_type_checks_operands(assertion):
    with pytest.raises(AssertionRejected):
        compile_assertion(AQ, None, assertion)


@pytest.mark.parametrize("as_of", ["2026-10-17' OR '1'='1", "tomorrow", "2026-13-01", "2026-10-17; DROP TABLE loans", "", "17/10/2026"])
def test_as_of_must_be_an_iso_date(as_of):
    with pytest.raises(AssertionRejected):
        compile_assertion("vw_kyc_status", None, "kyc_due_date >= :as_of", as_of=as_of)


def test_assert_is_required_and_where_is_optional():
    with pytest.raises(AssertionRejected):
        compile_assertion(AQ, "days_past_due > 1", None)
    assert compile_assertion(AQ, None, "days_past_due >= 0").where is None


# ------------------------------------------------------------------------------------------------ validator and model
def policy_with(*checks: ComplianceCheck) -> ExtractedPolicy:
    rule = Rule(rule_id="rule:T/1:a", title="t", description="d", effective_date="2025-11-28", scope=ProductScope(product_terms=["loans and advances"]),
                checks=list(checks), citation=SourceCitation(document_id="doc:t", snippet="Banks shall classify an advance as NPA when overdue."))
    return ExtractedPolicy(policy=Policy(policy_id="policy:T/1", title="T", issuer="RBI", reference="T/1", effective_date="2025-11-28"), rules=[rule])


def test_validator_accepts_good_and_rejects_bad_assertions():
    good = validate_extraction(policy_with(check("days_past_due > 90", "asset_classification IN ('SUBSTANDARD', 'DOUBTFUL', 'LOSS')")))
    assert [i for i in good if i.severity == "ERROR"] == []
    bad = validate_extraction(policy_with(check("days_past_due > 90; DROP TABLE loans", "provision_held >= 0"),
                                          check(None, "loan_ref = 'x'"), check(None, "provision_held >= 0", view="loans"),
                                          check(None, None)))
    errors = [i for i in bad if i.severity == "ERROR"]
    assert len([i for i in errors if i.field.endswith(".assert")]) == 4
    assert {i.field for i in errors} == {"checks[0].assert", "checks[1].assert", "checks[2].assert", "checks[3].assert"}


def test_validator_warns_on_an_unknown_enum_value():
    issues = validate_extraction(policy_with(check("asset_classification = 'NPA'", "provision_held >= 0")))
    assert [i.severity for i in issues] == ["WARNING"] and "not a known value" in issues[0].message


def test_assert_spelling_loads_and_old_extractions_still_load():
    spelled = ComplianceCheck.model_validate({"kind": "record_assertion", "description": "d", "view": AQ, "where": "days_past_due > 1",
                                              "assert": "provision_held >= 0", "tolerance": 0.02})
    assert spelled.assertion == "provision_held >= 0" and spelled.tolerance == 0.02
    old = ComplianceCheck.model_validate({"kind": "record_flag", "description": "d", "record_field": "borrower_notified", "expected": True})
    assert old.view is None and old.assertion is None
    assert ComplianceCheck.model_validate({"kind": "event_deadline", "description": "d", "trigger_event": "LOAN_CLOSED",
                                           "required_event": "DOCUMENTS_RELEASED", "deadline_days": 30}).where is None
    with pytest.raises(ValueError):
        ComplianceCheck(kind="record_assertion", description="d", tolerance=1.5)


# ------------------------------------------------------------------------------------------------ the engine
def raw_count(where, assertion, view=AQ) -> tuple[int, int]:
    """Independent count straight on SQLite (no guard): (rows in scope, rows failing)."""
    analyst_engine()  # builds the operational database on first use
    path = get_settings().runtime_dir / "operational.db"
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        scope = f"({where})" if where else "1=1"
        in_scope = conn.execute(f"SELECT COUNT(*) FROM {view} WHERE {scope}").fetchone()[0]
        bad = conn.execute(f"SELECT COUNT(*) FROM {view} WHERE {scope} AND NOT COALESCE(({assertion}), 0)").fetchone()[0]
    return in_scope, bad


def test_engine_counts_match_an_unguarded_query_for_every_iracp_check():
    SecureEngine()  # builds the operational database
    extraction = iracp_directions(parse_document(IRACP_JULY.read_bytes(), IRACP_JULY.name))
    engine = SecureEngine()
    checked = 0
    for rule in extraction.rules:
        for c in rule.checks:
            result = engine.check_data(c, "2026-10-17", rule.rule_id)
            assert (result.in_scope, result.violations) == raw_count(c.where, c.assertion), c.description
            checked += 1
    assert checked >= 20


def test_status_tolerance_and_worklist():
    base = run("days_past_due > 90 AND exposure_class <> 'FARM_CREDIT'", "asset_classification IN ('SUBSTANDARD', 'DOUBTFUL', 'LOSS')")
    assert (base.status, base.violations) == ("GAP", 14) and base.worklist_id and base.in_scope == raw_count(
        "days_past_due > 90 AND exposure_class <> 'FARM_CREDIT'", "1=1")[0]
    assert sum(base.by_branch.values()) == 14 and set(base.by_branch) == {"Boring Road, Patna", "Adajan, Surat", "C-Scheme, Jaipur"}
    assert base.exposure_inr > 0 and f"14 of {base.in_scope} rows" in base.evidence
    tolerant = run("days_past_due > 90 AND exposure_class <> 'FARM_CREDIT'", "asset_classification IN ('SUBSTANDARD', 'DOUBTFUL', 'LOSS')", tolerance=0.2)
    assert tolerant.status == "COMPLIANT" and tolerant.violations == 14 and "within tolerance" in tolerant.evidence
    strict = run("days_past_due > 90 AND exposure_class <> 'FARM_CREDIT'", "asset_classification IN ('SUBSTANDARD', 'DOUBTFUL', 'LOSS')", tolerance=0.05)
    assert strict.status == "GAP"
    clean = run("asset_classification = 'LOSS'", "provision_held >= outstanding_balance")
    assert clean.status == "COMPLIANT" and clean.violations == 0 and clean.worklist_id is None
    items = secure_engine.get_app_store().get_worklist(base.worklist_id)
    assert items and {k for item in items for k in item} <= set(secure_engine.WORKLIST_KEYS)  # opaque references only
    assert all(item["loan_ref"].startswith("LR-") and item["customer_ref"].startswith("CUST-") for item in items)


def test_zero_rows_in_scope_is_not_evaluated_never_compliant():
    result = run("days_past_due > 100000", "provision_held >= 0")
    assert result.status == "NOT_EVALUATED" and result.in_scope == 0 and "nothing was tested" in result.evidence


def test_null_in_the_assertion_counts_as_a_violation():
    result = run(None, "npa_age_months >= 12")  # NULL for every account that is not an NPA
    in_scope, expected_bad = raw_count(None, "npa_age_months >= 12")
    assert result.violations == expected_bad and result.violations > 3000 and result.in_scope == in_scope


def test_customer_level_view_has_no_loan_columns_and_still_works():
    result = SecureEngine().check_data(ComplianceCheck(kind="record_assertion", description="re-KYC", view="vw_kyc_status",
                                                        assertion="kyc_due_date >= :as_of"), "2026-10-17")
    assert (result.status, result.in_scope, result.violations) == ("GAP", 2500, 22)
    assert result.by_product == {} and result.worklist_id
    earlier = SecureEngine().check_data(ComplianceCheck(kind="record_assertion", description="re-KYC", view="vw_kyc_status",
                                                         assertion="kyc_due_date >= :as_of"), "2020-01-01")
    assert earlier.violations == 0 and earlier.status == "COMPLIANT"  # nobody was overdue in 2020 on this snapshot


def test_a_rejected_check_is_never_executed():
    result = SecureEngine().check_data(ComplianceCheck(kind="record_assertion", description="x", view=AQ, assertion="1=1; DROP TABLE loans"),
                                       "2026-10-17")
    assert result.status == "NOT_EVALUATED" and "rejected by the guard" in result.evidence
    with analyst_engine().connect() as conn:
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM loans").scalar() > 3000  # still there


def test_row_cap_and_time_limit_fail_closed(monkeypatch):
    monkeypatch.setattr(secure_engine, "ASSERTION_ROW_CAP", 10)
    capped = run(None, "provision_held >= 0")
    assert capped.status == "NOT_EVALUATED" and "More than 10 rows" in capped.evidence
    monkeypatch.undo()
    monkeypatch.setattr(secure_engine, "ASSERTION_SECONDS", -1.0)
    slow = run(None, "provision_held * 100 + unsecured_portion * 100 + secured_portion * 25 >= outstanding_balance * 0")
    assert slow.status == "NOT_EVALUATED" and "could not be executed" in slow.evidence


def test_check_data_runs_only_record_assertions():
    result = SecureEngine().check_data(ComplianceCheck(kind="record_flag", description="x", record_field="borrower_notified", expected=True), "2026-10-17")
    assert result.status == "NOT_EVALUATED"


def test_analyst_connection_is_read_only_even_for_the_engine():
    with analyst_engine().connect() as conn, pytest.raises(Exception):
        conn.exec_driver_sql("DELETE FROM loans")


def test_ad_hoc_sql_can_read_the_new_views_but_the_aggregate_rules_still_apply():
    ok = validate_sql("SELECT asset_classification, COUNT(*) AS n FROM vw_asset_quality GROUP BY asset_classification")
    assert "vw_asset_quality" in ok.tables
    assert validate_sql("SELECT kyc_status, COUNT(*) FROM vw_kyc_status GROUP BY kyc_status").tables == ["vw_kyc_status"]
    for sql in ("SELECT * FROM vw_asset_quality", "SELECT loan_ref FROM vw_asset_quality", "SELECT COUNT(*) FROM loans",
                "SELECT customer_ref, COUNT(*) FROM vw_kyc_status GROUP BY customer_ref"):
        with pytest.raises(SqlRejected):
            validate_sql(sql)


def test_rules_with_assertions_run_end_to_end_as_of_and_are_upcoming_before_their_date():
    from core.app_store import get_app_store
    from pipeline.deterministic import run_pipeline

    name = IRACP_JULY.name
    state = run_pipeline({"path": str(IRACP_JULY), "filename": name}, "2026-10-17")
    report = get_app_store().get_report(state["report_id"])
    assert report["summary"]["overall_status"] == "NON_COMPLIANT" and report["summary"]["gaps"] >= 8
    early = run_pipeline({"path": str(IRACP_JULY), "filename": name}, "2026-06-01")
    statuses = {r["rule_id"].rsplit(":", 1)[-1]: r["status"] for r in get_app_store().get_report(early["report_id"])["results"]}
    assert statuses["restructured-again-income-cash-basis-139b"] == "UPCOMING" and statuses["npa-classification-90-days"] == "GAP"


def test_the_pipeline_database_matches_a_fresh_build(tmp_path):
    """The SQLite built for the tests equals one built from the committed CSVs: same view columns as the pack declares."""
    import json

    db = build_sqlite(tmp_path / "x.db")
    pack = json.loads(Path("data/ontology/packs/provisioning_iracp.json").read_text())
    with sqlite3.connect(db) as conn:
        for view, spec in pack["views"].items():
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({view})")]
            assert columns == list(spec["columns"]), view
    assert guard.MAX_NODES > 0


def test_customer_level_assertion_runs_through_the_pipeline_with_remediation_and_tokenized_samples():
    """A record_assertion on vw_kyc_status (no loan_ref column) must survive finalisation, action planning and sampling."""
    from core.app_store import get_app_store
    from pipeline import stages

    kyc = check(None, "kyc_due_date >= :as_of", view="vw_kyc_status")
    rule = Rule(rule_id="rule:KYC/1:periodic-updation", title="Periodic KYC updation", description="Re-KYC by the due date",
                effective_date="2016-02-25", scope=ProductScope(product_terms=["loans and advances"]), checks=[kyc],
                citation=SourceCitation(document_id="doc:kyc", snippet="Periodic updation of KYC is required."))
    policy = Policy(policy_id="policy:KYC/1", title="KYC", issuer="RBI", reference="KYC/1", effective_date="2016-02-25")
    state: dict = {"extraction": ExtractedPolicy(policy=policy, rules=[rule]).model_dump(), "as_of": "2026-10-17"}
    stages.stage_map(state)
    stages.stage_blast_radius(state)
    stages.stage_evaluate(state)
    stages.stage_finalize(state)
    report = get_app_store().get_report(state["report_id"])
    result = report["results"][0]
    assert (result["status"], result["in_scope"], result["violations"]) == ("GAP", 2500, 22)
    assert report["summary"]["loans_with_violations"] == 22 and report["summary"]["overall_status"] == "NON_COMPLIANT"
    assert [a["workflow_id"] for a in report["actions"]] == ["BRANCH_MANAGER_ALERT"]
    samples = SecureEngine().fetch_anonymized_samples(result["worklist_id"], 3, "s1")["samples"]
    assert len(samples) == 3 and all("loan_token" not in x and x["customer_token"] for x in samples)
    assert not any(k in x for x in samples for k in ("loan_ref", "customer_ref"))
