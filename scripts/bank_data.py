"""Static reference data for the synthetic bank (branches, products, loan profiles). Used by generate_synthetic_bank.py.

Everything here is fictional. See docs/evals/bank-baseline.md for what the generated bank looks like and which exceptions
are planted.
"""

from __future__ import annotations

# (branch_id, name, city, region, customer weight)
BRANCHES = [
    ("branch:blr-koramangala", "Koramangala", "Bengaluru", "South", 5),
    ("branch:mum-andheri", "Andheri East", "Mumbai", "West", 4),
    ("branch:del-cp", "Connaught Place", "New Delhi", "North", 4),
    ("branch:chn-tnagar", "T Nagar", "Chennai", "South", 3),
    ("branch:hyd-banjara", "Banjara Hills", "Hyderabad", "South", 3),
    ("branch:kol-saltlake", "Salt Lake", "Kolkata", "East", 3),
    ("branch:blr-whitefield", "Whitefield", "Bengaluru", "South", 3),
    ("branch:kochi-mgroad", "MG Road", "Kochi", "South", 3),
    ("branch:cbe-rspuram", "RS Puram", "Coimbatore", "South", 2),
    ("branch:mum-bkc", "Bandra Kurla Complex", "Mumbai", "West", 3),
    ("branch:pune-kothrud", "Kothrud", "Pune", "West", 3),
    ("branch:ahd-cgroad", "CG Road", "Ahmedabad", "West", 3),
    ("branch:surat-adajan", "Adajan", "Surat", "West", 2),
    ("branch:ggn-sector29", "Sector 29", "Gurugram", "North", 3),
    ("branch:jaipur-cscheme", "C-Scheme", "Jaipur", "North", 2),
    ("branch:lko-hazratganj", "Hazratganj", "Lucknow", "North", 3),
    ("branch:chd-sector17", "Sector 17", "Chandigarh", "North", 2),
    ("branch:bbsr-saheednagar", "Saheed Nagar", "Bhubaneswar", "East", 2),
    ("branch:patna-boring", "Boring Road", "Patna", "East", 2),
    ("branch:guwahati-gs", "GS Road", "Guwahati", "East", 2),
]

