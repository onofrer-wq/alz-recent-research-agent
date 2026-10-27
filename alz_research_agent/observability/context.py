"""Rubric #13 (Structured JSON Logging), Rubric #14 (Intent vs. Outcome Capture) & Rubric #15 (Distributed Tracing).

Execution context management using ``contextvars`` so every structured log record
and OpenTelemetry span carries ``agent_id``, ``session_id``, ``invocation_id``,
and ``user_id`` across asynchronous task boundaries.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

agent_id_var: ContextVar[str | None] = ContextVar("agent_id", default=None)
session_id_var: ContextVar[str | None] = ContextVar("session_id", default=None)
invocation_id_var: ContextVar[str | None] = ContextVar("invocation_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)


def set_execution_context(
    *,
    agent_id: str | None = None,
    session_id: str | None = None,
    invocation_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, str | None]:
    """Sets any provided execution context variables in the current async context.

    Args:
        agent_id: Identifier of the active ADK agent.
        session_id: Identifier of the active ADK conversation session.
        invocation_id: Identifier of the current ADK runner invocation.
        user_id: Identifier of the end user associated with the session.

    Returns:
        Snapshot dictionary of the updated execution context.
    """
    if agent_id is not None:
        agent_id_var.set(agent_id)
    if session_id is not None:
        session_id_var.set(session_id)
    if invocation_id is not None:
        invocation_id_var.set(invocation_id)
    if user_id is not None:
        user_id_var.set(user_id)
    return current_context()


def reset_execution_context() -> None:
    """Resets all execution context variables to ``None``."""
    agent_id_var.set(None)
    session_id_var.set(None)
    invocation_id_var.set(None)
    user_id_var.set(None)


def current_context() -> dict[str, str | None]:
    """Returns a snapshot dictionary of the active execution context.

    Returns:
        Dictionary with keys ``agent_id``, ``session_id``, ``invocation_id``,
        and ``user_id``.
    """
    return {
        "agent_id": agent_id_var.get(),
        "session_id": session_id_var.get(),
        "invocation_id": invocation_id_var.get(),
        "user_id": user_id_var.get(),
    }


@contextmanager
def agent_scope(
    agent_id: str,
    *,
    session_id: str | None = None,
    invocation_id: str | None = None,
    user_id: str | None = None,
) -> Iterator[dict[str, str | None]]:
    """Context manager that binds ``agent_id`` (and optional session IDs) for a block.

    Restores previous ``ContextVar`` values upon exit even if an exception occurs.

    Args:
        agent_id: Identifier of the agent entering scope.
        session_id: Optional session identifier to bind for the scope.
        invocation_id: Optional invocation identifier to bind for the scope.
        user_id: Optional user identifier to bind for the scope.

    Yields:
        Snapshot dictionary of the active context inside the scope.
    """
    tokens: list[tuple[ContextVar[str | None], Token[str | None]]] = [
        (agent_id_var, agent_id_var.set(agent_id)),
    ]
    if session_id is not None:
        tokens.append((session_id_var, session_id_var.set(session_id)))
    if invocation_id is not None:
        tokens.append((invocation_id_var, invocation_id_var.set(invocation_id)))
    if user_id is not None:
        tokens.append((user_id_var, user_id_var.set(user_id)))
    try:
        yield current_context()
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
