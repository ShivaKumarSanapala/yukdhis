"""Bank-baseline eval suite: run documents through the pipeline against the synthetic bank and score the result.

Each case in ``data/evals/cases.json`` names a document, an analysis date and what the planted bank should produce: the document
type, the roll-up status, the exact planted findings (violations and branches), which rules are compliant, upcoming or gated,
the readiness gaps taken from the control library, and whether any remediation action may be proposed. The suite also runs the
data checks that evidence the library's controls and compares the counts with the planted exceptions.

    python scripts/run_eval_suite.py                    # deterministic pipeline (no LLM, no network)
    python scripts/run_eval_suite.py --only iracp       # cases whose id contains "iracp"
    python scripts/run_eval_suite.py --llm              # agentic extraction through Gemini (needs GCP; uses llm_expected overrides)
    python scripts/run_eval_suite.py --list

It prints a scorecard (PASS or FAIL per case, a diff for every failed assertion) and exits 1 when any assertion fails.
Tests call ``run_suite`` for the deterministic subset (tests/test_eval_suite.py).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CASES_FILE = ROOT / "data" / "evals" / "cases.json"
CIRCULARS = ROOT / "data" / "circulars"


@dataclass
class Assertion:
    name: str
    ok: bool
    expected: object = None
    actual: object = None


@dataclass
class CaseResult:
    case_id: str
    assertions: list[Assertion] = field(default_factory=list)
    error: str | None = None
    skipped: str | None = None

    @property
    def failures(self) -> list[Assertion]:
        return [a for a in self.assertions if not a.ok]

    @property
    def passed(self) -> bool:
        return self.error is None and not self.failures


def load_cases(path: Path = CASES_FILE) -> dict:
    return json.loads(path.read_text())


def _expect(result: CaseResult, name: str, expected, actual) -> None:
    result.assertions.append(Assertion(name, expected == actual, expected, actual))


def _hint(rule_id: str, hint: str) -> bool:
    return hint in rule_id


def _classify(document_path: Path) -> str:
    from ingestion.classifier import classify_document
    from ingestion.documents import parse_document

    return classify_document(parse_document(document_path.read_bytes(), document_path.name), use_llm=False).doc_type


def _run_pipeline(case: dict) -> tuple[dict | None, str | None]:
    """The deterministic runner (or Gemini extraction when AGENT_MODE=agentic). Returns (report, blocked reason)."""
    from core.app_store import get_app_store
    from pipeline.deterministic import run_pipeline

    preset = None
    if case.get("preset"):
        body = json.loads((ROOT / case["preset"]).read_text())
        preset = {k: body[k] for k in ("extraction", "validation_issues", "extraction_provenance") if k in body}
        source = {"filename": Path(case["preset"]).name, "path": str(ROOT / case["preset"])}
    else:
        source = {"path": str(CIRCULARS / case["document"]), "filename": case["document"]}
    try:
        state = run_pipeline(source, case["as_of"], preset=preset)
    except ValueError as error:  # document-type gate, or no template with the LLM off
        return None, str(error)
    return get_app_store().get_report(state["report_id"]), None


def _selected(results: list[dict], hint: str, check_index: int | None = None) -> list[dict]:
    return [r for r in results if _hint(r["rule_id"], hint) and (check_index is None or r["check_index"] == check_index)]


def evaluate_report(result: CaseResult, expected: dict, report: dict) -> None:
    summary, results = report["summary"], report["results"]
    if "overall_status" in expected:
        _expect(result, "overall_status", expected["overall_status"], summary["overall_status"])
    if "unexecutable_expected" in expected:
        _expect(result, "unexecutable_expected", expected["unexecutable_expected"], summary["unexecutable_obligations"] > 0)
    if "unexecutable_count" in expected:
        _expect(result, "unexecutable_count", expected["unexecutable_count"], summary["unexecutable_obligations"])
    if "rule_suffixes_exactly" in expected:
        _expect(result, "extracted rules (only these)", sorted(expected["rule_suffixes_exactly"]),
                sorted(r["rule_id"].rsplit(":", 1)[-1] for r in report["rules"]))
    if "rule_count" in expected:
        _expect(result, "rule_count", expected["rule_count"], summary["rules"])
    for finding in expected.get("planted_findings", []):
        picked = _selected(results, finding["rule_hint"], finding.get("check_index"))
        label = f"finding {finding['rule_hint']}" + (f"[check {finding['check_index']}]" if finding.get("check_index") is not None else "")
        _expect(result, f"{label}: violations", finding["violations"], sum(r["violations"] for r in picked) if picked else None)
        if "status" in finding:
            _expect(result, f"{label}: status", finding["status"], sorted({r["status"] for r in picked}) if picked else None)
        if "branches_include" in finding:  # the designed concentration; the other branches are drawn at random
            branches = Counter()
            for r in picked:
                branches.update(r["by_branch"])
            _expect(result, f"{label}: branches (designed concentration)", dict(sorted(finding["branches_include"].items())),
                    {b: branches.get(b) for b in sorted(finding["branches_include"])})
        if "branches" in finding:
            branches: Counter = Counter()
            for r in picked:
                branches.update(r["by_branch"])
            _expect(result, f"{label}: branches", dict(sorted(finding["branches"].items())), dict(sorted(branches.items())))
    for key, status in (("compliant_rules", "COMPLIANT"), ("upcoming_rules", "UPCOMING"), ("not_evaluated_rules", "NOT_EVALUATED")):
        for hint in expected.get(key, []):
            picked = _selected(results, hint)
            _expect(result, f"{hint}: every check {status}", [status] * len(picked) if picked else ["(no such rule)"],
                    [r["status"] for r in picked] if picked else ["(no such rule)"])
    gate = {a["rule_id"]: a for a in report.get("applicability", [])}
    for hint, kind in expected.get("gated", {}).items():
        applicable = {"not_applicable": False, "cannot_evaluate": None}[kind]
        picked = [a for rid, a in gate.items() if _hint(rid, hint)]
        _expect(result, f"{hint}: applicability", [applicable] if picked else ["(no such rule)"],
                [a["applicable"] for a in picked] if picked else ["(no such rule)"])
    for hint, missing in expected.get("missing_data", {}).items():
        picked = [a for rid, a in gate.items() if _hint(rid, hint)]
        have = sorted({m.split(":")[0] for a in picked for m in a["missing_data"]})
        _expect(result, f"{hint}: missing data domains", sorted(missing), have)
    if "no_actions" in expected:
        actions = report["actions"]
        _expect(result, "no_actions" if expected["no_actions"] else "actions proposed", expected["no_actions"], not actions)
    if "actions_workflows" in expected:
        _expect(result, "action workflows", sorted(set(expected["actions_workflows"])), sorted({a["workflow_id"] for a in report["actions"]}))


def evaluate_readiness(result: CaseResult, case: dict, expected: dict) -> None:
    from bank_mcp_server import control_library

    readiness = control_library.readiness(case["instrument"], case["as_of"])
    if "readiness_phase" in expected:
        _expect(result, "readiness phase", expected["readiness_phase"], readiness["phase"])
    if "readiness_gaps" in expected:
        _expect(result, "readiness gaps (controls)", sorted(expected["readiness_gaps"]), sorted(g["control"] for g in readiness["gaps"]))
    if "readiness_ready" in expected:
        _expect(result, "controls already ready", sorted(expected["readiness_ready"]), sorted(readiness["ready"]))
    if "readiness_gap_status" in expected:
        _expect(result, "readiness gap status", dict(sorted(expected["readiness_gap_status"].items())),
                dict(sorted({g["control"]: g["status"] for g in readiness["gaps"]}.items())))


def run_case(case: dict, llm: bool = False) -> CaseResult:
    result = CaseResult(case["id"])
    if llm and "llm" not in case.get("modes", ["deterministic"]):
        result.skipped = "no agentic expectation"
        return result
    if not llm and "deterministic" not in case.get("modes", ["deterministic"]):
        result.skipped = "agentic only"
        return result
    expected = {**case["expected"], **(case.get("llm_expected", {}) if llm else {})}
    try:
        if case.get("document"):
            _expect(result, "doc_type (deterministic classifier)", expected["doc_type"], _classify(CIRCULARS / case["document"]))
        outcome = expected.get("outcome", "report")
        if outcome != "none":
            report, blocked = _run_pipeline(case)
            if outcome == "blocked":
                _expect(result, "run is blocked before evaluation", True, report is None)
                if blocked is not None:
                    result.assertions.append(Assertion("blocked reason", expected["blocked_reason_contains"] in blocked,
                                                       expected["blocked_reason_contains"], blocked[:200]))
            else:
                _expect(result, "run produces a report", True, report is not None)
                if report is not None:
                    evaluate_report(result, expected, report)
                else:
                    result.error = f"blocked: {blocked}"
        if case.get("instrument"):
            evaluate_readiness(result, case, expected)
    except Exception as error:  # noqa: BLE001 - the scorecard reports it
        result.error = f"{type(error).__name__}: {error}"
    return result


def run_control_check(item: dict) -> CaseResult:
    """A control's data check, run through the guarded engine, against the planted counts."""
    from bank_mcp_server import control_library
    from bank_mcp_server.secure_engine import SecureEngine
    from core.models import ComplianceCheck
    from ingestion.documents import parse_document
    from ingestion.templates import iracp_directions

    result = CaseResult(item["id"])
    try:
        control = next(c for c in control_library.controls() if c["id"] == item["control"])
        data_check = control["data_check"]
        _expect(result, "control has a data check", True, data_check is not None)
        engine = SecureEngine()
        if data_check["rule"]:
            path = CIRCULARS / control_library.instruments()["iracp"]["corpus_file"]
            extraction = iracp_directions(parse_document(path.read_bytes(), path.name))
            rule = next(r for r in extraction.rules if r.rule_id.endswith(":" + data_check["rule"]))
            checks = rule.checks
        else:
            expression = data_check["expression"]
            checks = [ComplianceCheck(kind="record_assertion", description=data_check["description"], view=data_check["view"],
                                      where=expression["where"], assertion=expression["assert"])]
        outcomes = [engine.check_data(c, item["as_of"], subject=item["control"]) for c in checks]
        _expect(result, "violations across the data check", item["expected_violations"], sum(o.violations for o in outcomes))
        if "min_in_scope" in item:  # a check that matches (almost) nothing proves nothing
            in_scope = sum(o.in_scope for o in outcomes)
            result.assertions.append(Assertion("rows in scope (at least)", in_scope >= item["min_in_scope"], f">= {item['min_in_scope']}", in_scope))
        _expect(result, "control status", item["control_status"], control["implementation_status"])
    except Exception as error:  # noqa: BLE001
        result.error = f"{type(error).__name__}: {error}"
    return result


