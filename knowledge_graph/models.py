"""Typed contracts shared by the parser, mapper, and analyser.

These structures intentionally contain no customer PII.  Customer-level data is
kept in the bank repository and is only joined during a read-only analysis.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class RegulatoryRule:
    rule_id: str
    circular_id: str
    title: str
    effective_date: str
    applies_to: tuple[str, ...]
    requirements: tuple[str, ...]
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MappingResult:
    rule: RegulatoryRule
    product_ids: list[str]
    process_ids: list[str]
    control_ids: list[str]
    missing_capabilities: dict[str, list[str]]
    paths: list[list[str]]
    rationale: list[str]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["rule"] = self.rule.to_dict()
        return result


@dataclass
class ImpactReport:
    circular_id: str
    analysed_at: str
    affected_products: list[dict[str, Any]]
    impacted_loans: list[dict[str, Any]]
    summary: dict[str, int]
    product_gaps: dict[str, list[str]]
    required_actions: list[str]
    mapping_evidence: MappingResult
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["mapping_evidence"] = self.mapping_evidence.to_dict()
        return result
