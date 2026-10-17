"""Extraction cache keyed by document hash, extractor version and model id (L8).

The same PDF must give the same rules: an LLM extraction is stored once and
reused, so rule ids and checks stay stable across runs.

local: ``<runtime_dir>/extraction-cache/<sha256>.json``
gcp:   ``gs://<CIRCULARS_BUCKET>/extraction-cache/<sha256>.json`` (the bucket the API already archives uploads to;
       the Eventarc watcher ignores this prefix because it only processes ``incoming/``).

Each object holds one entry; an entry written by another extractor version or model is a miss and is
replaced. Cache errors never fail a run: they are logged and treated as a miss.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from core.config import Settings, get_settings

log = logging.getLogger(__name__)

EXTRACTOR_VERSION = "2026-10-17.2"  # bump when the prompt, schema, validator or templates change (.2: unexecutable obligations, capability_description)
PREFIX = "extraction-cache/"
SHA_RX = re.compile(r"^[0-9a-f]{64}$")


def cache_key(sha256: str, model: str, version: str = EXTRACTOR_VERSION) -> str:
    return f"{sha256}:{version}:{model}"


class ExtractionCache:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.use_gcs = self.settings.document_backend == "gcp" and bool(self.settings.circulars_bucket)

    @property
    def location(self) -> str:
        if self.use_gcs:
            return f"gs://{self.settings.circulars_bucket}/{PREFIX}"
        return str(self.settings.runtime_dir / PREFIX)

    def get(self, sha256: str, model: str, version: str = EXTRACTOR_VERSION) -> dict | None:
        if not SHA_RX.match(sha256 or ""):
            return None
        try:
            raw = self._read(sha256)
        except Exception as error:
            log.warning("Extraction cache read failed (%s); extracting again.", type(error).__name__)
            return None
        if raw is None:
            return None
        try:
            entry = json.loads(raw)
        except ValueError:
            log.warning("Extraction cache entry for %s is corrupt; extracting again.", sha256[:12])
            return None
        if entry.get("key") != cache_key(sha256, model, version):
            return None
        return entry

    def put(self, sha256: str, model: str, payload: dict, version: str = EXTRACTOR_VERSION) -> bool:
        if not SHA_RX.match(sha256 or ""):
            return False
        entry = {"key": cache_key(sha256, model, version), "sha256": sha256, "extractor_version": version, "model": model,
                 "created_at": datetime.now(timezone.utc).isoformat(), **payload}
        try:
            self._write(sha256, json.dumps(entry, default=str))
            return True
        except Exception as error:
            log.warning("Extraction cache write failed (%s).", type(error).__name__)
            return False

    # backends ---------------------------------------------------------------
    def _path(self, sha256: str) -> Path:
        return self.settings.runtime_dir / PREFIX / f"{sha256}.json"

    def _blob(self, sha256: str):
        from google.cloud import storage

        bucket = storage.Client(project=self.settings.project).bucket(self.settings.circulars_bucket)
        return bucket.blob(f"{PREFIX}{sha256}.json")

    def _read(self, sha256: str) -> str | None:
        if self.use_gcs:
            from google.api_core.exceptions import NotFound

            try:
                return self._blob(sha256).download_as_text()
            except NotFound:
                return None
        path = self._path(sha256)
        return path.read_text() if path.exists() else None

    def _write(self, sha256: str, body: str) -> None:
        if self.use_gcs:
            self._blob(sha256).upload_from_string(body, content_type="application/json")
            return
        path = self._path(sha256)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(body)
        tmp.replace(path)
