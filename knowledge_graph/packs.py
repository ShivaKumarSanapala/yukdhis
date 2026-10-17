"""Domain ontology packs: the *content* of the ontology, kept as data.

The meta-model (node and edge types, PII denylist, regulated-entity terms) is code in ``knowledge_graph.ontology``.
Everything about a business domain - the capabilities a rule may require, the record flags and events a check may test,
the sanitized views, the regulatory glossary, the remediation workflows and the applicability keywords - lives in one JSON
file per domain under ``data/ontology/packs/``. A bank enables packs with ``ontology_packs`` in
``data/bank/entity_profile.json``; when that key is absent every pack that is *onboarded* for the profile is enabled
(its business line is ``active`` and every data domain it requires is onboarded).

Packs are validated at load (unknown keys are rejected, errors name the file and the path) and merged with conflict
detection: the same capability id, record flag, view name, workflow id, event process or glossary term defined by two
packs with different content is a load error, never a silent override.

Tenant overlays are packs named ``tenant_<tenant_id>.json``. They hold vocabulary a bank's reviewers promoted from LLM
proposals (``scripts/ontology_proposals.py``) and are always enabled for their own tenant.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from core.config import DATA_DIR

PACK_DIR = DATA_DIR / "ontology" / "packs"
PROFILE_PATH = DATA_DIR / "bank" / "entity_profile.json"
SCHEMA_VERSION = 1
TENANT_PREFIX = "tenant_"

_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_CONTROL = re.compile(r"^control:[a-z0-9][a-z0-9-]*$")
_PROCESS = re.compile(r"^process:[a-z0-9][a-z0-9-]*$")
_UPPER = re.compile(r"^[A-Z][A-Z0-9_]*$")


class PackError(ValueError):
    """A pack file is malformed. The message names the file and the path of every problem."""


class PackConflictError(PackError):
    """Two packs define the same vocabulary item differently."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapabilitySpec(_Strict):
    control: str
    description: str = ""
    process: Optional[str] = None
    synonyms: list[str] = Field(default_factory=list)

    @field_validator("control")
    @classmethod
    def _control(cls, value: str) -> str:
        if not _CONTROL.match(value):
            raise ValueError("must look like 'control:<slug>' (lowercase letters, digits, hyphens)")
        return value

    @field_validator("process")
    @classmethod
    def _process(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _PROCESS.match(value):
            raise ValueError("must be null or look like 'process:<slug>'")
        return value


class ViewSpec(_Strict):
    description: str
    columns: dict[str, str]
    data_domain: Optional[str] = None  # entity_profile data domain this view serves (optional extension of schema v1)

    @field_validator("columns")
    @classmethod
    def _columns(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("a view needs at least one column")
        return value


class SignalSpec(_Strict):
    """Applicability signal: keywords (regular expressions, case-insensitive) that tie a rule to a business line / data."""

    keywords: list[str] = Field(min_length=1)
    business_line: Optional[str] = None
    data_domains: list[str] = Field(default_factory=list)
    licence: Optional[str] = None  # entity_profile ``licences`` key the bank must hold for the signal to apply
    name: Optional[str] = None  # defaults to the pack id
    label: Optional[str] = None  # defaults to the pack title
    order: int = 100  # evaluation order across packs; keeps the reasons of an assessment stable

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, value: list[str]) -> list[str]:
        for keyword in value:
            if not keyword.strip():
                raise ValueError("keywords must not be empty")
            try:
                re.compile(keyword)
            except re.error as error:
                raise ValueError(f"{keyword!r} is not a valid regular expression: {error}") from error
        return value


class OntologyPack(_Strict):
    pack_id: str
    version: str
    title: str
    domain: str
    business_line: Optional[str] = None
    requires_data_domains: list[str] = Field(default_factory=list)
    capabilities: dict[str, CapabilitySpec] = Field(default_factory=dict)
    record_flags: dict[str, str] = Field(default_factory=dict)
    event_types: list[str] = Field(default_factory=list)
    event_processes: dict[str, str] = Field(default_factory=dict)
    product_attributes: list[str] = Field(default_factory=list)
    views: dict[str, ViewSpec] = Field(default_factory=dict)
    glossary: dict[str, dict[str, Union[str, bool, int, float]]] = Field(default_factory=dict)
    workflows: dict[str, str] = Field(default_factory=dict)
    applicability_signals: Union[SignalSpec, list[SignalSpec], None] = None

    @field_validator("pack_id", "domain")
    @classmethod
    def _id(cls, value: str) -> str:
        if not _ID.match(value):
            raise ValueError("must be lowercase snake_case")
        return value

    @field_validator("version")
    @classmethod
    def _version(cls, value: str) -> str:
        if not _SEMVER.match(value):
            raise ValueError("must be a semantic version such as 1.0.0")
        return value

    @field_validator("capabilities", "record_flags", "views")
    @classmethod
    def _snake_keys(cls, value: dict) -> dict:
        bad = [key for key in value if not _ID.match(key)]
        if bad:
            raise ValueError(f"keys must be lowercase snake_case: {bad}")
        return value

    @field_validator("event_types")
    @classmethod
    def _events(cls, value: list[str]) -> list[str]:
        bad = [event for event in value if not _UPPER.match(event)]
        if bad:
            raise ValueError(f"event types must be UPPER_SNAKE_CASE: {bad}")
        return value

    @field_validator("workflows")
    @classmethod
    def _workflows(cls, value: dict[str, str]) -> dict[str, str]:
        bad = [key for key in value if not _UPPER.match(key)]
        if bad:
            raise ValueError(f"workflow ids must be UPPER_SNAKE_CASE: {bad}")
        return value

    @field_validator("event_processes")
    @classmethod
    def _event_processes(cls, value: dict[str, str]) -> dict[str, str]:
        for event, process in value.items():
            if not _UPPER.match(event):
                raise ValueError(f"event {event!r} must be UPPER_SNAKE_CASE")
            if not _PROCESS.match(process):
                raise ValueError(f"{event}: {process!r} must look like 'process:<slug>'")
        return value

    @field_validator("glossary")
    @classmethod
    def _glossary(cls, value: dict[str, dict]) -> dict[str, dict]:
        for term, attributes in value.items():
            if not term.strip() or not attributes:
                raise ValueError(f"glossary term {term!r} needs a name and at least one attribute")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> "OntologyPack":
        known = set(self.event_types)
        orphan = [e for e in self.event_processes if known and e not in known]
        if orphan:
            raise ValueError(f"event_processes names events missing from event_types: {orphan}")
        return self

    # -- convenience ---------------------------------------------------------------------------------------
    @property
    def is_tenant_overlay(self) -> bool:
        return self.pack_id.startswith(TENANT_PREFIX)

    @property
    def signals(self) -> list[SignalSpec]:
        spec = self.applicability_signals
        return [] if spec is None else [spec] if isinstance(spec, SignalSpec) else list(spec)

    @property
    def primary_domains(self) -> tuple[str, ...]:
        """Data domains the pack's own vocabulary (capabilities, flags, events) is evaluated on."""
        signals = self.signals
        return tuple(signals[0].data_domains) if signals else ()

    def counts(self) -> dict[str, int]:
        return {"capabilities": len(self.capabilities), "record_flags": len(self.record_flags),
                "event_types": len(self.event_types), "views": len(self.views), "workflows": len(self.workflows),
                "glossary_terms": len(self.glossary), "signals": len(self.signals)}


# loading --------------------------------------------------------------------------------------------------------
def _format_errors(source: str, error: ValidationError) -> str:
    lines = []
    for item in error.errors():
        path = ".".join(str(part) for part in item["loc"]) or "<root>"
        message = item["msg"]
        if item["type"] == "extra_forbidden":
            message = "unknown key (the pack schema rejects keys it does not define)"
        lines.append(f"{source}: {path}: {message}")
    return "\n".join(lines)


def parse_pack(data: Any, source: str = "<pack>") -> OntologyPack:
    """Validate a decoded pack. Raises ``PackError`` naming ``source`` and the offending path."""
    try:
        return OntologyPack.model_validate(data)
    except ValidationError as error:
        raise PackError(_format_errors(source, error)) from error


def load_pack(path: str | Path) -> OntologyPack:
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise PackError(f"{path.name}: invalid JSON at line {error.lineno} column {error.colno}: {error.msg}") from error
    pack = parse_pack(data, path.name)
    if pack.pack_id != path.stem:
        raise PackError(f"{path.name}: pack_id: {pack.pack_id!r} must equal the file name {path.stem!r}")
    return pack


def _signature(pack_dir: Path) -> tuple:
    if not pack_dir.is_dir():
        return ()
    return tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(pack_dir.glob("*.json")))