# Product nodes: (id, name, attributes, synonyms, owner team). "advances" is a synonym of every credit product so that
# a rule addressed to "loans and advances" resolves to the whole loan book.
ALL_ADVANCES = "advances"
PRODUCTS = [
    ("product:personal-loan-floating", "Flexi Personal Loan", dict(rate_type="FLOATING", instalment_based=True, secured=False, collateral_type="NONE", category="RETAIL_LOAN"), ["personal loan", "consumer loan", "floating rate personal loan", "emi loan"], "team:retail-lending"),
    ("product:home-loan-floating", "EasyHome Floating Loan", dict(rate_type="FLOATING", instalment_based=True, secured=True, collateral_type="IMMOVABLE", category="RETAIL_LOAN"), ["home loan", "housing loan", "mortgage", "emi loan"], "team:retail-lending"),
    ("product:auto-loan-floating", "DriveNow Floating Loan", dict(rate_type="FLOATING", instalment_based=True, secured=True, collateral_type="MOVABLE", category="RETAIL_LOAN"), ["auto loan", "car loan", "vehicle loan", "four-wheeler advances", "two-wheeler loan", "emi loan"], "team:vehicle-finance"),
    ("product:auto-loan-fixed", "DriveNow Fixed Loan", dict(rate_type="FIXED", instalment_based=True, secured=True, collateral_type="MOVABLE", category="RETAIL_LOAN"), ["fixed rate vehicle loan", "fixed rate car loan", "emi loan"], "team:vehicle-finance"),
    ("product:personal-loan-fixed", "Flexi Personal Loan Fixed", dict(rate_type="FIXED", instalment_based=True, secured=False, collateral_type="NONE", category="RETAIL_LOAN"), ["personal loan", "fixed rate personal loan", "emi loan"], "team:retail-lending"),
    ("product:education-loan", "EduAssist Education Loan", dict(rate_type="FLOATING", instalment_based=True, secured=False, collateral_type="NONE", category="RETAIL_LOAN"), ["education loan", "student loan", "emi loan"], "team:retail-lending"),
    ("product:loan-against-property", "PropertyPlus LAP", dict(rate_type="FIXED", instalment_based=True, secured=True, collateral_type="IMMOVABLE", category="RETAIL_LOAN"), ["loan against property", "lap", "mortgage loan", "property loan"], "team:retail-lending"),
    ("product:gold-loan", "GoldEase Loan", dict(rate_type="FIXED", instalment_based=False, secured=True, collateral_type="MOVABLE", category="RETAIL_LOAN"), ["gold loan", "loan against gold jewellery", "jewel loan"], "team:retail-lending"),
    ("product:credit-card", "Everyday Credit Card", dict(rate_type="FIXED", instalment_based=False, secured=False, collateral_type="NONE", category="CARD"), ["credit card", "card receivables", "card outstanding"], "team:cards"),
    ("product:msme-working-capital", "MSME Working Capital (CC/OD)", dict(rate_type="FLOATING", instalment_based=False, secured=True, collateral_type="MOVABLE", category="MSME_LOAN"), ["working capital", "cash credit", "overdraft", "cc od limit"], "team:msme-banking"),
    ("product:msme-term-loan", "MSME Growth Term Loan", dict(rate_type="FLOATING", instalment_based=True, secured=True, collateral_type="IMMOVABLE", category="MSME_LOAN"), ["msme loan", "business term loan", "sme loan"], "team:msme-banking"),
    ("product:agri-kcc", "KisanSure Kisan Credit Card", dict(rate_type="FLOATING", instalment_based=False, secured=True, collateral_type="IMMOVABLE", category="AGRI_LOAN"), ["kisan credit card", "kcc", "crop loan", "farm credit", "agricultural advances"], "team:agri-banking"),
]

PROCESSES = [
    ("process:rate-reset", "Benchmark Rate Reset", "Consumer Loan", "team:lending-ops"),
    ("process:borrower-notification", "Borrower Correspondence", "Correspondence", "team:customer-comms"),
    ("process:loan-closure", "Loan Closure and Settlement", "Consumer Loan", "team:lending-ops"),
    ("process:collateral-release", "Collateral Release", "Collateral Asset Administration", "team:collateral-ops"),
    ("process:document-custody", "Original Document Custody", "Document Services", "team:collateral-ops"),
    ("process:asset-classification", "Day-end Asset Classification", "Credit Risk Models", "team:credit-risk"),
    ("process:provisioning", "Provisioning and Loss Allowance", "Financial Accounting", "team:finance"),
    ("process:income-recognition", "Interest Income Recognition", "Financial Accounting", "team:finance"),
    ("process:restructuring-resolution", "Resolution Plans and Restructuring", "Loan Restructuring", "team:collections-recovery"),
    ("process:collections-recovery", "Collections and Recovery", "Collections", "team:collections-recovery"),
    ("process:kyc-periodic-review", "Periodic KYC Updation", "Party Authentication", "team:kyc-aml"),
    ("process:customer-grievance", "Customer Grievance Redressal", "Customer Case Management", "team:customer-service"),
    ("process:credit-underwriting", "Credit Appraisal and Sanction", "Consumer Loan", "team:credit-risk"),
    ("process:model-governance", "Model Development, Validation and Monitoring", "Model Management", "team:model-risk"),
    ("process:regulatory-reporting", "Regulatory and Supervisory Reporting", "Regulatory Reporting", "team:compliance"),
]

