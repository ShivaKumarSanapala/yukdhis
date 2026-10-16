"""Watcher agent: turns new circulars in Cloud Storage into pipeline runs.

Triggered by Eventarc (``google.cloud.storage.object.v1.finalized`` on the
circulars bucket) and, as a safety net, by Cloud Scheduler polling the
``incoming/`` prefix.  Each object is processed exactly once.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from core.config import get_settings

INCOMING_PREFIX = "incoming/"
ALLOWED_SUFFIXES = {".pdf", ".md", ".txt", ".html", ".docx"}


def eligible(name: str) -> bool:
    return name.startswith(INCOMING_PREFIX) and PurePosixPath(name).suffix.lower() in ALLOWED_SUFFIXES


def source_for(bucket: str, name: str) -> dict:
    return {"gcs_uri": f"gs://{bucket}/{name}", "filename": PurePosixPath(name).name, "trigger": "watcher"}


def list_incoming(limit: int = 20) -> list[dict]:
    settings = get_settings()
    settings.require("circulars_bucket")
    from google.cloud import storage

    blobs = storage.Client(project=settings.project).list_blobs(settings.circulars_bucket, prefix=INCOMING_PREFIX,
                                                                max_results=200)
    return [source_for(settings.circulars_bucket, b.name) for b in blobs if eligible(b.name)][:limit]