@lru_cache(maxsize=8)
def _load_dir(directory: str, signature: tuple) -> dict[str, OntologyPack]:
    packs: dict[str, OntologyPack] = {}
    for name, *_ in signature:
        pack = load_pack(Path(directory) / name)
        packs[pack.pack_id] = pack
    return packs


def load_all_packs(pack_dir: str | Path | None = None) -> dict[str, OntologyPack]:
    """Every pack file in the directory, validated. Cached until a file changes."""
    directory = Path(pack_dir or PACK_DIR)
    return dict(_load_dir(str(directory), _signature(directory)))


def clear_cache() -> None:
    _load_dir.cache_clear()
    _compile_signals.cache_clear()
    _load_profile.cache_clear()


@lru_cache(maxsize=4)
def _load_profile(path: str) -> dict:
    return json.loads(Path(path).read_text())


def load_profile(path: str | Path | None = None) -> dict:
    return _load_profile(str(path or PROFILE_PATH))


def tenant_pack_id(tenant_id: str) -> str:
    return TENANT_PREFIX + re.sub(r"[^a-z0-9]+", "_", tenant_id.lower()).strip("_")


def tenant_pack_path(tenant_id: str, pack_dir: str | Path | None = None) -> Path:
    return Path(pack_dir or PACK_DIR) / f"{tenant_pack_id(tenant_id)}.json"


