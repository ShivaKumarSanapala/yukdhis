"""ADK runtime wiring: Runners, DatabaseSessionService and MemoryService.

local: sessions in SQLite (aiosqlite), InMemoryMemoryService.
gcp:   sessions in Cloud SQL for PostgreSQL (asyncpg via the Cloud SQL
       connector, IAM auth); long-term memory in Vertex AI Memory Bank.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from google.adk.memory import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService

from core.config import get_settings

log = logging.getLogger(__name__)
PIPELINE_APP = "yukdhis_pipeline"
ASSISTANT_APP = "yukdhis_assistant"


@lru_cache(maxsize=1)
def session_service() -> DatabaseSessionService:
    settings = get_settings()
    if settings.memory_backend == "gcp" and settings.cloudsql_instance:
        settings.require("cloudsql_iam_user")
        from google.cloud.sql.connector import Connector, IPTypes
        from sqlalchemy.ext.asyncio import create_async_engine

        connector: Connector | None = None

        async def getconn():
            nonlocal connector
            if connector is None:
                import asyncio

                connector = Connector(ip_type=IPTypes.PRIVATE, refresh_strategy="lazy", loop=asyncio.get_running_loop())
            return await connector.connect_async(settings.cloudsql_instance, "asyncpg", user=settings.cloudsql_iam_user,
                                                 db=settings.cloudsql_app_db, enable_iam_auth=True)

        engine = create_async_engine("postgresql+asyncpg://", async_creator=getconn, pool_pre_ping=True)
        return DatabaseSessionService(db_engine=engine)
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    return DatabaseSessionService(db_url=f"sqlite+aiosqlite:///{settings.runtime_dir / 'sessions.db'}")


@lru_cache(maxsize=1)
def memory_service():
    settings = get_settings()
    if settings.memory_backend == "gcp" and settings.memory_bank_engine_id:
        from google.adk.memory import VertexAiMemoryBankService

        return VertexAiMemoryBankService(project=settings.project, location=settings.location,
                                         agent_engine_id=settings.memory_bank_engine_id)
    return InMemoryMemoryService()


@lru_cache(maxsize=1)
def pipeline_runner() -> Runner:
    from agents.compliance_coordinator.agent import root_agent

    return Runner(app_name=PIPELINE_APP, agent=root_agent, session_service=session_service(), memory_service=memory_service())


@lru_cache(maxsize=1)
def assistant_runner() -> Runner:
    from agents.compliance_assistant.agent import root_agent

    return Runner(app_name=ASSISTANT_APP, agent=root_agent, session_service=session_service(), memory_service=memory_service())
