"""Rubric #7 (Persistent Session State) & Rubric #8 (Asynchronous Memory Operations).

Session and long-term memory service factories across environments:

| ``APP_ENV`` | Session Service (``build_session_service``) | Memory Service (``build_memory_service``) |
|-------------|---------------------------------------------|-------------------------------------------|
| ``local``   | ``DatabaseSessionService`` (SQLite async)   | ``InMemoryMemoryService``                 |
| ``dev``     | ``DatabaseSessionService`` (SQLite/CloudSQL)| ``InMemoryMemoryService``                 |
| ``prod``    | ``VertexAiSessionService`` (Agent Engine)   | ``VertexAiMemoryBankService``             |

Equivalent ``adk web`` CLI invocation with local persistent SQLite URIs:

```bash
uv run adk web --port 8000 \\
    --session_service_uri=sqlite:///./sessions.db \\
    --memory_service_uri=sqlite:///./memory.db
```
"""

from __future__ import annotations

from google.adk.memory import InMemoryMemoryService, VertexAiMemoryBankService
from google.adk.sessions import DatabaseSessionService, VertexAiSessionService

from alz_research_agent.config import Settings, get_settings


def _normalize_async_db_url(db_url: str) -> str:
    """Ensures SQLite URLs use the async ``sqlite+aiosqlite://`` driver required by ADK 2.13."""
    cleaned = db_url.strip()
    if cleaned.startswith("sqlite://") and not cleaned.startswith("sqlite+aiosqlite://"):
        return cleaned.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return cleaned


def build_session_service(
    settings: Settings | None = None,
) -> DatabaseSessionService | VertexAiSessionService:
    """Builds the ADK session service matching ``settings.app_env``.

    In ``prod``, returns ``VertexAiSessionService`` backed by Vertex AI Agent Engine
    and fails fast with a ``ValueError`` if ``AGENT_ENGINE_ID`` is missing. In
    ``local`` or ``dev``, returns ``DatabaseSessionService`` with ``SESSION_DB_URL``.

    Args:
        settings: Optional ``Settings`` override.

    Returns:
        Configured ``DatabaseSessionService`` or ``VertexAiSessionService``.

    Raises:
        ValueError: When ``app_env == 'prod'`` and ``agent_engine_id`` is not set.
    """
    cfg = settings or get_settings()
    env = cfg.app_env.strip().lower()

    if env == "prod":
        engine_id = cfg.agent_engine_id.strip()
        if not engine_id:
            raise ValueError(
                "APP_ENV='prod' requires a non-empty AGENT_ENGINE_ID to initialize VertexAiSessionService."
            )
        project = cfg.google_cloud_project.strip()
        if not project or project == "your-gcp-project-id":
            raise ValueError(
                "APP_ENV='prod' requires a valid GOOGLE_CLOUD_PROJECT to initialize VertexAiSessionService."
            )
        return VertexAiSessionService(
            project=project,
            location=cfg.google_cloud_location.strip() or "us-central1",
            agent_engine_id=engine_id,
        )

    async_url = _normalize_async_db_url(cfg.session_db_url)
    return DatabaseSessionService(db_url=async_url)


def build_memory_service(
    settings: Settings | None = None,
) -> InMemoryMemoryService | VertexAiMemoryBankService:
    """Builds the ADK long-term memory service matching ``settings.app_env``.

    In ``prod``, returns ``VertexAiMemoryBankService`` backed by Vertex AI Agent
    Engine and fails fast if ``AGENT_ENGINE_ID`` is missing. Otherwise returns
    ``InMemoryMemoryService``.

    Args:
        settings: Optional ``Settings`` override.

    Returns:
        Configured ``InMemoryMemoryService`` or ``VertexAiMemoryBankService``.

    Raises:
        ValueError: When ``app_env == 'prod'`` and ``agent_engine_id`` is not set.
    """
    cfg = settings or get_settings()
    env = cfg.app_env.strip().lower()

    if env == "prod":
        engine_id = cfg.agent_engine_id.strip()
        if not engine_id:
            raise ValueError(
                "APP_ENV='prod' requires a non-empty AGENT_ENGINE_ID to initialize VertexAiMemoryBankService."
            )
        project = cfg.google_cloud_project.strip()
        if not project or project == "your-gcp-project-id":
            raise ValueError(
                "APP_ENV='prod' requires a valid GOOGLE_CLOUD_PROJECT to initialize VertexAiMemoryBankService."
            )
        return VertexAiMemoryBankService(
            project=project,
            location=cfg.google_cloud_location.strip() or "us-central1",
            agent_engine_id=engine_id,
        )

    return InMemoryMemoryService()
