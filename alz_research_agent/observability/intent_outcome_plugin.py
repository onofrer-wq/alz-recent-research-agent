"""Rubric #14 (Intent vs. Outcome Capture), Rubric #13 (Structured JSON Logging) & Rubric #16 (PII Redaction).

ADK ``BasePlugin`` implementation that captures structured ``AGENT_INTENT`` and
``AGENT_OUTCOME`` audit events across agent lifecycles, tool executions, and LLM
invocations using the ``alz_research_agent.audit`` logger.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from google.adk.plugins import BasePlugin

from alz_research_agent.observability.context import set_execution_context
from alz_research_agent.privacy.pii_redactor import redact_value

if TYPE_CHECKING:
    from google.adk.agents.base_agent import BaseAgent
    from google.adk.agents.callback_context import CallbackContext
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.models.llm_request import LlmRequest
    from google.adk.models.llm_response import LlmResponse
    from google.adk.tools.base_tool import BaseTool
    from google.adk.tools.tool_context import ToolContext
    from google.genai import types

AUDIT_LOGGER_NAME = "alz_research_agent.audit"
_MAX_ARG_STR_LEN = 200


def _truncate_and_redact(value: Any, max_len: int = _MAX_ARG_STR_LEN) -> Any:
    """Redacts PII and truncates long strings inside tool arguments."""
    redacted = redact_value(value)
    return _truncate_sanitized(redacted, max_len=max_len)


def _truncate_sanitized(value: Any, max_len: int = _MAX_ARG_STR_LEN) -> Any:
    """Recursively truncates string leaves that exceed ``max_len`` characters."""
    if isinstance(value, str):
        return value if len(value) <= max_len else f"{value[:max_len]}..."
    if isinstance(value, dict):
        return {str(k): _truncate_sanitized(v, max_len=max_len) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate_sanitized(item, max_len=max_len) for item in value]
    if isinstance(value, tuple):
        return tuple(_truncate_sanitized(item, max_len=max_len) for item in value)
    return value


def _extract_state_dict(context: Any) -> dict[str, Any]:
    """Safely extracts a dictionary snapshot of ``context.state`` if available."""
    state_obj = getattr(context, "state", None)
    if state_obj is None:
        return {}
    if isinstance(state_obj, dict):
        return dict(state_obj)
    to_dict = getattr(state_obj, "to_dict", None)
    if callable(to_dict):
        try:
            return dict(to_dict())
        except Exception:
            return {}
    try:
        return dict(state_obj)
    except Exception:
        return {}


def _infer_result_metrics(result: Any) -> tuple[str, str | None, int, bool]:
    """Infers ``(status, error_type, result_count, intent_matched_outcome)`` from a tool result."""
    if isinstance(result, dict):
        raw_status = str(result.get("status", "ok")).strip().lower()
        error_type = result.get("error_type")
        if error_type is not None:
            error_type = str(error_type)
        elif raw_status in ("error", "failed", "exception"):
            error_type = str(result.get("error") or "ToolResultError")

        count: int | None = None
        if isinstance(result.get("count"), int):
            count = int(result["count"])
        else:
            for list_key in ("results", "papers", "studies", "items", "articles", "records"):
                candidate = result.get(list_key)
                if isinstance(candidate, list):
                    count = len(candidate)
                    break
        if count is None:
            count = len(result)

        matched = raw_status in ("ok", "success", "completed") and error_type is None
        return raw_status, error_type, count, matched

    if isinstance(result, list):
        return "ok", None, len(result), True

    return "ok", None, (1 if result is not None else 0), True


class IntentOutcomePlugin(BasePlugin):
    """ADK ``BasePlugin`` logging paired ``AGENT_INTENT`` and ``AGENT_OUTCOME`` audit events."""

    def __init__(self, name: str = "intent_outcome_plugin") -> None:
        """Initializes the intent-versus-outcome audit plugin.

        Args:
            name: Unique plugin identifier registered with the ADK ``PluginManager``.
        """
        super().__init__(name=name)
        self.logger = logging.getLogger(AUDIT_LOGGER_NAME)
        self._agent_timers: dict[str, float] = {}
        self._agent_initial_keys: dict[str, set[str]] = {}
        self._tool_timers: dict[str, float] = {}
        self._model_timers: dict[str, float] = {}

    async def before_run_callback(
        self,
        *,
        invocation_context: InvocationContext,
    ) -> types.Content | None:
        """Binds ``session_id``, ``invocation_id``, ``user_id``, and root ``agent_id``."""
        session = getattr(invocation_context, "session", None)
        session_id = getattr(session, "id", None)
        user_id = getattr(session, "user_id", None) or getattr(invocation_context, "user_id", None)
        invocation_id = getattr(invocation_context, "invocation_id", None)
        agent_name = getattr(getattr(invocation_context, "agent", None), "name", None)
        set_execution_context(
            agent_id=agent_name,
            session_id=session_id,
            invocation_id=invocation_id,
            user_id=user_id,
        )
        return None

    async def before_agent_callback(
        self,
        *,
        agent: BaseAgent,
        callback_context: CallbackContext,
    ) -> types.Content | None:
        """Sets ``agent_id`` context and logs ``AGENT_INTENT`` prior to agent execution."""
        agent_name = getattr(agent, "name", None) or getattr(callback_context, "agent_name", None) or "unknown"
        session = getattr(callback_context, "session", None)
        set_execution_context(
            agent_id=agent_name,
            session_id=getattr(session, "id", None),
            invocation_id=getattr(callback_context, "invocation_id", None),
            user_id=getattr(callback_context, "user_id", None) or getattr(session, "user_id", None),
        )
        self._agent_timers[agent_name] = time.perf_counter()
        self._agent_initial_keys[agent_name] = set(_extract_state_dict(callback_context).keys())

        self.logger.info(
            "Agent %s starting execution",
            agent_name,
            extra={
                "event": "AGENT_INTENT",
                "phase": "agent",
                "agent_name": agent_name,
            },
        )
        return None

    async def after_agent_callback(
        self,
        *,
        agent: BaseAgent,
        callback_context: CallbackContext,
    ) -> types.Content | None:
        """Logs ``AGENT_OUTCOME`` with latency and written session state keys."""
        agent_name = getattr(agent, "name", None) or getattr(callback_context, "agent_name", None) or "unknown"
        start_time = self._agent_timers.pop(agent_name, None)
        latency_ms = round((time.perf_counter() - start_time) * 1000.0, 2) if start_time is not None else 0.0

        current_state = _extract_state_dict(callback_context)
        initial_keys = self._agent_initial_keys.pop(agent_name, set())
        written_keys = sorted(set(current_state.keys()) - initial_keys)
        if not written_keys and current_state:
            written_keys = sorted(current_state.keys())

        self.logger.info(
            "Agent %s completed execution",
            agent_name,
            extra={
                "event": "AGENT_OUTCOME",
                "phase": "agent",
                "agent_name": agent_name,
                "status": "ok",
                "latency_ms": latency_ms,
                "state_keys_written": written_keys,
                "intent_matched_outcome": True,
            },
        )
        return None

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict[str, Any] | None:
        """Logs ``AGENT_INTENT`` with tool name and PII-redacted, truncated ``tool_args``."""
        tool_name = getattr(tool, "name", None) or type(tool).__name__
        agent_name = getattr(tool_context, "agent_name", None)
        if agent_name:
            set_execution_context(agent_id=agent_name)

        call_key = f"{tool_name}:{getattr(tool_context, 'function_call_id', None) or 'default'}"
        self._tool_timers[call_key] = time.perf_counter()

        sanitized_args = _truncate_and_redact(tool_args or {})
        self.logger.info(
            "Invoking tool %s",
            tool_name,
            extra={
                "event": "AGENT_INTENT",
                "phase": "tool",
                "tool_name": tool_name,
                "tool_args": sanitized_args,
            },
        )
        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Logs ``AGENT_OUTCOME`` with status, error_type, result_count, latency, and match flag."""
        del tool_args
        tool_name = getattr(tool, "name", None) or type(tool).__name__
        call_key = f"{tool_name}:{getattr(tool_context, 'function_call_id', None) or 'default'}"
        start_time = self._tool_timers.pop(call_key, None)
        latency_ms = round((time.perf_counter() - start_time) * 1000.0, 2) if start_time is not None else 0.0

        status, error_type, result_count, matched = _infer_result_metrics(result)
        self.logger.info(
            "Tool %s completed with status=%s",
            tool_name,
            status,
            extra={
                "event": "AGENT_OUTCOME",
                "phase": "tool",
                "tool_name": tool_name,
                "status": status,
                "error_type": error_type,
                "result_count": result_count,
                "latency_ms": latency_ms,
                "intent_matched_outcome": matched,
            },
        )
        return None

    async def on_tool_error_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        error: Exception,
    ) -> dict[str, Any] | None:
        """Logs ``AGENT_OUTCOME`` with ``status='exception'`` when a tool raises an error."""
        del tool_args
        tool_name = getattr(tool, "name", None) or type(tool).__name__
        call_key = f"{tool_name}:{getattr(tool_context, 'function_call_id', None) or 'default'}"
        start_time = self._tool_timers.pop(call_key, None)
        latency_ms = round((time.perf_counter() - start_time) * 1000.0, 2) if start_time is not None else 0.0

        self.logger.error(
            "Tool %s raised %s",
            tool_name,
            type(error).__name__,
            extra={
                "event": "AGENT_OUTCOME",
                "phase": "tool",
                "tool_name": tool_name,
                "status": "exception",
                "error_type": type(error).__name__,
                "result_count": 0,
                "latency_ms": latency_ms,
                "intent_matched_outcome": False,
            },
        )
        return None

    async def before_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> LlmResponse | None:
        """Logs model invocation intent with model name, content count, and offered tools."""
        agent_name = getattr(callback_context, "agent_name", None)
        if agent_name:
            set_execution_context(agent_id=agent_name)

        timer_key = agent_name or "default"
        self._model_timers[timer_key] = time.perf_counter()

        model_name = getattr(llm_request, "model", None) or "unknown"
        contents = getattr(llm_request, "contents", None) or []
        tools_dict = getattr(llm_request, "tools_dict", None) or {}
        tools_offered = sorted(str(k) for k in tools_dict) if isinstance(tools_dict, dict) else []

        self.logger.info(
            "Dispatching LLM request to %s",
            model_name,
            extra={
                "event": "AGENT_INTENT",
                "phase": "model",
                "model": str(model_name),
                "content_count": len(contents),
                "tools_offered": tools_offered,
            },
        )
        return None

    async def after_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        """Logs model outcome with function calls, token usage, and latency (skipping partial chunks)."""
        if getattr(llm_response, "partial", False):
            return None

        agent_name = getattr(callback_context, "agent_name", None) or "default"
        start_time = self._model_timers.pop(agent_name, None)
        latency_ms = round((time.perf_counter() - start_time) * 1000.0, 2) if start_time is not None else 0.0

        function_calls: list[str] = []
        content = getattr(llm_response, "content", None)
        for part in getattr(content, "parts", None) or []:
            fc = getattr(part, "function_call", None)
            if fc is not None and getattr(fc, "name", None):
                function_calls.append(str(fc.name))

        usage = getattr(llm_response, "usage_metadata", None)
        tokens: dict[str, int] = {
            "prompt_tokens": int(getattr(usage, "prompt_token_count", 0) or 0) if usage else 0,
            "candidate_tokens": int(getattr(usage, "candidates_token_count", 0) or 0) if usage else 0,
            "total_tokens": int(getattr(usage, "total_token_count", 0) or 0) if usage else 0,
        }
        error_code = getattr(llm_response, "error_code", None)
        status = "error" if error_code else "ok"

        self.logger.info(
            "Received LLM response (%s)",
            status,
            extra={
                "event": "AGENT_OUTCOME",
                "phase": "model",
                "status": status,
                "error_type": str(error_code) if error_code else None,
                "function_calls": function_calls,
                "tokens": tokens,
                "latency_ms": latency_ms,
                "intent_matched_outcome": error_code is None,
            },
        )
        return None