def is_onboarded(pack: OntologyPack, profile: dict) -> bool:
    """A pack is onboarded when its business line is active and every data domain it requires is onboarded."""
    if pack.business_line is not None and profile.get("business_lines", {}).get(pack.business_line) != "active":
        return False
    return set(pack.requires_data_domains) <= set(profile.get("onboarded_data_domains", []))


def enabled_packs(profile: dict | None = None, pack_dir: str | Path | None = None,
                  tenant_ids: Iterable[str] = ()) -> list[OntologyPack]:
    """Packs a bank uses. ``profile['ontology_packs']`` (a list of pack ids) wins; otherwise every onboarded pack.

    The tenant overlay of the profile's tenant (and of ``tenant_ids``) is always enabled when its file exists.
    """
    profile = load_profile() if profile is None else profile
    available = load_all_packs(pack_dir)
    explicit = profile.get("ontology_packs")
    if explicit is not None:
        if not isinstance(explicit, list) or not all(isinstance(p, str) for p in explicit):
            raise PackError("entity_profile.json: ontology_packs: must be a list of pack ids")
        unknown = [p for p in explicit if p not in available]
        if unknown:
            raise PackError(f"entity_profile.json: ontology_packs: unknown pack id(s) {unknown}; "
                            f"available: {sorted(available)}")
        chosen = list(dict.fromkeys(explicit))
    else:
        chosen = sorted(p.pack_id for p in available.values() if not p.is_tenant_overlay and is_onboarded(p, profile))
    tenants = [t for t in (profile.get("tenant_id"), *tenant_ids) if t]
    for tenant in tenants:
        overlay = tenant_pack_id(tenant)
        if overlay in available and overlay not in chosen:
            chosen.append(overlay)
    return [available[pack_id] for pack_id in chosen]


# merging --------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class MergedVocabulary:
    capabilities: dict[str, str]  # capability id -> control node id
    capability_specs: dict[str, CapabilitySpec]
    record_flags: dict[str, str]
    event_types: frozenset[str]
    event_processes: dict[str, str]
    capability_processes: dict[str, str]
    product_attributes: tuple[str, ...]
    views: dict[str, dict]  # exported form: {"description", "columns"}
    view_domains: dict[str, str]
    glossary: dict[str, dict]
    workflows: dict[str, str]
    # data domains a check on this vocabulary item is evaluated on (used by the applicability gate)
    capability_domains: dict[str, frozenset[str]]
    flag_domains: dict[str, frozenset[str]]
    event_domains: dict[str, frozenset[str]]
    packs: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # (pack_id, version)


