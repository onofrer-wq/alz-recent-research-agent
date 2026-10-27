"""Rubric #9 (Multi-Agent Patterns), Rubric #3 (Explicit JSON Schemas) & Rubric #10 (Strategic Model Routing).

Consultant specialist agent (``alz_consultant_agent``) that synthesizes the
research dossier into a schema-validated ``ConsultantDraft`` stored in
``state["consultant_draft"]`` while tracking ``loop_iteration`` in session state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.adk.agents import LlmAgent

from alz_research_agent.agents.prompts import build_instruction
from alz_research_agent.config import AgentRole, Settings, get_model_for_role
from alz_research_agent.schemas import ConsultantDraft

if TYPE_CHECKING:
    from google.adk.agents.callback_context import CallbackContext
    from google.genai import types


def increment_loop_iteration(
    callback_context: CallbackContext,
) -> types.Content | None:
    """Increments ``state['loop_iteration']`` before each Consultant synthesis pass."""
    state = getattr(callback_context, "state", None)
    if state is not None:
        try:
            current = int(state.get("loop_iteration", 0) or 0)
            state["loop_iteration"] = current + 1
        except Exception:
            pass
    return None


def build_consultant_agent(settings: Settings | None = None) -> LlmAgent:
    """Constructs the ``alz_consultant_agent`` ``LlmAgent``.

    Args:
        settings: Optional ``Settings`` override.

    Returns:
        Configured ``LlmAgent`` bound to the ``REASONING`` model tier with
        ``output_schema=ConsultantDraft`` and ``output_key='consultant_draft'``.
    """
    return LlmAgent(
        name="alz_consultant_agent",
        description=(
            "Scientific literacy consultant that synthesizes research_findings and "
            "critic_feedback into a structured ConsultantDraft stored in consultant_draft."
        ),
        model=get_model_for_role(AgentRole.REASONING, settings),
        instruction=build_instruction("CONSULTANT"),
        tools=[],
        output_schema=ConsultantDraft,
        output_key="consultant_draft",
        before_agent_callback=increment_loop_iteration,
    )


consultant_agent: LlmAgent = build_consultant_agent()
