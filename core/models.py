"""Typed contracts shared by every agent, tool and service.

The extraction models double as the Gemini ``response_schema`` so the Reader
agent's structured output and the deterministic validator agree on one shape.
None of these models carry customer PII; row-level worklists stay inside the
secure execution engine and are referenced by ``worklist_id`` only.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

CheckKind = Literal["control_required", "record_flag", "event_deadline", "record_assertion"]
RateType = Literal["FLOATING", "FIXED"]
CollateralType = Literal["IMMOVABLE", "MOVABLE", "NONE"]
ProductCategory = Literal["RETAIL_LOAN", "MSME_LOAN", "AGRI_LOAN", "CARD", "DEPOSIT"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
RuleStatus = Literal["PENDING_REVIEW", "APPROVED", "REJECTED", "SUPERSEDED"]
Modality = Literal["MUST", "MAY", "MUST_NOT"]
ScopeStatus = Literal["RESOLVED", "UNRESOLVED"]
CheckStatus = Literal["COMPLIANT", "GAP", "NOT_EVALUATED", "UPCOMING"]
DocumentType = Literal["circular", "master_direction", "draft_or_consultation", "press_release", "faq", "notification", "other"]
ReferenceRelation = Literal["amends", "supersedes", "refers_to"]
# Report-level roll-up (summary["overall_status"]): NON_COMPLIANT | UPCOMING | INCONCLUSIVE | COMPLIANT


class SourceCitation(BaseModel):
    document_id: str
    chunk_id: Optional[str] = None
    page: Optional[int] = None
    snippet: str = Field(description="Exact text from the source that supports the rule.")


class ProductScope(BaseModel):
    """Which products a rule governs: free-text terms resolved to canonical
    BIAN products, narrowed by explicit attribute filters."""

    product_terms: list[str] = Field(default_factory=list, description="Product phrases as written, e.g. 'four-wheeler advances'.")
    rate_type: Optional[RateType] = None
    instalment_based: Optional[bool] = None
    secured: Optional[bool] = None
    collateral_type: Optional[CollateralType] = None
    category: Optional[ProductCategory] = None
    record_status: Optional[Literal["ACTIVE", "CLOSED"]] = Field(
        default=None, description="Operational loan status the rule applies to, if any.")


class ComplianceCheck(BaseModel):
    """Executable, queryable form of one obligation inside a rule."""

    kind: CheckKind
    description: str
    capability: Optional[str] = Field(default=None, description="control_required: capability id, e.g. fixed_rate_option.")
    capability_description: Optional[str] = Field(
        default=None, description="control_required: one sentence on what the capability does, in the circular's terms.")
    control_id: Optional[str] = Field(
        default=None, description="Canonical Control node id. Set by the mapper after canonicalisation; leave empty.")
    record_field: Optional[str] = Field(default=None, description="record_flag: boolean field on vw_compliance_impact.")
    expected: Optional[bool] = Field(default=None, description="record_flag: the compliant value.")
    trigger_event: Optional[str] = Field(default=None, description="event_deadline: event that starts the clock.")
    required_event: Optional[str] = Field(default=None, description="event_deadline: event that must follow.")
    deadline_days: Optional[int] = None
    penalty_per_day_inr: Optional[float] = None
    view: Optional[str] = Field(default=None, description="record_assertion: sanitized view the expressions run on, e.g. vw_asset_quality.")
    where: Optional[str] = Field(
        default=None, description="record_assertion: boolean expression selecting the rows in scope (columns of `view`, "
                                  "comparisons, AND/OR/NOT, IN, arithmetic; no functions). Omit to test every row.")
    assertion: Optional[str] = Field(
        default=None, description="record_assertion: boolean expression every in-scope row must satisfy (same grammar "
                                  "as `where`). JSON key may be spelled `assert`.")
    tolerance: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="record_assertion: share of in-scope rows allowed to fail before the check is a GAP "
                                                  "(default 0).")

    @model_validator(mode="before")
    @classmethod
    def _assert_alias(cls, data):
        """`assert` is a Python keyword; accept it as the JSON spelling of ``assertion``."""
        if isinstance(data, dict) and "assert" in data:
            data = {**data}
            data.setdefault("assertion", data.pop("assert"))
            data.pop("assert", None)
        return data


class Rule(BaseModel):
    rule_id: str
    title: str
    description: str
    effective_date: str = Field(description="ISO date (YYYY-MM-DD) from which the rule applies.")
    scope: ProductScope
    processes: list[str] = Field(default_factory=list, description="Business process phrases, e.g. 'loan closure'.")
    segments: list[str] = Field(default_factory=list, description="Customer segment phrases, e.g. 'individual borrowers'.")
    checks: list[ComplianceCheck]
    penalty_risk: RiskLevel = "MEDIUM"
    modality: Modality = Field(default="MUST", description="MUST = obligation, MAY = permission or option for the bank, MUST_NOT = prohibition.")
    citation: SourceCitation
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    status: RuleStatus = "PENDING_REVIEW"


class Policy(BaseModel):
    policy_id: str
    title: str
    issuer: str
    reference: str
    issued_on: Optional[str] = None
    effective_date: str
    source_uri: Optional[str] = None
    supersedes: list[str] = Field(default_factory=list)


class UnexecutableObligation(BaseModel):
    """A binding obligation that no available check kind or data can test. Reported, never turned into a fake check."""

    citation: str = Field(description="The obligation's text, copied VERBATIM from the source.")
    reason: str = Field(description="Why no available check kind or data can test it.")
    missing_data: list[str] = Field(default_factory=list, description="Data that would be needed to test it.")


class ExtractedPolicy(BaseModel):
    policy: Policy
    rules: list[Rule]
    unexecutable: list[UnexecutableObligation] = Field(
        default_factory=list, description="Obligations that cannot be expressed with the available checks or data.")


class DocumentChunk(BaseModel):
    chunk_id: str
    content: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    headings: list[str] = Field(default_factory=list)


class ParsedDocument(BaseModel):
    document_id: str
    source_uri: str
    mime_type: str
    title: str
    parser: str
    chunks: list[DocumentChunk]

    @property
    def text(self) -> str:
        return "\n\n".join(chunk.content for chunk in self.chunks)


class DocumentClassification(BaseModel):
    """What kind of regulatory document this is. Only binding instruments may yield obligations."""

    doc_type: DocumentType
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    method: str = "deterministic"
    signals: list[str] = Field(default_factory=list)


class DocumentReference(BaseModel):
    """Another circular or direction the document cites (surfaced, never fetched automatically)."""

    ref: str
    url: Optional[str] = None
    relation: ReferenceRelation = "refers_to"


class Applicability(BaseModel):
    """Whether a rule applies to this bank. ``applicable=None`` means the engine cannot tell."""

    rule_id: str
    applicable: Optional[bool]
    reasons: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    severity: Literal["ERROR", "WARNING"]
    rule_id: Optional[str] = None
    field: str
    message: str


class EntityMatch(BaseModel):
    term: str
    node_id: Optional[str]
    node_type: str
    score: float
    method: str


class ControlDecision(BaseModel):
    """How one extracted capability was resolved to a canonical Control node (L7)."""

    rule_id: Optional[str] = None
    check_index: Optional[int] = None
    term: str
    control_id: str
    decision: Literal["EXACT", "SYNONYM", "MATCH", "NEW"]
    method: str
    confidence: float = 0.0
    needs_review: bool = False
    rationale: str = ""
    model_decision: Optional[str] = None  # MATCH | NEW | AMBIGUOUS as returned by Gemini, when it was asked
    candidates: list[dict] = Field(default_factory=list)


class RuleMapping(BaseModel):
    rule_id: str
    product_ids: list[str]
    process_ids: list[str]
    segment_ids: list[str]
    control_ids: list[str]
    resolutions: list[EntityMatch]
    edges_written: list[dict]
    verification_query: str
    scope_status: ScopeStatus = "RESOLVED"
    scope_notes: list[str] = Field(default_factory=list)
    control_decisions: list[ControlDecision] = Field(default_factory=list)


class BlastRadius(BaseModel):
    rule_id: str
    as_of: str
    products: list[dict]
    processes: list[dict]
    controls_required: list[str]
    control_gaps: dict[str, list[str]]
    owners: list[dict]
    segments: list[dict]
    paths: list[list[str]]
    query_language: str
    queries: list[str]
    scope_status: ScopeStatus = "RESOLVED"
    scope_notes: list[str] = Field(default_factory=list)
    applicability: Optional[Applicability] = None


class CheckResult(BaseModel):
    rule_id: str
    check_index: int
    kind: CheckKind
    description: str
    status: CheckStatus
    in_scope: int = 0
    violations: int = 0
    exposure_inr: float = 0.0
    penalty_inr: float = 0.0
    by_branch: dict[str, int] = Field(default_factory=dict)
    by_product: dict[str, int] = Field(default_factory=dict)
    products_missing_control: list[str] = Field(default_factory=list)
    worklist_id: Optional[str] = None
    evidence: str = ""
    missing_data: list[str] = Field(default_factory=list)


class ProposedAction(BaseModel):
    action_id: str
    workflow_id: str
    rule_id: str
    worklist_id: Optional[str]
    target_count: int
    notice_template: str
    owner: str
    status: Literal["PENDING_APPROVAL", "APPROVED", "REJECTED", "DISPATCHED"] = "PENDING_APPROVAL"


class ImpactReport(BaseModel):
    report_id: str
    tenant_id: str
    policy: Policy
    rules: list[Rule]
    generated_at: str
    as_of: str
    mode: str
    blast_radius: list[BlastRadius]
    results: list[CheckResult]
    summary: dict
    actions: list[ProposedAction]
    narrative: str = ""
    limitations: list[str] = Field(default_factory=list)
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    unexecutable: list[UnexecutableObligation] = Field(default_factory=list)
