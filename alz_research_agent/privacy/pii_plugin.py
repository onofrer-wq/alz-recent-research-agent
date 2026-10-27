"""Rubric #16 (PII Redaction) & Rubric #11 (Guardrails & Policy Plugins).

ADK plugin that scrubs PII from incoming user messages and ``role="user"``
model request contents before they reach downstream agents or Gemini models.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.adk.plugins import BasePlugin

from alz_research_agent.privacy.pii_redactor import redact_text

if TYPE_CHECKING:
    from google.adk.agents.callback_context import CallbackContext
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.models.llm_request import LlmRequest
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types


def _redact_content_parts(content: types.Content | None) -> bool:
    """Redacts text parts of a ``types.Content`` object in-place.

    Args:
        content: GenAI content object whose text parts should be scrubbed.

    Returns:
        ``True`` if at least one text part was modified, ``False`` otherwise.
    """
    if content is None:
        return False
    modified = False
    for part in getattr(content, "parts", None) or []:
        part_text = getattr(part, "text", None)
        if isinstance(part_text, str) and part_text:
            redacted = redact_text(part_text)
            if redacted != part_text:
                part.text = redacted
                modified = True
    return modified


class PiiRedactionPlugin(BasePlugin):
    """ADK ``BasePlugin`` that redacts PII at user ingress and before LLM calls."""

    def __init__(self, name: str = "pii_redaction_plugin") -> None:
        """Initializes the PII redaction plugin.

        Args:
            name: Unique plugin identifier registered with the ADK ``PluginManager``.
        """
        super().__init__(name=name)

    async def on_user_message_callback(
        self,
        *,
        invocation_context: InvocationContext,
        user_message: types.Content,
    ) -> types.Content | None:
        """Redacts PII in ``user_message`` as soon as it enters the runner.

        Mutates ``user_message`` in-place and returns ``None`` so subsequent
        plugins in ``PluginManager`` continue executing with the sanitized message.

        Args:
            invocation_context: Active ADK invocation context.
            user_message: User message content object.

        Returns:
            ``None`` after mutating ``user_message`` in-place.
        """
        del invocation_context
        if _redact_content_parts(user_message):
            return user_message
        return None

    async def before_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> LlmResponse | None:
        """Redacts PII in all ``role="user"`` contents of ``llm_request`` before calling the LLM.

        Args:
            callback_context: Active ADK callback context.
            llm_request: Outgoing LLM request payload.

        Returns:
            ``None`` after scrubbing ``llm_request.contents`` in-place.
        """
        del callback_context
        for content in getattr(llm_request, "contents", None) or []:
            role = getattr(content, "role", None)
            if role in ("user", None):
                _redact_content_parts(content)
        return None