def merge_packs(packs: Iterable[OntologyPack]) -> MergedVocabulary:
    """Merge packs into one vocabulary. Different content under the same key is a ``PackConflictError``."""
    packs = list(packs)
    errors: list[str] = []

    def put(target: dict, owners: dict, kind: str, key: str, value: Any, pack: OntologyPack) -> None:
        if key in target:
            if target[key] != value:
                errors.append(f"{kind} {key!r} is defined differently by packs {owners[key]!r} and {pack.pack_id!r}")
            return
        target[key], owners[key] = value, pack.pack_id

    capabilities: dict[str, CapabilitySpec] = {}
    flags: dict[str, str] = {}
    views: dict[str, ViewSpec] = {}
    workflows: dict[str, str] = {}
    event_processes: dict[str, str] = {}
    glossary: dict[str, dict] = {}
    owners: dict[str, dict[str, str]] = {k: {} for k in ("capability", "record flag", "view", "workflow", "event process", "glossary term")}
    events: set[str] = set()
    attributes: dict[str, None] = {}
    cap_domains: dict[str, frozenset[str]] = {}
    flag_domains: dict[str, frozenset[str]] = {}
    event_domains: dict[str, frozenset[str]] = {}

    for pack in packs:
        primary = frozenset(pack.primary_domains)
        for key, spec in pack.capabilities.items():
            put(capabilities, owners["capability"], "capability", key, spec, pack)
            cap_domains[key] = cap_domains.get(key, frozenset()) | primary
        for key, text in pack.record_flags.items():
            put(flags, owners["record flag"], "record flag", key, text, pack)
            flag_domains[key] = flag_domains.get(key, frozenset()) | primary
        for key, spec in pack.views.items():
            put(views, owners["view"], "view", key, spec, pack)
        for key, text in pack.workflows.items():
            put(workflows, owners["workflow"], "workflow", key, text, pack)
        for key, process in pack.event_processes.items():
            put(event_processes, owners["event process"], "event process", key, process, pack)
        for key, attrs in pack.glossary.items():
            put(glossary, owners["glossary term"], "glossary term", key, attrs, pack)
        view_domains = {spec.data_domain for spec in pack.views.values()
                        if spec.data_domain and "event_type" in spec.columns}
        for event in pack.event_types:
            events.add(event)
            event_domains[event] = event_domains.get(event, frozenset()) | primary | frozenset(view_domains)
        attributes.update(dict.fromkeys(pack.product_attributes))
    if errors:
        raise PackConflictError("Ontology pack conflict: " + "; ".join(errors))

    return MergedVocabulary(
        capabilities={k: v.control for k, v in capabilities.items()},
        capability_specs=dict(capabilities),
        record_flags=dict(flags),
        event_types=frozenset(events),
        event_processes=dict(event_processes),
        capability_processes={k: v.process for k, v in capabilities.items() if v.process},
        product_attributes=tuple(attributes),
        views={n: {"description": v.description, "columns": dict(v.columns)} for n, v in views.items()},
        view_domains={n: v.data_domain for n, v in views.items() if v.data_domain},
        glossary={k: dict(v) for k, v in glossary.items()},
        workflows=dict(workflows),
        capability_domains=cap_domains, flag_domains=flag_domains, event_domains=event_domains,
        packs=tuple((p.pack_id, p.version) for p in packs),
    )


# applicability signals -----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class PackSignal:
    pack_id: str
    name: str
    label: str
    pattern: re.Pattern
    business_line: Optional[str] = None
    domains: tuple[str, ...] = ()
    licence: Optional[str] = None
    order: int = 100


@lru_cache(maxsize=8)
def _compile_signals(directory: str, signature: tuple) -> tuple[PackSignal, ...]:
    signals: list[PackSignal] = []
    for pack in _load_dir(directory, signature).values():
        for spec in pack.signals:
            pattern = re.compile("|".join(f"(?:{k})" for k in spec.keywords), re.IGNORECASE)
            signals.append(PackSignal(pack.pack_id, spec.name or pack.pack_id, spec.label or pack.title, pattern,
                                      spec.business_line, tuple(spec.data_domains), spec.licence, spec.order))
    return tuple(sorted(signals, key=lambda s: (s.order, s.pack_id, s.name)))


def applicability_signals(pack_dir: str | Path | None = None) -> tuple[PackSignal, ...]:
    """Signals of *every* pack, enabled or not: a rule on an un-onboarded business must still be recognised and gated."""
    directory = Path(pack_dir or PACK_DIR)
    signature = _signature(directory)
    _load_dir(str(directory), signature)  # validates, populates the cache
    return _compile_signals(str(directory), signature)


def check_against_profile(pack: OntologyPack, profile: dict) -> list[str]:
    """Problems linking a pack to an entity profile (unknown business line or data domain)."""
    known_lines = set(profile.get("business_lines", {}))
    known_domains = set(profile.get("onboarded_data_domains", [])) | set(profile.get("not_onboarded_data_domains", []))
    problems = []
    lines = [(pack.business_line, "business_line")] + [(s.business_line, "applicability_signals.business_line") for s in pack.signals]
    for line, where in lines:
        if line and line not in known_lines:
            problems.append(f"{pack.pack_id}: {where}: {line!r} is not a business line in the entity profile")
    domains = list(pack.requires_data_domains) + [d for s in pack.signals for d in s.data_domains]
    domains += [v.data_domain for v in pack.views.values() if v.data_domain]
    for domain in dict.fromkeys(domains):
        if domain not in known_domains:
            problems.append(f"{pack.pack_id}: data domain {domain!r} is not in the entity profile")
    return problems
