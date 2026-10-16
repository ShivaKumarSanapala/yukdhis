"""Generate the deterministic synthetic bank used by the demo and tests.

Outputs (all fictional):
  data/bank/architecture.json        BIAN topology for the knowledge graph
  data/bank/operational/*.csv        core-banking extract (contains synthetic PII)
  data/bank/manifest.json            reference date and planted scenario counts

Two RBI scenarios are planted:
  * Floating-rate reset on EMI loans (RBI/2023-24/55)
  * Release of property documents within 30 days (RBI/2023-24/60):
    exactly 12 in-scope violations, 7 of them at Koramangala.
"""

from __future__ import annotations

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANK = ROOT / "data" / "bank"
REFERENCE_DATE = date(2026, 10, 17)
DOC_RULE_EFFECTIVE = date(2023, 12, 1)
SEED = 2026

PRODUCTS = [
    ("product:personal-loan-floating", "Flexi Personal Loan", dict(rate_type="FLOATING", instalment_based=True, secured=False, collateral_type="NONE", category="RETAIL_LOAN"), ["personal loan", "consumer loan", "floating rate personal loan", "emi loan"], "team:retail-lending"),
    ("product:home-loan-floating", "EasyHome Floating Loan", dict(rate_type="FLOATING", instalment_based=True, secured=True, collateral_type="IMMOVABLE", category="RETAIL_LOAN"), ["home loan", "housing loan", "mortgage", "emi loan"], "team:retail-lending"),
    ("product:auto-loan-floating", "DriveNow Floating Loan", dict(rate_type="FLOATING", instalment_based=True, secured=True, collateral_type="MOVABLE", category="RETAIL_LOAN"), ["auto loan", "car loan", "vehicle loan", "four-wheeler advances", "two-wheeler loan", "emi loan"], "team:vehicle-finance"),
    ("product:personal-loan-fixed", "Flexi Personal Loan Fixed", dict(rate_type="FIXED", instalment_based=True, secured=False, collateral_type="NONE", category="RETAIL_LOAN"), ["personal loan", "fixed rate personal loan", "emi loan"], "team:retail-lending"),
    ("product:loan-against-property", "PropertyPlus LAP", dict(rate_type="FIXED", instalment_based=True, secured=True, collateral_type="IMMOVABLE", category="RETAIL_LOAN"), ["loan against property", "lap", "mortgage loan", "property loan"], "team:retail-lending"),
    ("product:gold-loan", "GoldEase Loan", dict(rate_type="FIXED", instalment_based=False, secured=True, collateral_type="MOVABLE", category="RETAIL_LOAN"), ["gold loan", "loan against gold jewellery", "jewel loan"], "team:retail-lending"),
    ("product:msme-term-loan", "MSME Growth Term Loan", dict(rate_type="FLOATING", instalment_based=True, secured=True, collateral_type="IMMOVABLE", category="MSME_LOAN"), ["msme loan", "business term loan", "sme loan"], "team:msme-banking"),
]
PROCESSES = [
    ("process:rate-reset", "Benchmark Rate Reset", "Consumer Loan", "team:lending-ops"),
    ("process:borrower-notification", "Borrower Correspondence", "Correspondence", "team:customer-comms"),
    ("process:loan-closure", "Loan Closure and Settlement", "Consumer Loan", "team:lending-ops"),
    ("process:collateral-release", "Collateral Release", "Collateral Asset Administration", "team:collateral-ops"),
    ("process:document-custody", "Original Document Custody", "Document Services", "team:collateral-ops"),
]
CONTROLS = [
    ("control:borrower-notification", "Reset Impact Notification"),
    ("control:emi-tenor-choice", "EMI or Tenor Choice"),
    ("control:fixed-rate-option", "Fixed Rate Conversion Option"),
    ("control:reset-statement", "Quarterly Reset Statement with APR"),
    ("control:document-release-sla", "30-day Document Release Tracker"),
    ("control:delay-compensation", "Automatic Delay Compensation"),
    ("control:document-collection-choice", "Document Collection Location Choice"),
]
TEAMS = [
    ("team:retail-lending", "Retail Lending"), ("team:vehicle-finance", "Vehicle Finance"),
    ("team:msme-banking", "MSME Banking"), ("team:lending-ops", "Lending Operations"),
    ("team:customer-comms", "Customer Communications"), ("team:collateral-ops", "Collateral Operations"),
]
SEGMENTS = [("segment:retail-individual", "Retail Individual Borrower", ["individual", "retail", "personal", "borrower"]),
            ("segment:msme", "MSME Borrower", ["msme", "business", "enterprise"])]
