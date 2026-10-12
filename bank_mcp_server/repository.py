"""Read-only operational data access used by the analyser and MCP server."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any


DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "bank" / "loans.csv"
SAFE_FIELDS = {
    "loan_id", "customer_ref", "product_id", "status", "rate_type", "emi_amount",
    "outstanding_balance", "rate_reset_due", "borrower_notified", "has_fixed_rate_option",
    "has_emi_tenor_choice", "statement_available",
}


class ReadOnlyBankRepository:
    def __init__(self, data_file: str | Path = DATA_FILE) -> None:
        self.data_file = Path(data_file)

    def active_floating_loans(self, product_ids: list[str]) -> list[dict[str, Any]]:
        with self.data_file.open(newline="") as source:
            loans = [dict(row) for row in csv.DictReader(source)]
        return [
            {key: self._coerce(key, value) for key, value in loan.items() if key in SAFE_FIELDS}
            for loan in loans
            if loan["product_id"] in product_ids and loan["status"] == "ACTIVE" and loan["rate_type"] == "FLOATING"
        ]

    def count_by_product(self, loans: list[dict[str, Any]]) -> dict[str, int]:
        return dict(Counter(loan["product_id"] for loan in loans))

    @staticmethod
    def _coerce(field: str, value: str) -> Any:
        if field in {"emi_amount", "outstanding_balance"}:
            return int(value)
        if field.startswith("has_") or field == "borrower_notified" or field == "statement_available":
            return value.lower() == "true"
        return value