def run_suite(cases: dict | None = None, only: str | None = None, llm: bool = False) -> list[CaseResult]:
    cases = cases or load_cases()
    out = [run_case(c, llm) for c in cases["cases"] if not only or only in c["id"]]
    if not llm:
        out += [run_control_check(i) for i in cases.get("control_checks", []) if not only or only in i["id"]]
    return out


def scorecard(results: list[CaseResult]) -> str:
    lines = []
    for r in results:
        if r.skipped:
            lines.append(f"SKIP  {r.case_id}  ({r.skipped})")
            continue
        status = "PASS" if r.passed else "FAIL"
        lines.append(f"{status}  {r.case_id}  ({len(r.assertions) - len(r.failures)}/{len(r.assertions)} assertions)")
        if r.error:
            lines.append(f"      error: {r.error}")
        for a in r.failures:
            lines.append(f"      - {a.name}\n          expected: {json.dumps(a.expected, default=str)}\n          actual:   {json.dumps(a.actual, default=str)}")
    ran = [r for r in results if not r.skipped]
    failed = [r for r in ran if not r.passed]
    total = sum(len(r.assertions) for r in ran)
    bad = sum(len(r.failures) for r in ran) + sum(1 for r in ran if r.error)
    lines.append("")
    lines.append(f"{len(ran) - len(failed)}/{len(ran)} cases passed, {total - sum(len(r.failures) for r in ran)}/{total} assertions passed"
                 + (f", {len(failed)} FAILED" if failed else "") + (f" ({bad} problems)" if bad else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", type=Path, default=CASES_FILE)
    parser.add_argument("--only", help="run the cases whose id contains this text")
    parser.add_argument("--llm", action="store_true", help="agentic extraction with Gemini (needs GCP); never used by tests")
    parser.add_argument("--list", action="store_true", help="list the case ids and exit")
    args = parser.parse_args(argv)
    os.environ.setdefault("YUKDHIS_BACKEND", "local")
    os.environ.setdefault("AUTH_MODE", "none")
    os.environ["AGENT_MODE"] = "agentic" if args.llm else "deterministic"
    if "YUKDHIS_RUNTIME_DIR" not in os.environ:
        os.environ["YUKDHIS_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="yukdhis-eval-")
    cases = load_cases(args.cases)
    if args.list:
        print("\n".join(c["id"] for c in cases["cases"]) + "\n" + "\n".join(i["id"] for i in cases.get("control_checks", [])))
        return 0
    results = run_suite(cases, args.only, args.llm)
    print(scorecard(results))
    return 0 if all(r.passed or r.skipped for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
