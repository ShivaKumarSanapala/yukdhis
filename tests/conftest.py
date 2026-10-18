"""Shared test isolation: one runtime directory for the whole process, and fresh app/graph state per test.

Several test modules set YUKDHIS_RUNTIME_DIR at import, but core.config reads it once, so whichever module imports
first decides the directory for everyone. Without this, tests share (and pollute) one app/graph database and can
pass or fail depending on file order. The operational DB is deterministic from data/bank, so it is kept.
"""

import os
import tempfile
from pathlib import Path

RUNTIME = Path(tempfile.mkdtemp(prefix="yukdhis-pytest-"))
os.environ.update(YUKDHIS_RUNTIME_DIR=str(RUNTIME), YUKDHIS_BACKEND="local", AGENT_MODE="deterministic", AUTH_MODE="none")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import pytest  # noqa: E402

from bank_mcp_server import operational_store  # noqa: E402
from core import app_store  # noqa: E402
from knowledge_graph import store as graph_store  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_state():
    for holder in (app_store, graph_store):
        if holder._STORE is not None and getattr(holder._STORE, "engine", None) is not None:
            holder._STORE.engine.dispose()
        holder._STORE = None
    operational_store.reset_engines()
    for name in ("app.db", "graph.db"):
        (RUNTIME / name).unlink(missing_ok=True)
    yield
