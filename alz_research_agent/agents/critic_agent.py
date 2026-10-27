"""Rubric #9 (Multi-Agent Patterns), Rubric #5 (Constitution) & Rubric #10 (Strategic Model Routing).

Critic specialist agent (``alz_evidence_critic_agent``) that grades every claim
in ``consultant_draft`` using ``grade_evidence_claim``, enforces speculative
extrapolation labeling and cutting-edge focus, and calls ``exit_critique_loop``
when the draft is ready.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent

from alz_research_agent.agents.prompts import build_instruction
from alz_research_agent.config import AgentRole, Settings, get_model_for_role
from alz_research_agent.tools import CRITIC_TOOLS


def build_critic_agent(settings: Settings | None = None) -> LlmAgent:
    """Constructs the ``alz_evidence_critic_agent`` ``LlmAgent``.

    Note: ``output_schema`` is intentionally omitted because ADK agents equipped
    with function-calling tools (``CRITIC_TOOLS``) cannot simultaneously bind a
    constrained decoding ``output_schema``; ``SelfEvaluationJudgePlugin`` validates
    the Critic's verdict against ``CriticVerdict`` in ``after_agent_callback``.

    Args:
        settings: Optional ``Settings`` override.

    Returns:
        Configured ``LlmAgent`` bound to the ``REASONING`` model tier and ``CRITIC_TOOLS``.
    """
    return LlmAgent(
        name="alz_evidence_critic_agent",
        description=(
            "Methodological evidence critic that scores claims with grade_evidence_claim, "
            "enforces speculative extrapolation labels, and calls exit_critique_loop."
        ),
        model=get_model_for_role(AgentRole.REASONING, settings),
        instruction=build_instruction("CRITIC"),
        tools=list(CRITIC_TOOLS),
        output_key="critic_feedback",
    )


critic_agent: LlmAgent = build_critic_agent()
