"""Applicability gate: does this rule apply to *this* bank, and can we evaluate it?

Runs between mapping and evaluation (see ``pipeline/stages.py``). It is
deterministic and offline: it reads the rule's text, scope and checks, the
mapper's resolved products, and the bank's entity profile
(``data/bank/entity_profile.json``), and returns, per rule:

* ``applicable = True``  - the bank holds the licence / runs the business and every data domain the rule needs is onboarded;
* ``applicable = False`` - the rule targets a licence, entity type or business the bank does not have;
* ``applicable = None``  - cannot tell: the business or its data domain is not onboarded (``missing_data`` lists what is needed).

The keyword signals that tie a rule to a business line, data domain or licence are declared by the ontology packs
(``applicability_signals`` in ``data/ontology/packs/*.json``), for every pack whether or not the bank enabled it, so a
rule on an un-onboarded business is still recognised and gated.

Only ``True`` rules are evaluated. The others become ``NOT_EVALUATED`` with the
reasons as evidence, and get no remediation actions.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from core.models import Applicability, Rule
from knowledge_graph import packs
from knowledge_graph.ontology import get_ontology

PROFILE_PATH = packs.PROFILE_PATH

DOMAIN_LABELS = {
    "loans": "loan book (vw_compliance_impact)",
    "loan_events": "loan event log (vw_loan_events)",
    "customers": "customer dimension (tokenized)",
    "branches": "branch directory",
    "deposits": "deposit accounts and balances",
    "treasury_trades": "treasury FX / derivative trades (trade date, product, direction, INR notional, purpose, "
                       "hedge designation, counterparty residency, booking unit)",
    "counterparties": "derivative counterparty classification (residency, sector, credit quality)",
    "rbi_reserve_balances": "reserve balances held with RBI, by day",
    "capital_rwa": "capital adequacy data (RWA by risk type, CET1, Tier 1, CRAR, current CVA baseline)",
    "liquidity": "liquidity data (LCR, NSFR, CRR and SLR positions)",
}
BUSINESS_LINE_LABELS = {
    "retail_lending": "retail lending", "msme_lending": "MSME lending", "deposits": "deposits",
    "treasury_derivatives": "treasury / derivatives desk", "capital_management": "capital management (RWA, CRAR)",
    "liquidity_management": "liquidity management",
}


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


def signals(pack_dir: Path | None = None) -> tuple[packs.PackSignal, ...]:
    """Keyword signals declared by the ontology packs (every pack, enabled or not), in a stable order."""
    return packs.applicability_signals(pack_dir)


# Regulated-entity classes other than this bank's. Stripped before looking for wording that covers the bank.
OTHER_ENTITIES = {
    "NBFCs": _rx(r"\bnbfcs?\b|\bnon[- ]banking financial compan(?:y|ies)\b"),
    "co-operative banks": _rx(r"\b(?:(?:urban|primary|state|central|district)\s+(?:\(urban\)\s+)?)*co-?operative banks?\b"),
    "regional rural banks": _rx(r"\bregional rural banks?\b|\brrbs?\b"),
    "small finance banks": _rx(r"\bsmall finance banks?\b"),
    "payments banks": _rx(r"\bpayments? banks?\b"),
    "local area banks": _rx(r"\blocal area banks?\b"),
    "primary dealers": _rx(r"\b(?:standalone )?primary dealers?\b|\bspds?\b"),
    "asset reconstruction companies": _rx(r"\basset reconstruction compan(?:y|ies)\b|\barcs?\b"),
    "all-India financial institutions": _rx(r"\ball[- ]india financial institutions?\b|\baifis?\b"),
}
COVERS_BANK = _rx(r"\bbanks?\b|\bregulated entit(?:y|ies)\b|\blenders?\b|\bauthori[sz]ed dealers?\b|\bad (?:category|cat)")
AD_CATEGORY = _rx(r"\b(?:ad|authori[sz]ed dealers?)\s*(?:banks?\s*)?(?:category|cat\.?)\s*[-–]?\s*(iii|ii|i|1|2|3)\b")
AD_NORMAL = {"i": "AD_CATEGORY_I", "1": "AD_CATEGORY_I", "ii": "AD_CATEGORY_II", "2": "AD_CATEGORY_II",
             "iii": "AD_CATEGORY_III", "3": "AD_CATEGORY_III"}
ENTITY_LABELS = {"scheduled_commercial_bank": "a scheduled commercial bank"}
LOAN_ATTRIBUTE_CATEGORIES = {"RETAIL_LOAN", "MSME_LOAN"}


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    return json.loads(Path(path).read_text())


def load_profile(path: Path | None = None) -> dict:
    return _load(str(path or PROFILE_PATH))


def rule_text(rule: Rule) -> str:
    parts = [rule.title, rule.description, rule.citation.snippet, *rule.scope.product_terms, *rule.processes, *rule.segments]
    for check in rule.checks:
        parts += [check.description, (check.capability or "").replace("_", " ")]
    return " \n".join(p for p in parts if p)


def assess_rule(rule: Rule, mapping: dict | None = None, profile: dict | None = None) -> Applicability:
    profile = profile or load_profile()
    text = rule_text(rule)
    licences = profile.get("licences", {})
    lines = profile.get("business_lines", {})
    onboarded = set(profile.get("onboarded_data_domains", []))
    not_applicable: list[str] = []
    reasons: list[str] = []
    domains: set[str] = set()

    # Entity type: a rule addressed only to other classes of regulated entity does not apply.
    others = [name for name, rx in OTHER_ENTITIES.items() if rx.search(text)]
    if others:
        stripped = text
        for name in others:
            stripped = OTHER_ENTITIES[name].sub(" ", stripped)
        entity = ENTITY_LABELS.get(profile.get("entity_type", ""), profile.get("entity_type", "this bank"))
        if not COVERS_BANK.search(stripped):
            not_applicable.append(f"Addressed to {', '.join(others)} only; the bank is {entity}.")

    # Authorised Dealer category.
    categories = sorted({AD_NORMAL[m.lower()] for m in AD_CATEGORY.findall(text)})
    held = licences.get("authorised_dealer_category")
    if categories:
        if held in categories:
            reasons.append(f"The bank holds the {_pretty(held)} licence the rule addresses.")
        else:
            not_applicable.append(f"Addressed to {', '.join(_pretty(c) for c in categories)}; the bank is "
                                  f"{_pretty(held) if held else 'not an Authorised Dealer'}.")

    # Keyword signals -> licences, business lines and data domains.
    matched = [s for s in signals() if s.pattern.search(text)]
    for signal in matched:
        if signal.licence and not licences.get(signal.licence):
            not_applicable.append(f"Rule concerns {signal.label}, which needs an {_pretty(signal.licence)}; "
                                  f"the bank has none.")
        if signal.business_line:
            status = lines.get(signal.business_line, "not_onboarded")
            line = BUSINESS_LINE_LABELS.get(signal.business_line, signal.business_line)
            if status == "none":
                not_applicable.append(f"Rule concerns {signal.label}; the bank has no {line} business.")
            elif status != "active":
                reasons.append(f"Rule concerns {signal.label}; the bank's {line} is not onboarded.")
        domains.update(signal.domains)

    # Ontology signals: a check on a pack's vocabulary runs on the data domains that pack is evaluated on.
    ontology = get_ontology(profile)
    for check in rule.checks:
        if check.kind == "record_flag":
            domains.update(_domains(ontology.flag_domains, [check.record_field]))
        elif check.kind == "event_deadline":
            domains.update(_domains(ontology.event_domains, [check.trigger_event, check.required_event]))
        elif check.kind == "control_required":
            capability = check.capability if check.capability in ontology.capabilities else ontology.control_capability(
                check.control_id or "")
            if capability:
                domains.update(ontology.capability_domains.get(capability, ()))
    scope = rule.scope
    if scope.category in LOAN_ATTRIBUTE_CATEGORIES or any(
            getattr(scope, a) is not None for a in ("rate_type", "instalment_based", "secured", "collateral_type")):
        domains.add("loans")
    elif scope.category == "DEPOSIT":
        domains.add("deposits")
    products = [p for p in (mapping or {}).get("product_ids", []) if p]
    if products:
        domains.add("loans")
        reasons.append(f"Resolved to onboarded product(s): {', '.join(sorted(products))}.")

    missing = sorted(d for d in domains if d not in onboarded)
    missing_data = [f"{d}: {DOMAIN_LABELS.get(d, d)}" for d in missing]
    if not_applicable:
        return Applicability(rule_id=rule.rule_id, applicable=False, reasons=_unique(not_applicable + reasons),
                             missing_data=missing_data, domains=sorted(domains))
    if missing:
        reasons.append(f"Data domain not onboarded: {', '.join(missing)}.")
        return Applicability(rule_id=rule.rule_id, applicable=None, reasons=_unique(reasons), missing_data=missing_data,
                             domains=sorted(domains))
    if not domains:
        reasons.append("No signal links this rule to an onboarded business line or data domain.")
        return Applicability(rule_id=rule.rule_id, applicable=None, reasons=_unique(reasons), domains=[])
    entity = ENTITY_LABELS.get(profile.get("entity_type", ""), "the bank")
    reasons.append(f"Applies to {entity}; evaluated on onboarded data: {', '.join(sorted(domains))}.")
    return Applicability(rule_id=rule.rule_id, applicable=True, reasons=_unique(reasons), domains=sorted(domains))


def assess_rules(rules: Iterable[Rule], mappings: Iterable[dict] = (), profile: dict | None = None) -> list[Applicability]:
    by_rule = {m.get("rule_id"): m for m in mappings}
    return [assess_rule(rule, by_rule.get(rule.rule_id), profile) for rule in rules]


def is_gated(assessment: Applicability | dict | None) -> bool:
    """True when the rule must not be evaluated (not applicable, or applicability unknown)."""
    if assessment is None:
        return False
    value = assessment.applicable if isinstance(assessment, Applicability) else assessment.get("applicable")
    return value is not True


def evidence(assessment: Applicability) -> str:
    head = "Not applicable to this bank" if assessment.applicable is False else "Cannot evaluate: applicability unknown"
    text = f"{head}: {' '.join(assessment.reasons)}"
    if assessment.missing_data:
        text += f" Missing data: {'; '.join(assessment.missing_data)}."
    return text


def _domains(by_item: dict[str, frozenset[str]], items: list[str | None]) -> set[str]:
    """Data domains of the named vocabulary items; every item's domains when none is recognised."""
    known = [by_item[item] for item in items if item in by_item]
    return set().union(*(known or by_item.values()))


def _pretty(code: str | None) -> str:
    return {"AD_CATEGORY_I": "AD Category-I", "AD_CATEGORY_II": "AD Category-II", "AD_CATEGORY_III": "AD Category-III",
            "ifsc_banking_unit": "IFSC Banking Unit"}.get(code or "", (code or "").replace("_", " "))


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
