"""Generate the deterministic synthetic bank used by the demo, the tests and the eval suite.

Outputs (all fictional):
  data/bank/architecture.json        BIAN topology for the knowledge graph, including internal policies and controls
  data/bank/operational/*.csv        core-banking extract (contains synthetic PII)
  data/bank/manifest.json            reference date, row counts, and the planted exceptions with exact counts

The bank is built to be *mostly compliant* with the RBI IRACP Directions in force on the reference date (Income Recognition,
Asset Classification and Provisioning Directions, 2025, as updated 1 Jul 2026, ``RBI/DOR/2025-26/164``), with a small, fully
documented set of planted exceptions (``manifest["planted"]``). Provisioning and classification follow the Directions;
paragraph numbers are cited next to each rule below. PD, LGD and EAD are deliberately NOT generated: the bank has no ECL
data, which is the planted readiness gap for the final ACPIR Directions (effective 1 Apr 2027).

Planted scenarios:
  * Release of property documents within 30 days (RBI/2023-24/60): exactly 12 violations, 7 at Koramangala (unchanged).
  * Floating-rate reset on EMI loans (RBI/2023-24/55): product-level control gaps and a few unnotified borrowers.
  * IRACP: DPD above 90 still STANDARD, SMA mis-tags, NPA provision shortfalls (two branches), re-restructured accounts on
    accrual basis; KYC reviews past due.

``python scripts/generate_synthetic_bank.py`` writes everything; running it twice produces identical bytes.
"""

from __future__ import annotations

import csv
import json
import math
import random
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.bank_data import (  # noqa: E402
    ALL_ADVANCES, BRANCHES, CALAMITY_BRANCHES, FIRST, LAST, PROCESSES, PRODUCT_ORDER, PRODUCTS, PROFILES, PROVIDES,
    STRESSED_BRANCHES, TEAMS)

BANK = ROOT / "data" / "bank"
REFERENCE_DATE = date(2026, 10, 17)
DOC_RULE_EFFECTIVE = date(2023, 12, 1)
AMENDMENT_EFFECTIVE = date(2026, 7, 1)  # IRACP Amendment Directions, 2026: 62A/62B, 84A-84D, 139A/139B
SEED = 2026
N_CUSTOMERS, N_MSME = 2500, 250

# Planted exception counts (exact; see manifest["planted"]).
PLANT_DPD90_STANDARD = {"branch:patna-boring": 6, "branch:surat-adajan": 5, "branch:jaipur-cscheme": 3}
PLANT_SMA_MISTAG = {"SMA1": 3, "SMA2": 3}          # SMA1 reported as SMA0, SMA2 reported as SMA1
# NPA provision shortfalls by branch and kind: substandard secured (para 85), substandard unsecured (para 86), doubtful with an
# unsecured portion (paras 90 and 91; held below the unsecured portion, so it fails both doubtful rules), by age band of the
# doubtful rule, whatever its age band.
PLANT_SHORTFALL = {
    "branch:pune-kothrud": {"SS_SECURED": 6, "SS_UNSECURED": 3, "DOUBTFUL": 1},
    "branch:lko-hazratganj": {"SS_SECURED": 4, "SS_UNSECURED": 0, "DOUBTFUL": 2},
}
PLANT_REKYC = {"HIGH": 8, "MEDIUM": 8, "LOW": 6}
N_RESTRUCTURED_FIRST, N_RESTRUCTURED_REPEAT, N_REPEAT_ON_ACCRUAL = 28, 8, 5
N_UNNOTIFIED, N_NO_TENOR_CHOICE, N_NO_STATEMENT = 42, 9, 18

# IRACP para 5(13): an exposure is unsecured when security is at most 10 per cent of the outstanding, ab initio.
# Para 80(1),(7), 81: standard-asset rates in basis points of the funded outstanding.
STANDARD_RATE_BPS = {"INDIVIDUAL_HOUSING": 25, "FARM_CREDIT": 25, "SMALL_MICRO_ENTERPRISE": 25, "MEDIUM_ENTERPRISE": 40, "OTHER": 40}
SUBSTANDARD_SECURED_PCT = 15        # para 85
SUBSTANDARD_UNSECURED_PCT = 25      # para 86: 15 + additional 10 per cent
DOUBTFUL_SECURED_PCT = ((24, 25), (48, 40), (10 ** 9, 100))   # para 91: up to 1 year / 1-3 years / more than 3 years in doubtful
ADDITIONAL_RESOLUTION_BPS = 500     # paras 84A, 84B: 5 per cent of the outstanding debt for each resolution plan implemented
NPA_CLASSES = ("SUBSTANDARD", "DOUBTFUL", "LOSS")
STANDARD_CLASSES = ("STANDARD", "SMA0", "SMA1", "SMA2")
KYC_YEARS = {"HIGH": 2, "MEDIUM": 8, "LOW": 10}   # Master Direction - KYC: periodic updation by risk category


def node(node_id, node_type, name, **attributes):
    return {"id": node_id, "type": node_type, "name": name, "attributes": attributes}