TEAMS = [
    ("team:retail-lending", "Retail Lending"), ("team:vehicle-finance", "Vehicle Finance"),
    ("team:msme-banking", "MSME Banking"), ("team:lending-ops", "Lending Operations"),
    ("team:customer-comms", "Customer Communications"), ("team:collateral-ops", "Collateral Operations"),
    ("team:cards", "Cards"), ("team:agri-banking", "Agri and Rural Banking"),
    ("team:credit-risk", "Credit Risk"), ("team:finance", "Finance and Accounts"),
    ("team:collections-recovery", "Collections and Recovery"), ("team:kyc-aml", "KYC and Financial Crime"),
    ("team:customer-service", "Customer Service"), ("team:model-risk", "Model Risk Management"),
    ("team:compliance", "Compliance"), ("team:internal-audit", "Internal Audit"),
    ("team:it-core-banking", "Core Banking Technology"), ("team:vendor-management", "Vendor Management"),
]

# Rate-reset controls offered per product (the PROVIDES edges of the rate-reset and document-release scenarios).
PROVIDES = {
    "product:personal-loan-floating": ["borrower-notification", "emi-tenor-choice", "fixed-rate-option", "reset-statement"],
    "product:home-loan-floating": ["borrower-notification", "emi-tenor-choice", "fixed-rate-option", "reset-statement",
                                    "document-release-sla", "delay-compensation", "document-collection-choice"],
    "product:auto-loan-floating": ["borrower-notification", "emi-tenor-choice", "reset-statement", "document-release-sla",
                                    "delay-compensation", "document-collection-choice"],
    "product:auto-loan-fixed": ["document-release-sla", "delay-compensation", "document-collection-choice"],
    "product:education-loan": ["borrower-notification", "emi-tenor-choice", "fixed-rate-option", "reset-statement"],
    "product:loan-against-property": ["document-release-sla", "delay-compensation", "document-collection-choice"],
    "product:gold-loan": ["document-release-sla", "delay-compensation", "document-collection-choice"],
    "product:msme-term-loan": ["borrower-notification", "emi-tenor-choice"],
}