BRANCHES = [
    ("branch:blr-koramangala", "Koramangala", "Bengaluru", "South"),
    ("branch:mum-andheri", "Andheri East", "Mumbai", "West"),
    ("branch:del-cp", "Connaught Place", "New Delhi", "North"),
    ("branch:chn-tnagar", "T Nagar", "Chennai", "South"),
    ("branch:hyd-banjara", "Banjara Hills", "Hyderabad", "South"),
    ("branch:kol-saltlake", "Salt Lake", "Kolkata", "East"),
]
PROVIDES = {
    "product:personal-loan-floating": ["borrower-notification", "emi-tenor-choice", "fixed-rate-option"],
    "product:home-loan-floating": ["borrower-notification", "emi-tenor-choice", "fixed-rate-option", "reset-statement",
                                    "document-release-sla", "delay-compensation", "document-collection-choice"],
    "product:auto-loan-floating": ["borrower-notification", "emi-tenor-choice", "reset-statement", "document-release-sla"],
    "product:loan-against-property": ["document-release-sla", "delay-compensation"],
    "product:gold-loan": ["document-collection-choice"],
    "product:msme-term-loan": ["borrower-notification", "emi-tenor-choice"],
}
FIRST = ["Aarav", "Diya", "Vihaan", "Ananya", "Ishaan", "Meera", "Kabir", "Saanvi", "Arjun", "Riya", "Dev", "Kavya",
         "Rohan", "Nisha", "Aditya", "Pooja", "Karthik", "Lakshmi", "Farhan", "Zoya"]
LAST = ["Sharma", "Iyer", "Reddy", "Banerjee", "Patel", "Nair", "Khan", "Gupta", "Menon", "Das", "Rao", "Kulkarni"]


def node(node_id, node_type, name, **attributes):
    return {"id": node_id, "type": node_type, "name": name, "attributes": attributes}


def edge(source, target, edge_type, valid_from="2020-01-01", valid_to=None, **attributes):
    return {"source": source, "target": target, "type": edge_type, "valid_from": valid_from,
            "valid_to": valid_to, "attributes": attributes}


def architecture() -> dict:
    nodes, edges = [], []
    for pid, name, attrs, synonyms, owner in PRODUCTS:
        nodes.append(node(pid, "Product", name, synonyms=synonyms, bian_domain="Product Directory", **attrs))
        edges.append(edge(pid, owner, "OWNED_BY"))
        edges.append(edge(pid, "process:loan-closure", "USES"))
        if attrs["rate_type"] == "FLOATING" and attrs["instalment_based"]:
            edges += [edge(pid, "process:rate-reset", "USES"), edge(pid, "process:borrower-notification", "USES")]
        if attrs["secured"]:
            edges += [edge(pid, "process:collateral-release", "USES"), edge(pid, "process:document-custody", "USES")]
        for control in PROVIDES.get(pid, []):
            edges.append(edge(pid, f"control:{control}", "PROVIDES"))
    # Bitemporal history: DriveNow offered a fixed-rate switch until mid-2024.
    edges.append(edge("product:auto-loan-floating", "control:fixed-rate-option", "PROVIDES",
                      valid_from="2021-01-01", valid_to="2024-07-01", change_ticket="CHG-2024-0612"))
    for pid, name, domain, owner in PROCESSES:
        nodes.append(node(pid, "Process", name, bian_domain=domain))
        edges.append(edge(pid, owner, "OWNED_BY"))
    nodes += [node(cid, "Control", name) for cid, name in CONTROLS]
    nodes += [node(tid, "Team", name) for tid, name in TEAMS]
    for sid, name, synonyms in SEGMENTS:
        nodes.append(node(sid, "CustomerSegment", name, synonyms=synonyms))
    for pid, _, attrs, _, _ in PRODUCTS:
        segment = "segment:msme" if attrs["category"] == "MSME_LOAN" else "segment:retail-individual"
        edges.append(edge(segment, pid, "ELIGIBLE_FOR"))
    nodes += [node(bid, "Branch", f"{name}, {city}", city=city, region=region) for bid, name, city, region in BRANCHES]
    return {"tenant_id": "demo-bank", "nodes": nodes, "edges": edges}


