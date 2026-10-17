"""Runtime configuration.

Every component has a ``local`` backend (offline, deterministic, used by tests
and the demo) and a ``gcp`` backend (Spanner Graph, Cloud SQL, Document AI,
Cloud DLP, Gemma on Vertex AI / GKE, Pub/Sub, Vertex AI Memory Bank).
``YUKDHIS_BACKEND`` sets the default; each ``*_BACKEND`` variable overrides it.
Secrets are never defaulted: GCP backends fail fast when required settings are
missing.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RUNTIME_DIR = Path(os.getenv("YUKDHIS_RUNTIME_DIR", str(ROOT / ".data")))


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def split_list(value: str | None) -> tuple[str, ...]:
    """Split a ``;``, ``,`` or whitespace separated env value (``;`` survives gcloud's comma-separated env lists)."""
    return tuple(item for item in re.split(r"[;,\s]+", value or "") if item)


def _backend(name: str, default: str) -> str:
    value = (_env(name) or default).lower()
    if value not in {"local", "gcp"}:
        raise ValueError(f"{name} must be 'local' or 'gcp', got {value!r}")
    return value


@dataclass(frozen=True)
class Settings:
    tenant_id: str
    project: str | None
    location: str
    reasoning_model: str
    fast_model: str
    embedding_model: str
    agent_mode: str  # agentic | deterministic

    graph_backend: str
    operational_backend: str
    document_backend: str
    privacy_backend: str
    gemma_backend: str
    dispatch_backend: str
    memory_backend: str

    spanner_instance: str | None
    spanner_database: str | None
    cloudsql_instance: str | None  # project:region:instance
    cloudsql_operational_db: str
    cloudsql_app_db: str
    cloudsql_iam_user: str | None
    app_db_grantees: tuple[str, ...]  # other app DB users that must share tables this identity creates
    documentai_processor: str | None  # projects/.../locations/.../processors/...
    dlp_kms_key: str | None
    dlp_wrapped_key_b64: str | None
    gemma_vertex_endpoint: str | None
    gemma_gke_url: str | None
    pubsub_topic: str | None
    circulars_bucket: str | None
    memory_bank_engine_id: str | None

    auth_mode: str  # none | firebase
    eventarc_sa_email: str | None
    cors_origins: tuple[str, ...]
    max_upload_bytes: int
    runtime_dir: Path = field(default=RUNTIME_DIR)

    @property
    def is_gcp_llm_ready(self) -> bool:
        return bool(self.project) or bool(_env("GOOGLE_API_KEY"))

    def require(self, *names: str) -> None:
        missing = [name for name in names if not getattr(self, name)]
        if missing:
            raise RuntimeError("Missing required configuration: " + ", ".join(n.upper() for n in missing))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    default = _backend("YUKDHIS_BACKEND", "local")
    origins = _env("CORS_ORIGINS", "http://localhost:5173") or ""
    return Settings(
        tenant_id=_env("TENANT_ID", "demo-bank") or "demo-bank",
        project=_env("GOOGLE_CLOUD_PROJECT"),
        location=_env("GOOGLE_CLOUD_LOCATION", "us-central1") or "us-central1",
        reasoning_model=_env("REASONING_MODEL", "gemini-2.5-pro") or "gemini-2.5-pro",
        fast_model=_env("FAST_MODEL", "gemini-2.5-flash") or "gemini-2.5-flash",
        embedding_model=_env("EMBEDDING_MODEL", "text-embedding-005") or "text-embedding-005",
        agent_mode=(_env("AGENT_MODE", "agentic") or "agentic").lower(),
        graph_backend=_backend("GRAPH_BACKEND", default),
        operational_backend=_backend("OPERATIONAL_BACKEND", default),
        document_backend=_backend("DOCUMENT_BACKEND", default),
        privacy_backend=_backend("PRIVACY_BACKEND", default),
        gemma_backend=_backend("GEMMA_BACKEND", default),
        dispatch_backend=_backend("DISPATCH_BACKEND", default),
        memory_backend=_backend("MEMORY_BACKEND", default),
        spanner_instance=_env("SPANNER_INSTANCE"),
        spanner_database=_env("SPANNER_DATABASE"),
        cloudsql_instance=_env("CLOUDSQL_INSTANCE"),
        cloudsql_operational_db=_env("CLOUDSQL_OPERATIONAL_DB", "bank_ops") or "bank_ops",
        cloudsql_app_db=_env("CLOUDSQL_APP_DB", "yukdhis_app") or "yukdhis_app",
        cloudsql_iam_user=_env("CLOUDSQL_IAM_USER"),
        app_db_grantees=split_list(_env("APP_DB_GRANTEES")),
        documentai_processor=_env("DOCUMENTAI_PROCESSOR"),
        dlp_kms_key=_env("DLP_KMS_KEY"),
        dlp_wrapped_key_b64=_env("DLP_WRAPPED_KEY"),
        gemma_vertex_endpoint=_env("GEMMA_VERTEX_ENDPOINT"),
        gemma_gke_url=_env("GEMMA_GKE_URL"),
        pubsub_topic=_env("PUBSUB_TOPIC"),
        circulars_bucket=_env("CIRCULARS_BUCKET"),
        memory_bank_engine_id=_env("MEMORY_BANK_AGENT_ENGINE_ID"),
        auth_mode=(_env("AUTH_MODE", "none") or "none").lower(),
        eventarc_sa_email=_env("EVENTARC_SA_EMAIL"),
        cors_origins=tuple(origin.strip() for origin in origins.split(",") if origin.strip()),
        max_upload_bytes=int(_env("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)) or 10485760),
    )


def reset_settings_cache() -> None:
    get_settings.cache_clear()