def edge(source, target, edge_type, valid_from="2020-01-01", valid_to=None, **attributes):
    return {"source": source, "target": target, "type": edge_type, "valid_from": valid_from,
            "valid_to": valid_to, "attributes": attributes}


# ---------------------------------------------------------------------------------------------------- helpers
def add_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


def completed_months(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month - (1 if end.day < start.day else 0)


def ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def rounded(value: float, step: int) -> int:
    return max(step, int(round(value / step)) * step)


def stream(name: str) -> random.Random:
    """Independent deterministic stream per concern, so changing one scenario does not shift another."""
    return random.Random(f"{SEED}:{name}")


def unique_ref(rng: random.Random, prefix: str, bits: int, seen: set[str], width: int) -> str:
    while True:
        ref = f"{prefix}-{rng.getrandbits(bits):0{width}X}"
        if ref not in seen:
            seen.add(ref)
            return ref


# ---------------------------------------------------------------------------------------------------- provisioning
def required_provision(classification: str, exposure_class: str, outstanding: int, secured_portion: int, secured_flag: bool,
                       npa_age_months: int | None, restructure_count: int) -> int:
    """Minimum provision in INR under IRACP (as updated 1 Jul 2026), rounded up to the rupee."""
    if classification in STANDARD_CLASSES:
        bps = STANDARD_RATE_BPS[exposure_class]                       # paras 80(1), 80(7), 81
        bps += ADDITIONAL_RESOLUTION_BPS * restructure_count          # paras 84A, 84B
        return min(outstanding, ceil_div(outstanding * bps, 10_000))
    if classification == "SUBSTANDARD":                               # paras 85, 86
        pct = SUBSTANDARD_SECURED_PCT if secured_flag else SUBSTANDARD_UNSECURED_PCT
        return ceil_div(outstanding * pct, 100)
    if classification == "DOUBTFUL":                                  # paras 90, 91
        unsecured = outstanding - secured_portion
        pct = next(rate for limit, rate in DOUBTFUL_SECURED_PCT if (npa_age_months or 0) < limit)
        return min(outstanding, ceil_div(unsecured * 100 + secured_portion * pct, 100))
    if classification == "LOSS":                                      # para 95
        return outstanding
    raise ValueError(classification)


def sma_band(dpd: int) -> str:
    """Para 31 and Illustration I: overdue day 1-30 SMA-0, 31-60 SMA-1, 61-90 SMA-2; para 42(1): more than 90 days is NPA."""
    return "STANDARD" if dpd <= 0 else "SMA0" if dpd <= 30 else "SMA1" if dpd <= 60 else "SMA2"


# ---------------------------------------------------------------------------------------------------- operational data
def make_branches(rng: random.Random) -> list[dict]:
    return [{"branch_id": bid, "branch_name": name, "city": city, "region": region,
             "manager_name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
             "manager_email": f"mgr.{bid.split(':')[1]}@bank.example.com"} for bid, name, city, region, _ in BRANCHES]


def make_customers(rng: random.Random) -> tuple[list[dict], dict[str, str]]:
    """Customers with KYC state. Returns (rows, enterprise size by customer_id for MSME customers)."""
    weights = [b[4] for b in BRANCHES]
    branch_ids = [b[0] for b in BRANCHES]
    refs: set[str] = set()
    customers, sizes = [], {}
    for index in range(N_CUSTOMERS):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        msme = index >= N_CUSTOMERS - N_MSME
        cid = f"CIF-{100001 + index}"
        if msme:
            sizes[cid] = rng.choices(["MICRO", "SMALL", "MEDIUM"], weights=[40, 40, 20])[0]
        risk = rng.choices(["LOW", "MEDIUM", "HIGH"], weights=[55, 35, 10] if msme else [82, 15, 3])[0]
        years = KYC_YEARS[risk]
        last_review = REFERENCE_DATE - timedelta(days=rng.randrange(0, years * 365 - 20))
        due = add_years(last_review, years)
        customers.append({
            "customer_id": cid, "customer_ref": unique_ref(rng, "CUST", 24, refs, 6),
            "full_name": f"{first} {last}", "email": f"{first.lower()}.{last.lower()}{index}@mail.example.com",
            "phone": f"+91-90000{index:05d}", "pan": f"ZZ{chr(65 + index % 26)}PX{1000 + index}Q",
            "segment_id": "segment:msme" if msme else "segment:retail-individual",
            "branch_id": rng.choices(branch_ids, weights=weights)[0],
            "kyc_risk_category": risk, "last_kyc_review": last_review.isoformat(), "kyc_due_date": due.isoformat(),
            "kyc_status": "",
        })
    return customers, sizes


def finalize_kyc(customers: list[dict]) -> None:
    for c in customers:
        due = date.fromisoformat(c["kyc_due_date"])
        c["kyc_status"] = ("OVERDUE" if due < REFERENCE_DATE else "DUE_SOON" if (due - REFERENCE_DATE).days <= 60 else "CURRENT")


def plant_overdue_kyc(rng: random.Random, customers: list[dict]) -> dict:
    """J customers whose periodic KYC updation is overdue (past kyc_due_date)."""
    picked = []
    for risk, count in PLANT_REKYC.items():
        pool = [c for c in customers if c["kyc_risk_category"] == risk and c["kyc_status"] == "CURRENT"]
        picked += rng.sample(pool, count)
    for c in picked:
        years = KYC_YEARS[c["kyc_risk_category"]]
        due = REFERENCE_DATE - timedelta(days=rng.randrange(7, 420))
        c["kyc_due_date"] = due.isoformat()
        c["last_kyc_review"] = add_years(due, -years).isoformat()
        c["kyc_status"] = "OVERDUE"
    return {"count": len(picked), "by_risk_category": dict(PLANT_REKYC),
            "by_branch": dict(Counter(c["branch_id"] for c in picked).most_common())}


def new_loan(rng: random.Random, loans: list[dict], refs: set[str], customer: dict, product_id: str, status: str, sanctioned_on: date,
             closed_on: date | None = None, enterprise_size: str | None = None) -> dict:
    profile = PROFILES.get(product_id)
    attrs = next(p[2] for p in PRODUCTS if p[0] == product_id)
    floating_emi = attrs["rate_type"] == "FLOATING" and attrs["instalment_based"]
    emi_share = profile["emi"] if profile else 0.0
    loan = {
        "loan_id": f"LN-{len(loans) + 1001}", "loan_ref": unique_ref(rng, "LR", 28, refs, 7),
        "customer_id": customer["customer_id"], "product_id": product_id, "status": status, "rate_type": attrs["rate_type"],
        "emi_amount": 0, "outstanding_balance": 0, "sanctioned_on": sanctioned_on.isoformat(),
        "closed_on": closed_on.isoformat() if closed_on else "",
        "rate_reset_due": "2026-11-01" if floating_emi and status == "ACTIVE" else "",
        "borrower_notified": False, "has_fixed_rate_option": False, "has_emi_tenor_choice": False, "statement_available": False,
        # --- asset quality (IRACP) ---
        "days_past_due": 0, "overdue_amount": 0, "asset_classification": "", "npa_date": "", "npa_age_months": "",
        "security_value": 0, "secured": False, "restructured": False, "restructure_date": "", "restructure_count": 0,
        "provision_held": 0, "provision_required": 0, "interest_recognition": "", "exposure_class": "", "ecl_stage_proxy": "",
        # internal (not written)
        "_branch": customer["branch_id"], "_floating_emi": floating_emi, "_emi_share": emi_share, "_size": enterprise_size,
        "_state": "CLOSED" if status == "CLOSED" else "STANDARD", "_npa_age_days": None,
    }
    loans.append(loan)
    return loan


def exposure_class_for(product_id: str, size: str | None) -> str:
    exposure = PROFILES[product_id]["exposure"]
    if exposure == "MSME":
        return "MEDIUM_ENTERPRISE" if size == "MEDIUM" else "SMALL_MICRO_ENTERPRISE"   # paras 80(1), 81
    return exposure


def sample_amount(rng: random.Random, profile: dict) -> int:
    low, high, mode = profile["amount"]
    return rounded(rng.triangular(low, high, mode), profile["round_to"])


def draw_state(rng: random.Random, profile: dict, branch_id: str) -> str:
    p0, p1, p2, pn = profile["dpd"]
    boost = STRESSED_BRANCHES.get(branch_id, 1.0)
    pn *= boost
    p0, p1, p2 = p0 * (1 + (boost - 1) * 0.3), p1 * (1 + (boost - 1) * 0.3), p2 * (1 + (boost - 1) * 0.3)
    roll = rng.random()
    for state, edge_ in (("NPA", pn), ("SMA2", p2), ("SMA1", p1), ("SMA0", p0)):
        if roll < edge_:
            return state
        roll -= edge_
    return "STANDARD"


def apply_state(rng: random.Random, loan: dict, profile: dict, state: str) -> None:
    """Set DPD, classification, NPA age, security and amounts for an ACTIVE loan whose state was drawn."""
    outstanding = loan["outstanding_balance"]
    if state == "NPA":
        kind = rng.choices(["SS", "D1", "D2", "D3", "LOSS"], weights=[60, 17, 13, 4, 6])[0]
        age_days = {"SS": (1, 360), "D1": (370, 700), "D2": (740, 1400), "D3": (1470, 2100), "LOSS": (760, 1700)}[kind]
        age = rng.randrange(*age_days)
        npa_date = REFERENCE_DATE - timedelta(days=age)
        months = completed_months(npa_date, REFERENCE_DATE)
        loan["_npa_age_days"] = age
        loan["days_past_due"] = 91 + age                    # DPD keeps counting; NPA from day 91 (para 42(1))
        loan["npa_date"], loan["npa_age_months"] = npa_date.isoformat(), months
        loan["asset_classification"] = "SUBSTANDARD" if months < 12 else "DOUBTFUL"   # defs 5(12), 5(2)
        if kind == "LOSS":
            loan["asset_classification"] = "LOSS"
        loan["_state"] = "NPA"
        loan["interest_recognition"] = "CASH"                # paras 125, 132
    else:
        dpd = {"STANDARD": 0, "SMA0": rng.randrange(1, 31), "SMA1": rng.randrange(31, 61), "SMA2": rng.randrange(61, 91)}[state]
        loan["days_past_due"] = dpd
        loan["asset_classification"] = sma_band(dpd)
        loan["_state"] = state
        loan["interest_recognition"] = "ACCRUAL"             # para 124
    # Security: assessed realisable value. Eroded on NPAs; para 68(2) treats value below 10 per cent of the outstanding as nil.
    ratio_low, ratio_high = profile["security"] or (0.0, 0.0)
    if loan["secured"] and profile["security"]:
        if state == "NPA":
            loan["security_value"] = int(outstanding * rng.uniform(0.45, 1.15))
        else:
            loan["security_value"] = int(outstanding * rng.uniform(ratio_low, ratio_high))
    if loan["asset_classification"] == "LOSS" and loan["secured"]:
        loan["security_value"] = int(outstanding * rng.uniform(0.02, 0.09))
    dpd = loan["days_past_due"]
    emi = loan["emi_amount"]
    if dpd > 0:
        instalments = max(1, math.ceil(dpd / 30))
        loan["overdue_amount"] = min(outstanding, instalments * emi) if emi else min(outstanding, rounded(outstanding * min(1.0, 0.02 + dpd / 365 * 0.12), 100))
    loan["ecl_stage_proxy"] = 1 if dpd <= 30 else 2 if dpd <= 90 else 3


def finish_provisioning(rng: random.Random, loan: dict) -> None:
    secured_portion = min(loan["security_value"], loan["outstanding_balance"])
    required = required_provision(loan["asset_classification"], loan["exposure_class"], loan["outstanding_balance"], secured_portion,
                                  loan["secured"], loan["npa_age_months"] if loan["npa_age_months"] != "" else None,
                                  loan["restructure_count"])
    loan["provision_required"] = required
    # Voluntary buffer above the regulatory minimum is allowed (para 100, 103).
    buffer = 1.0 if rng.random() > 0.08 else rng.choice([1.05, 1.1, 1.25])
    loan["provision_held"] = min(loan["outstanding_balance"], int(math.ceil(required * buffer))) if buffer > 1.0 else required


def build_active_book(rng: random.Random, customers: list[dict], sizes: dict[str, str]) -> tuple[list[dict], set[str]]:
    loans: list[dict] = []
    refs: set[str] = set()
    retail = [c for c in customers if c["segment_id"] == "segment:retail-individual"]
    msme = [c for c in customers if c["segment_id"] == "segment:msme"]
    for product_id in PRODUCT_ORDER:
        profile = PROFILES[product_id]
        category = next(p[2]["category"] for p in PRODUCTS if p[0] == product_id)
        pool = msme if category == "MSME_LOAN" else retail
        for _ in range(profile["count"]):
            customer = rng.choice(pool)
            low_year, high_year = profile["years"]
            start = date(low_year, 1, 1)
            end = min(date(high_year, 12, 31), REFERENCE_DATE - timedelta(days=45))
            sanctioned = start + timedelta(days=rng.randrange(0, max(1, (end - start).days)))
            loan = new_loan(rng, loans, refs, customer, product_id, "ACTIVE", sanctioned, enterprise_size=sizes.get(customer["customer_id"]))
            outstanding = sample_amount(rng, profile)
            loan["outstanding_balance"] = outstanding
            loan["emi_amount"] = rounded(outstanding * profile["emi"], 50) if profile["emi"] else 0
            secured = profile["secured"]
            loan["secured"] = (outstanding > int(secured.split(":")[1])) if isinstance(secured, str) else bool(secured)
            loan["exposure_class"] = exposure_class_for(product_id, loan["_size"])
            state = draw_state(rng, profile, customer["branch_id"])
            if profile["dpd"][3] == 0 and state == "NPA":
                state = "SMA2"
            apply_state(rng, loan, profile, state)
            if loan["_state"] == "NPA":  # sanction must precede the NPA date by a sensible margin
                npa = date.fromisoformat(loan["npa_date"])
                if date.fromisoformat(loan["sanctioned_on"]) > npa - timedelta(days=150):
                    loan["sanctioned_on"] = (npa - timedelta(days=rng.randrange(150, 900))).isoformat()
            # Rate-reset scenario flags (RBI/2023-24/55) concern floating EMI retail loans.
            if loan["_floating_emi"] and category == "RETAIL_LOAN" and loan["_state"] != "CLOSED":
                loan["borrower_notified"] = True
                loan["has_fixed_rate_option"] = product_id != "product:auto-loan-floating"
                loan["has_emi_tenor_choice"] = True
                loan["statement_available"] = True
            else:
                loan["has_emi_tenor_choice"] = True
    return loans, refs


def plant_classification_exceptions(rng: random.Random, loans: list[dict]) -> dict:
    """Mis-classifications. Each is a loan the day-end process should have moved but did not."""
    planted: dict = {}
    eligible_products = {"product:personal-loan-fixed", "product:personal-loan-floating", "product:auto-loan-fixed",
                         "product:auto-loan-floating", "product:education-loan", "product:msme-term-loan"}
    # (a) DPD above 90 but still STANDARD (para 42(1)): provision and interest stay on the standard-asset basis.
    chosen = []
    for branch, count in PLANT_DPD90_STANDARD.items():
        pool = [l for l in loans if l["_branch"] == branch and l["_state"] == "STANDARD" and l["product_id"] in eligible_products
                and not l["restructured"]]
        chosen += rng.sample(pool, count)
    for loan in chosen:
        dpd = rng.randrange(95, 160)
        loan["days_past_due"], loan["_state"] = dpd, "MISSED_NPA"
        loan["asset_classification"] = "STANDARD"
        loan["overdue_amount"] = min(loan["outstanding_balance"], max(1, math.ceil(dpd / 30)) * (loan["emi_amount"] or loan["outstanding_balance"] // 12))
        loan["ecl_stage_proxy"] = 3
        finish_provisioning(rng, loan)
    planted["dpd_over_90_still_standard"] = {"count": len(chosen), "by_branch": dict(PLANT_DPD90_STANDARD)}
    # (b) SMA band mis-tags (para 31): SMA-1 reported as SMA-0, SMA-2 reported as SMA-1.
    mistag = 0
    for band, count in PLANT_SMA_MISTAG.items():
        pool = [l for l in loans if l["_state"] == band and l["restructure_count"] == 0]
        for loan in rng.sample(pool, count):
            loan["asset_classification"] = "SMA0" if band == "SMA1" else "SMA1"
            loan["_state"] = f"MISTAG_{band}"
            mistag += 1
    planted["sma_band_mistags"] = {"count": mistag, "sma1_reported_as_sma0": PLANT_SMA_MISTAG["SMA1"],
                                   "sma2_reported_as_sma1": PLANT_SMA_MISTAG["SMA2"]}
    # (c) NPA provision shortfalls in two branches (paras 85, 86, 90, 91): the provision held is a fraction of the requirement.
    short, by_kind = [], Counter()
    for branch, kinds in PLANT_SHORTFALL.items():
        for kind, count in kinds.items():
            if not count:
                continue

            def eligible(l, kind=kind, branch=branch):
                if l["_branch"] != branch or l["_state"] != "NPA":
                    return False
                if kind == "SS_SECURED":
                    return l["asset_classification"] == "SUBSTANDARD" and l["secured"]
                if kind == "SS_UNSECURED":
                    return l["asset_classification"] == "SUBSTANDARD" and not l["secured"]
                unsecured = l["outstanding_balance"] - min(l["security_value"], l["outstanding_balance"])
                return l["asset_classification"] == "DOUBTFUL" and unsecured >= 0.02 * l["outstanding_balance"]

            pool = [l for l in loans if eligible(l) and l not in short]
            if len(pool) < count:
                raise SystemExit(f"{branch}: only {len(pool)} {kind} NPAs for {count} planted shortfalls; raise STRESSED_BRANCHES")
            chosen_here = rng.sample(pool, count)
            short += chosen_here
            by_kind[(branch, kind)] = count
            for loan in chosen_here:
                if kind == "DOUBTFUL":  # below the unsecured portion, so it fails the unsecured-portion and the ageing rule
                    unsecured = loan["outstanding_balance"] - min(loan["security_value"], loan["outstanding_balance"])
                    loan["provision_held"] = int(unsecured * rng.uniform(0.3, 0.8))
                else:
                    loan["provision_held"] = int(loan["provision_required"] * rng.uniform(0.30, 0.85))
                loan["_state"] = "SHORTFALL"
    planted["npa_provision_shortfall"] = {
        "count": len(short), "by_branch": {b: sum(k.values()) for b, k in PLANT_SHORTFALL.items()},
        "by_kind": {f"{b}:{k}": n for (b, k), n in by_kind.items()},
        "failing_checks": {"substandard-provision-secured-15": 10, "substandard-provision-unsecured-25": 3,
                           "doubtful-unsecured-portion-100": 3, "doubtful-secured-portion-ageing": 3},
        "shortfall_inr": sum(l["provision_required"] - l["provision_held"] for l in short)}
    return planted


def plant_restructuring(rng: random.Random, loans: list[dict]) -> dict:
    """Resolution plans under Chapter VI-A (calamity relief) implemented after the amendment took effect on 1 Jul 2026.

    84A/84B: additional specific provision of 5 per cent of the outstanding per resolution plan. 139A: accrual basis for
    accounts with a resolution plan; 139B: cash basis for accounts restructured again (para 62B)."""
    products = {"product:home-loan-floating", "product:agri-kcc", "product:msme-working-capital", "product:msme-term-loan",
                "product:personal-loan-fixed", "product:personal-loan-floating"}
    pool = [l for l in loans if l["_branch"] in CALAMITY_BRANCHES and l["_state"] == "STANDARD" and l["product_id"] in products]
    total = N_RESTRUCTURED_FIRST + N_RESTRUCTURED_REPEAT
    if len(pool) < total:
        raise SystemExit(f"only {len(pool)} calamity-branch standard loans for {total} resolution plans")
    chosen = rng.sample(pool, total)
    repeat = chosen[:N_RESTRUCTURED_REPEAT]
    on_accrual = set(id(l) for l in repeat[:N_REPEAT_ON_ACCRUAL])
    for loan in chosen:
        count = 2 if loan in repeat else 1
        date_ = AMENDMENT_EFFECTIVE + timedelta(days=rng.randrange(1, (REFERENCE_DATE - AMENDMENT_EFFECTIVE).days - 5))
        loan.update(restructured=True, restructure_date=date_.isoformat(), restructure_count=count, _state="RESTRUCTURED")
        loan["interest_recognition"] = "ACCRUAL" if count == 1 or id(loan) in on_accrual else "CASH"   # 139A, 139B
        loan["days_past_due"], loan["asset_classification"], loan["overdue_amount"], loan["ecl_stage_proxy"] = 0, "STANDARD", 0, 1
        finish_provisioning(rng, loan)
    return {"resolution_plans": {"count": total, "first_plan": N_RESTRUCTURED_FIRST, "repeated": N_RESTRUCTURED_REPEAT,
                                 "by_branch": dict(Counter(l["_branch"] for l in chosen).most_common())},
            "repeat_restructured_on_accrual": {"count": N_REPEAT_ON_ACCRUAL, "paragraph": "139B"}}


def plant_rate_reset_exceptions(rng: random.Random, loans: list[dict]) -> dict:
    scope = [l for l in loans if l["_floating_emi"] and l["status"] == "ACTIVE" and l["product_id"] != "product:msme-term-loan"]
    for loan in rng.sample(scope, N_UNNOTIFIED):
        loan["borrower_notified"] = False
    for loan in rng.sample(scope, N_NO_TENOR_CHOICE):
        loan["has_emi_tenor_choice"] = False
    for loan in rng.sample(scope, N_NO_STATEMENT):
        loan["statement_available"] = False
    auto = [l for l in scope if l["product_id"] == "product:auto-loan-floating"]
    return {"floating_emi_loans_in_scope": len(scope), "borrowers_not_notified_of_reset": N_UNNOTIFIED,
            "loans_without_emi_or_tenor_choice": N_NO_TENOR_CHOICE, "loans_without_reset_statement": N_NO_STATEMENT,
            "auto_floating_loans_without_fixed_rate_option": len(auto),
            "note": "DriveNow Floating lost its fixed-rate switch on 2024-07-01 (product-level control gap, bitemporal demo)"}


def build_closed_book(rng: random.Random, loans: list[dict], refs: set[str], customers: list[dict], sizes: dict[str, str],
                      events: list[dict]) -> None:
    """Closed loans. The planted document-release scenario is unchanged: 12 violations, 7 at Koramangala."""
    retail = [c for c in customers if c["segment_id"] == "segment:retail-individual"]
    msme = [c for c in customers if c["segment_id"] == "segment:msme"]
    koramangala = [c for c in retail if c["branch_id"] == "branch:blr-koramangala"]
    others = [c for c in retail if c["branch_id"] != "branch:blr-koramangala"]
    secured_retail = ["product:home-loan-floating", "product:auto-loan-floating", "product:auto-loan-fixed",
                      "product:loan-against-property", "product:gold-loan"]

    def sanctioned() -> date:
        return date(2019, 1, 1) + timedelta(days=rng.randrange(0, 1500))

    def closed_between(start: date, end: date) -> date:
        return start + timedelta(days=rng.randrange(0, (end - start).days))

    def close(customer, product_id, closed, released=None, size=None):
        loan = new_loan(rng, loans, refs, customer, product_id, "CLOSED", sanctioned(), closed, enterprise_size=size)
        loan["exposure_class"] = exposure_class_for(product_id, size)
        events.append({"loan_id": loan["loan_id"], "event_type": "LOAN_DISBURSED", "occurred_on": loan["sanctioned_on"]})
        events.append({"loan_id": loan["loan_id"], "event_type": "LOAN_CLOSED", "occurred_on": closed.isoformat()})
        if released:
            events.append({"loan_id": loan["loan_id"], "event_type": "DOCUMENTS_RELEASED", "occurred_on": released.isoformat()})
        return loan

    plan = (
        [("unreleased", koramangala)] * 4 + [("late", koramangala)] * 3 +       # 7 violations at Koramangala
        [("unreleased", others)] * 3 + [("late", others)] * 2 +                 # 5 violations elsewhere
        [("pre_effective_late", retail)] * 14 + [("boundary", retail)] * 8 +
        [("not_yet_due", retail)] * 9 + [("on_time", retail)] * 290
    )
    for kind, pool in plan:
        customer, product_id = rng.choice(pool), rng.choice(secured_retail)
        if kind == "unreleased":
            close(customer, product_id, closed_between(DOC_RULE_EFFECTIVE, REFERENCE_DATE - timedelta(days=45)))
        elif kind == "late":
            closed = closed_between(DOC_RULE_EFFECTIVE, REFERENCE_DATE - timedelta(days=120))
            close(customer, product_id, closed, closed + timedelta(days=rng.randrange(31, 90)))
        elif kind == "pre_effective_late":
            closed = closed_between(date(2023, 6, 1), DOC_RULE_EFFECTIVE - timedelta(days=1))
            close(customer, product_id, closed, closed + timedelta(days=rng.randrange(40, 80)))
        elif kind == "boundary":
            closed = closed_between(DOC_RULE_EFFECTIVE, REFERENCE_DATE - timedelta(days=60))
            close(customer, product_id, closed, closed + timedelta(days=30))
        elif kind == "not_yet_due":
            close(customer, product_id, REFERENCE_DATE - timedelta(days=rng.randrange(3, 25)))
        else:
            closed = closed_between(DOC_RULE_EFFECTIVE, REFERENCE_DATE - timedelta(days=35))
            close(customer, product_id, closed, closed + timedelta(days=rng.randrange(0, 29)))
    for _ in range(20):  # Late MSME releases: outside the personal-loan circular.
        customer = rng.choice(msme)
        closed = closed_between(DOC_RULE_EFFECTIVE, REFERENCE_DATE - timedelta(days=100))
        close(customer, "product:msme-term-loan", closed, closed + timedelta(days=70), sizes.get(customer["customer_id"]))
    for _ in range(95):  # Closed unsecured loans and card accounts: no documents to release.
        close(rng.choice(retail), rng.choice(["product:personal-loan-floating", "product:personal-loan-fixed", "product:education-loan",
                                              "product:credit-card"]), closed_between(DOC_RULE_EFFECTIVE, REFERENCE_DATE - timedelta(days=40)))


def active_events(rng: random.Random, loans: list[dict]) -> list[dict]:
    """Event log for the active book: disbursal, rate resets, overdue and classification events, restructuring."""
    events: list[dict] = []

    def add(loan, event_type, when):
        events.append({"loan_id": loan["loan_id"], "event_type": event_type, "occurred_on": when.isoformat() if isinstance(when, date) else when})

    for loan in loans:
        if loan["status"] != "ACTIVE":
            continue
        add(loan, "LOAN_DISBURSED", loan["sanctioned_on"])
        if loan["_floating_emi"]:
            for reset in ("2026-01-01", "2026-04-01", "2026-07-01"):  # quarterly benchmark resets
                add(loan, "RATE_RESET", reset)
                if loan["borrower_notified"]:
                    add(loan, "RATE_RESET_NOTICE_SENT", date.fromisoformat(reset) + timedelta(days=3))
        dpd = loan["days_past_due"]
        if dpd > 0:
            overdue_since = REFERENCE_DATE - timedelta(days=dpd - 1)
            add(loan, "OVERDUE_FLAGGED", overdue_since)
            for _ in range(min(6, max(1, dpd // 30))):
                add(loan, "EMI_BOUNCED", overdue_since + timedelta(days=rng.randrange(0, min(dpd, 180))))
            if dpd > 30:
                add(loan, "COLLECTION_NOTICE_SENT", overdue_since + timedelta(days=30))
        tag = loan["asset_classification"]
        if tag in ("SMA0", "SMA1", "SMA2"):
            for index, band in enumerate(("SMA0", "SMA1", "SMA2")):
                if ("SMA0", "SMA1", "SMA2").index(tag) >= index:
                    add(loan, "SMA_FLAGGED", REFERENCE_DATE - timedelta(days=dpd - 1) + timedelta(days=(0, 30, 60)[index]))
        if loan["npa_date"]:  # day-end NPA classification, provisioning and interest reversal (paras 31, 85-95, 132)
            for event in ("NPA_CLASSIFIED", "PROVISION_BOOKED", "INTEREST_REVERSED"):
                add(loan, event, loan["npa_date"])
        if loan["restructured"]:
            add(loan, "ACCOUNT_RESTRUCTURED", loan["restructure_date"])
            if loan["restructure_count"] > 1:
                add(loan, "ACCOUNT_RESTRUCTURED", "2025-12-15")
    return events


def operational() -> tuple[dict[str, list[dict]], dict]:
    customers, sizes = make_customers(stream("customers"))
    finalize_kyc(customers)
    planted: dict = {}
    loans, refs = build_active_book(stream("book"), customers, sizes)
    planted.update(plant_restructuring(stream("restructuring"), loans))
    for loan in loans:
        if loan["_state"] != "RESTRUCTURED":
            finish_provisioning(stream(f"provision:{loan['loan_id']}"), loan)
    planted.update(plant_classification_exceptions(stream("classification"), loans))
    rate_reset = plant_rate_reset_exceptions(stream("ratereset"), loans)
    closed_events: list[dict] = []
    build_closed_book(stream("closed"), loans, refs, customers, sizes, closed_events)
    events = active_events(stream("events"), loans) + closed_events
    planted["kyc_periodic_updation_overdue"] = plant_overdue_kyc(stream("kyc"), customers)
    # Scenario manifest entries.
    planted["rbi_2023_24_60_document_release_violations"] = 12
    planted["of_which_koramangala"] = 7
    planted["rbi_2023_24_55_rate_reset"] = rate_reset
    # Same-day events keep their causal order (a loan is closed before its documents are released).
    rank = {"LOAN_DISBURSED": 0, "LOAN_CLOSED": 1, "DOCUMENTS_RELEASED": 2}
    events.sort(key=lambda e: (e["occurred_on"], e["loan_id"], rank.get(e["event_type"], 5), e["event_type"]))
    for index, event in enumerate(events, start=1):
        event["event_id"] = f"EV-{index:05d}"
    loan_rows = []
    for loan in loans:
        loan_rows.append({k: v for k, v in loan.items() if not k.startswith("_")})
    branch_rows = make_branches(stream("branches"))
    tables = {"branches": branch_rows, "customers": customers, "loans": loan_rows,
              "loan_events": [{"event_id": e["event_id"], "loan_id": e["loan_id"], "event_type": e["event_type"],
                               "occurred_on": e["occurred_on"]} for e in events]}
    planted["_classification_mix"] = dict(Counter(l["asset_classification"] for l in loan_rows if l["status"] == "ACTIVE").most_common())
    return tables, planted


# ---------------------------------------------------------------------------------------------------- architecture
def architecture() -> dict:
    from scripts.bank_library import library_nodes_and_edges  # noqa: PLC0415 (kept out of the import path of the tests of this file)

    nodes, edges = [], []
    for pid, name, attrs, synonyms, owner in PRODUCTS:
        nodes.append(node(pid, "Product", name, synonyms=[*synonyms, ALL_ADVANCES], bian_domain="Product Directory", **attrs))
        edges.append(edge(pid, owner, "OWNED_BY"))
        edges.append(edge(pid, "process:loan-closure", "USES"))
        if attrs["rate_type"] == "FLOATING" and attrs["instalment_based"]:
            edges += [edge(pid, "process:rate-reset", "USES"), edge(pid, "process:borrower-notification", "USES")]
        if attrs["secured"]:
            edges += [edge(pid, "process:collateral-release", "USES"), edge(pid, "process:document-custody", "USES")]
        edges += [edge(pid, f"process:{p}", "USES") for p in ("asset-classification", "provisioning", "income-recognition", "collections-recovery")]
        for control in PROVIDES.get(pid, []):
            edges.append(edge(pid, f"control:{control}", "PROVIDES"))
    # Bitemporal history: DriveNow offered a fixed-rate switch until mid-2024.
    edges.append(edge("product:auto-loan-floating", "control:fixed-rate-option", "PROVIDES",
                      valid_from="2021-01-01", valid_to="2024-07-01", change_ticket="CHG-2024-0612"))
    for pid, name, domain, owner in PROCESSES:
        nodes.append(node(pid, "Process", name, bian_domain=domain))
        edges.append(edge(pid, owner, "OWNED_BY"))
    nodes += [node(tid, "Team", name) for tid, name in TEAMS]
    nodes += [node("segment:retail-individual", "CustomerSegment", "Retail Individual Borrower",
                   synonyms=["individual", "retail", "personal", "borrower"]),
              node("segment:msme", "CustomerSegment", "MSME Borrower", synonyms=["msme", "business", "enterprise"])]
    for pid, _, attrs, _, _ in PRODUCTS:
        segment = "segment:msme" if attrs["category"] == "MSME_LOAN" else "segment:retail-individual"
        edges.append(edge(segment, pid, "ELIGIBLE_FOR"))
    nodes += [node(bid, "Branch", f"{name}, {city}", city=city, region=region) for bid, name, city, region, _ in BRANCHES]
    extra_nodes, extra_edges = library_nodes_and_edges([p[0] for p in PRODUCTS])
    nodes += extra_nodes
    edges += extra_edges
    return {"tenant_id": "demo-bank", "nodes": nodes, "edges": edges}


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: (str(v).lower() if isinstance(v, bool) else v) for k, v in row.items()})


def build(bank: Path = BANK, pack_file: Path | None = None) -> dict:
    """Write every output under ``bank`` (and the ontology pack to ``pack_file``) and return the manifest."""
    from scripts.bank_library import write_library
    from scripts.bank_pack import PACK_FILE, write_pack

    write_library(bank)
    write_pack(pack_file or PACK_FILE)
    tables, planted = operational()
    (bank / "architecture.json").write_text(json.dumps(architecture(), indent=1) + "\n")
    for name, rows in tables.items():
        write_csv(bank / "operational" / f"{name}.csv", rows)
    mix = planted.pop("_classification_mix")
    manifest = {
        "reference_date": REFERENCE_DATE.isoformat(), "seed": SEED, "synthetic": True,
        "rows": {name: len(rows) for name, rows in tables.items()},
        "asset_classification_mix_active_book": mix,
        "planted": planted,
        "absent_by_design": ["PD", "LGD", "EAD", "ECL staging engine output", "effective interest rate schedules",
                             "macro scenarios", "prudential floor computation"],
    }
    (bank / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    print(json.dumps(build(), indent=2))


if __name__ == "__main__":
    main()