def operational(rng: random.Random) -> dict[str, list[dict]]:
    branches = [
        {"branch_id": bid, "branch_name": name, "city": city, "region": region,
         "manager_name": f"{rng.choice(FIRST)} {rng.choice(LAST)}", "manager_email": f"mgr.{bid.split(':')[1]}@bank.example.com"}
        for bid, name, city, region in BRANCHES
    ]
    customers = []
    for index in range(120):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        segment = "segment:msme" if index >= 110 else "segment:retail-individual"
        customers.append({
            "customer_id": f"CIF-{100001 + index}", "customer_ref": f"CUST-{rng.getrandbits(24):06X}",
            "full_name": f"{first} {last}", "email": f"{first.lower()}.{last.lower()}{index}@mail.example.com",
            "phone": f"+91-90000{index:05d}", "pan": f"ZZ{chr(65 + index % 26)}PX{1000 + index}Q",
            "segment_id": segment, "branch_id": rng.choices([b[0] for b in BRANCHES], weights=[3, 2, 2, 1, 1, 1])[0],
        })
    retail = [c for c in customers if c["segment_id"] == "segment:retail-individual"]
    msme = [c for c in customers if c["segment_id"] == "segment:msme"]
    loans: list[dict] = []
    events: list[dict] = []

    def add_loan(customer, product_id, status, sanctioned_on, closed_on=None, released_on=None, **flags):
        attrs = next(p[2] for p in PRODUCTS if p[0] == product_id)
        floating_emi = attrs["rate_type"] == "FLOATING" and attrs["instalment_based"]
        loan = {
            "loan_id": f"LN-{len(loans) + 1001}", "loan_ref": f"LR-{rng.getrandbits(28):07X}",
            "customer_id": customer["customer_id"], "product_id": product_id, "status": status,
            "rate_type": attrs["rate_type"], "emi_amount": rng.randrange(4000, 60000, 50) if attrs["instalment_based"] else 0,
            "outstanding_balance": 0 if status == "CLOSED" else rng.randrange(80000, 6000000, 1000),
            "sanctioned_on": sanctioned_on.isoformat(), "closed_on": closed_on.isoformat() if closed_on else "",
            "rate_reset_due": "2026-11-01" if floating_emi and status == "ACTIVE" else "",
            "borrower_notified": flags.get("borrower_notified", False),
            "has_fixed_rate_option": flags.get("has_fixed_rate_option", False),
            "has_emi_tenor_choice": flags.get("has_emi_tenor_choice", False),
            "statement_available": flags.get("statement_available", False),
        }
        loans.append(loan)
        events.append({"loan_id": loan["loan_id"], "event_type": "LOAN_DISBURSED", "occurred_on": loan["sanctioned_on"]})
        if floating_emi and status == "ACTIVE":
            events.append({"loan_id": loan["loan_id"], "event_type": "RATE_RESET", "occurred_on": "2026-07-01"})
            if loan["borrower_notified"]:
                events.append({"loan_id": loan["loan_id"], "event_type": "RATE_RESET_NOTICE_SENT", "occurred_on": "2026-07-04"})
        if closed_on:
            events.append({"loan_id": loan["loan_id"], "event_type": "LOAN_CLOSED", "occurred_on": closed_on.isoformat()})
        if released_on:
            events.append({"loan_id": loan["loan_id"], "event_type": "DOCUMENTS_RELEASED", "occurred_on": released_on.isoformat()})

    def sanctioned():
        return date(2019, 1, 1) + timedelta(days=rng.randrange(0, 1500))

    # Active floating-rate EMI book (rate reset scenario).
    for product_id, count in [("product:personal-loan-floating", 18), ("product:home-loan-floating", 22),
                              ("product:auto-loan-floating", 14)]:
        for _ in range(count):
            add_loan(rng.choice(retail), product_id, "ACTIVE", sanctioned(),
                     borrower_notified=rng.random() < 0.7,
                     has_fixed_rate_option=product_id != "product:auto-loan-floating",
                     has_emi_tenor_choice=rng.random() < 0.95,
                     statement_available=product_id != "product:personal-loan-floating" and rng.random() < 0.85)
    for _ in range(8):  # MSME floating loans: out of scope for personal-loan rules.
        add_loan(rng.choice(msme), "product:msme-term-loan", "ACTIVE", sanctioned(), borrower_notified=rng.random() < 0.5,
                 has_emi_tenor_choice=True)
    for product_id, count in [("product:personal-loan-fixed", 10), ("product:loan-against-property", 8), ("product:gold-loan", 6)]:
        for _ in range(count):
            add_loan(rng.choice(retail), product_id, "ACTIVE", sanctioned(), has_emi_tenor_choice=True)

    # Closed secured loans (document release scenario).
    secured_retail = ["product:home-loan-floating", "product:auto-loan-floating", "product:loan-against-property", "product:gold-loan"]
    koramangala = [c for c in retail if c["branch_id"] == "branch:blr-koramangala"]
    others = [c for c in retail if c["branch_id"] != "branch:blr-koramangala"]

    def closed_between(start: date, end: date) -> date:
        return start + timedelta(days=rng.randrange(0, (end - start).days))

    plan = (
        [("unreleased", koramangala)] * 4 + [("late", koramangala)] * 3 +       # 7 violations at Koramangala
        [("unreleased", others)] * 3 + [("late", others)] * 2 +                 # 5 violations elsewhere
        [("pre_effective_late", retail)] * 4 + [("boundary", retail)] * 3 +
        [("not_yet_due", retail)] * 3 + [("on_time", retail)] * 44
    )
    for kind, pool in plan:
        customer, product_id = rng.choice(pool), rng.choice(secured_retail)
        if kind == "unreleased":
            closed = closed_between(DOC_RULE_EFFECTIVE, REFERENCE_DATE - timedelta(days=45))
            add_loan(customer, product_id, "CLOSED", sanctioned(), closed)
        elif kind == "late":
            closed = closed_between(DOC_RULE_EFFECTIVE, REFERENCE_DATE - timedelta(days=120))
            add_loan(customer, product_id, "CLOSED", sanctioned(), closed, closed + timedelta(days=rng.randrange(31, 90)))
        elif kind == "pre_effective_late":
            closed = closed_between(date(2023, 6, 1), DOC_RULE_EFFECTIVE - timedelta(days=1))
            add_loan(customer, product_id, "CLOSED", sanctioned(), closed, closed + timedelta(days=rng.randrange(40, 80)))
        elif kind == "boundary":
            closed = closed_between(DOC_RULE_EFFECTIVE, REFERENCE_DATE - timedelta(days=60))
            add_loan(customer, product_id, "CLOSED", sanctioned(), closed, closed + timedelta(days=30))
        elif kind == "not_yet_due":
            add_loan(customer, product_id, "CLOSED", sanctioned(), REFERENCE_DATE - timedelta(days=rng.randrange(3, 25)))
        else:
            closed = closed_between(DOC_RULE_EFFECTIVE, REFERENCE_DATE - timedelta(days=35))
            add_loan(customer, product_id, "CLOSED", sanctioned(), closed, closed + timedelta(days=rng.randrange(0, 29)))
    for _ in range(3):  # Late MSME releases: outside the personal-loan circular.
        closed = closed_between(DOC_RULE_EFFECTIVE, REFERENCE_DATE - timedelta(days=100))
        add_loan(rng.choice(msme), "product:msme-term-loan", "CLOSED", sanctioned(), closed, closed + timedelta(days=70))
    for _ in range(6):  # Closed unsecured loans: no documents to release.
        add_loan(rng.choice(retail), "product:personal-loan-floating", "CLOSED", sanctioned(),
                 closed_between(DOC_RULE_EFFECTIVE, REFERENCE_DATE - timedelta(days=40)))
    for index, event in enumerate(events, start=1):
        event["event_id"] = f"EV-{index:05d}"
    return {"branches": branches, "customers": customers, "loans": loans, "loan_events": events}


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: (str(v).lower() if isinstance(v, bool) else v) for k, v in row.items()})


def main() -> None:
    rng = random.Random(SEED)
    (BANK / "architecture.json").write_text(json.dumps(architecture(), indent=1) + "\n")
    tables = operational(rng)
    for name, rows in tables.items():
        write_csv(BANK / "operational" / f"{name}.csv", rows)
    manifest = {
        "reference_date": REFERENCE_DATE.isoformat(), "seed": SEED, "synthetic": True,
        "rows": {name: len(rows) for name, rows in tables.items()},
        "planted": {"rbi_2023_24_60_document_release_violations": 12, "of_which_koramangala": 7},
    }
    (BANK / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