# Per-product loan profile for ACTIVE accounts.
#   count        active accounts
#   amount       (low, high, mode) outstanding balance in INR
#   round_to     rounding of the balance
#   emi          instalment as a share of the outstanding balance (0 = not instalment based)
#   years        (earliest, latest) sanction year; latest is capped at the reference date
#   secured      ab-initio security flag, or "threshold:<INR>" = secured only when the balance is above the threshold
#   security     (low, high) of security value / balance for a performing account (None = no security)
#   exposure     provisioning exposure class (IRACP para 80)
#   dpd          probability of (SMA0, SMA1, SMA2, NPA) for an average branch
PROFILES = {
    "product:home-loan-floating": dict(count=640, amount=(1_500_000, 12_000_000, 3_500_000), round_to=1000, emi=0.0095, years=(2012, 2026),
                                       secured=True, security=(1.15, 1.8), exposure="INDIVIDUAL_HOUSING", dpd=(0.030, 0.008, 0.004, 0.011)),
    "product:auto-loan-floating": dict(count=240, amount=(250_000, 1_800_000, 700_000), round_to=1000, emi=0.022, years=(2021, 2026),
                                       secured=True, security=(1.05, 1.3), exposure="OTHER", dpd=(0.045, 0.012, 0.006, 0.022)),
    "product:auto-loan-fixed": dict(count=230, amount=(250_000, 1_600_000, 650_000), round_to=1000, emi=0.022, years=(2021, 2026),
                                    secured=True, security=(1.05, 1.3), exposure="OTHER", dpd=(0.045, 0.012, 0.006, 0.022)),
    "product:personal-loan-floating": dict(count=360, amount=(80_000, 1_500_000, 350_000), round_to=1000, emi=0.025, years=(2022, 2026),
                                           secured=False, security=None, exposure="OTHER", dpd=(0.050, 0.015, 0.008, 0.030)),
    "product:personal-loan-fixed": dict(count=410, amount=(60_000, 1_200_000, 300_000), round_to=1000, emi=0.025, years=(2022, 2026),
                                        secured=False, security=None, exposure="OTHER", dpd=(0.050, 0.015, 0.008, 0.030)),
    "product:education-loan": dict(count=170, amount=(200_000, 3_000_000, 900_000), round_to=1000, emi=0.012, years=(2019, 2026),
                                   secured="threshold:750000", security=(1.0, 1.4), exposure="OTHER", dpd=(0.040, 0.015, 0.010, 0.035)),
    "product:gold-loan": dict(count=330, amount=(30_000, 800_000, 150_000), round_to=100, emi=0.0, years=(2025, 2026),
                              secured=True, security=(1.30, 1.5), exposure="OTHER", dpd=(0.030, 0.006, 0.002, 0.009)),
    "product:credit-card": dict(count=380, amount=(8_000, 250_000, 40_000), round_to=100, emi=0.0, years=(2018, 2026),
                                secured=False, security=None, exposure="OTHER", dpd=(0.080, 0.030, 0.020, 0.045)),
    "product:msme-working-capital": dict(count=200, amount=(500_000, 15_000_000, 3_000_000), round_to=10_000, emi=0.0, years=(2019, 2026),
                                         secured=True, security=(1.0, 1.3), exposure="MSME", dpd=(0.040, 0.012, 0.008, 0.030)),
    "product:msme-term-loan": dict(count=190, amount=(800_000, 20_000_000, 4_000_000), round_to=10_000, emi=0.017, years=(2018, 2026),
                                   secured=True, security=(1.1, 1.5), exposure="MSME", dpd=(0.035, 0.012, 0.008, 0.028)),
    "product:agri-kcc": dict(count=210, amount=(40_000, 1_600_000, 250_000), round_to=1000, emi=0.0, years=(2023, 2026),
                             secured="threshold:200000", security=(1.2, 2.0), exposure="FARM_CREDIT", dpd=(0.050, 0.020, 0.010, 0.0)),
    "product:loan-against-property": dict(count=190, amount=(1_500_000, 20_000_000, 4_500_000), round_to=1000, emi=0.011, years=(2016, 2026),
                                          secured=True, security=(1.3, 2.0), exposure="OTHER", dpd=(0.025, 0.008, 0.004, 0.013)),
}
PRODUCT_ORDER = list(PROFILES)

# Branches with a worse asset quality (NPA probability multiplier). Pune and Lucknow are the two branches where
# the planted provisioning shortfalls sit; Patna, Surat and Jaipur hold the planted mis-classifications.
STRESSED_BRANCHES = {"branch:pune-kothrud": 3.0, "branch:lko-hazratganj": 3.0, "branch:patna-boring": 1.6,
                     "branch:surat-adajan": 1.4, "branch:jaipur-cscheme": 1.4}
# Calamity-affected branches: resolution plans (restructuring) under Chapter VI-A of the Resolution of Stressed Assets Directions.
CALAMITY_BRANCHES = ["branch:guwahati-gs", "branch:kochi-mgroad", "branch:bbsr-saheednagar", "branch:patna-boring"]

FIRST = ["Aarav", "Diya", "Vihaan", "Ananya", "Ishaan", "Meera", "Kabir", "Saanvi", "Arjun", "Riya", "Dev", "Kavya",
         "Rohan", "Nisha", "Aditya", "Pooja", "Karthik", "Lakshmi", "Farhan", "Zoya", "Siddharth", "Tara", "Imran", "Neha",
         "Varun", "Isha", "Manoj", "Sunita", "Rahul", "Fatima"]
LAST = ["Sharma", "Iyer", "Reddy", "Banerjee", "Patel", "Nair", "Khan", "Gupta", "Menon", "Das", "Rao", "Kulkarni",
        "Singh", "Joshi", "Mehta", "Chatterjee", "Pillai", "Naidu", "Verma", "Bose"]
