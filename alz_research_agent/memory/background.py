"""Rubric #8 (Asynchronous Memory Operations) & Rubric #16 (PII Redaction).

Non-blocking background consolidation of PII-redacted ADK sessions into long-term
memory using ``asyncio.create_task``, module-level strong task references,
completion logging callbacks, and ``MemoryConsolidationPlugin``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import TYPE_CHECKING, Any

from google.adk.plugins import BasePlugin

from alz_research_agent.privacy.pii_redactor import redact_session_copy

if TYPE_CHECKING:
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.sessions.session import Session

logger = logging.getLogger("alz_research_agent.audit")

_BACKGROUND_MEMORY_TASKS: set[asyncio.Task[Any]] = set()


async def _consolidate_redacted_session(
    redacted_session: Session,
    memory_service: Any,
) -> None:
    """Invokes ``memory_service.add_session_to_memory`` on a PII-redacted session copy."""
    add_fn = getattr(memory_service, "add_session_to_memory", None)
    if add_fn is None:
        return
    result = add_fn(redacted_session)
    if inspect.isawaitable(result):
        await result


def _on_consolidation_done(
    task: asyncio.Task[Any],
    session_id: str | None,
) -> None:
    """Done-callback that releases the strong task reference and logs outcome."""
    _BACKGROUND_MEMORY_TASKS.discard(task)
    if task.cancelled():
        logger.warning(
            "Background memory consolidation cancelled for session_id=%s",
            session_id,
            extra={
                "event": "MEMORY_CONSOLIDATION_CANCELLED",
                "session_id": session_id,
            },
        )
        return

    exc = task.exception()
    if exc is not None:
        logger.error(
            "Background memory consolidation failed for session_id=%s: %s",
            session_id,
            type(exc).__name__,
            extra={
                "event": "MEMORY_CONSOLIDATION_FAILED",
                "session_id": session_id,
                "error_type": type(exc).__name__,
            },
        )
        return

    logger.info(
        "Background memory consolidation completed for session_id=%s",
        session_id,
        extra={
            "event": "MEMORY_CONSOLIDATION_COMPLETED",
            "session_id": session_id,
        },
    )


def schedule_memory_consolidation(
    session: Session | None,
    memory_service: Any,
) -> asyncio.Task[Any] | None:
    """Redacts ``session`` via ``redact_session_copy`` and schedules ``add_session_to_memory``.

    Stores a strong reference in ``_BACKGROUND_MEMORY_TASKS`` until completion so
    the task is never garbage-collected mid-execution.

    Args:
        session: Active ADK ``Session`` to sanitize and consolidate.
        memory_service: ADK memory service exposing ``add_session_to_memory``.

    Returns:
        The scheduled ``asyncio.Task``, or ``None`` if ``session`` or ``memory_service``
        is unavailable.
    """
    if session is None or memory_service is None:
        return None

    redacted_copy = redact_session_copy(session)
    session_id = getattr(redacted_copy, "id", None)

    task: asyncio.Task[Any] = asyncio.create_task(
        _consolidate_redacted_session(redacted_copy, memory_service),
        name=f"memory_consolidate_{session_id or 'unknown'}",
    )
    _BACKGROUND_MEMORY_TASKS.add(task)
    task.add_done_callback(lambda t: _on_consolidation_done(t, session_id))
    return task


async def drain_background_memory_tasks(max_wait_seconds: float = 5.0) -> int:
    """Awaits all currently scheduled background memory consolidation tasks (test helper).

    Args:
        max_wait_seconds: Maximum seconds to wait for pending tasks.

    Returns:
        Count of tasks that were awaited.
    """
    pending = list(_BACKGROUND_MEMORY_TASKS)
    if not pending:
        return 0
    async with asyncio.timeout(max_wait_seconds):
        await asyncio.gather(*pending, return_exceptions=True)
    return len(pending)


class MemoryConsolidationPlugin(BasePlugin):
    """ADK ``BasePlugin`` that schedules asynchronous memory consolidation in ``after_run_callback``."""

    def __init__(
        self,
        *,
        name: str = "memory_consolidation_plugin",
        memory_service: Any = None,
    ) -> None:
        """Initializes the background memory consolidation plugin.

        Args:
            name: Unique plugin identifier.
            memory_service: Optional fallback memory service when ``invocation_context``
                does not carry one.
        """
        super().__init__(name=name)
        self._memory_service = memory_service

    async def after_run_callback(
        self,
        *,
        invocation_context: InvocationContext,
    ) -> None:
        """Schedules fire-and-forget PII-safe memory consolidation at the end of a run."""
        session = getattr(invocation_context, "session", None)
        mem_service = getattr(invocation_context, "memory_service", None) or self._memory_service
        schedule_memory_consolidation(session, mem_service)
        return None
