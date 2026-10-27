"""Rubric #11 (Guardrails & Policy Plugins).

Guardrail and policy plugin suite assembling Google Cloud Model Armor (when
templates are configured), deterministic medical safety policy enforcement,
3-year recency floor enforcement, and post-agent self-evaluation judging.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from alz_research_agent.guardrails.medical_policy_plugin import MedicalSafetyPolicyPlugin
from alz_research_agent.guardrails.model_armor import build_model_armor_plugin
from alz_research_agent.guardrails.recency_policy_plugin import RecencyPolicyPlugin
from alz_research_agent.guardrails.self_eval_plugin import (
    SelfEvaluationJudgePlugin,
    compute_citation_groundedness,
    parse_and_validate_critic_verdict,
)

if TYPE_CHECKING:
    from google.adk.plugins import BasePlugin


def build_guardrail_plugins(
    *,
    enable_llm_judge: bool = False,
    judge_client: Any = None,
) -> list[BasePlugin]:
    """Constructs the ordered list of guardrail plugins for the ADK ``App`` / ``Runner``.

    Order:
    1. ``ModelArmorPlugin`` (if ``MODEL_ARMOR_PROMPT_TEMPLATE`` or
       ``MODEL_ARMOR_RESPONSE_TEMPLATE`` is configured)
    2. ``MedicalSafetyPolicyPlugin``
    3. ``RecencyPolicyPlugin``
    4. ``SelfEvaluationJudgePlugin``

    Args:
        enable_llm_judge: Whether ``SelfEvaluationJudgePlugin`` also queries ``JUDGE_MODEL``.
        judge_client: Optional injected GenAI client for ``SelfEvaluationJudgePlugin``.

    Returns:
        Ordered list of initialized ``BasePlugin`` instances.
    """
    plugins: list[BasePlugin] = []
    armor = build_model_armor_plugin()
    if armor is not None:
        plugins.append(armor)
    plugins.append(MedicalSafetyPolicyPlugin())
    plugins.append(RecencyPolicyPlugin())
    plugins.append(
        SelfEvaluationJudgePlugin(
            enable_llm_judge=enable_llm_judge,
            judge_client=judge_client,
        )
    )
    return plugins


__all__ = [
    "MedicalSafetyPolicyPlugin",
    "RecencyPolicyPlugin",
    "SelfEvaluationJudgePlugin",
    "build_guardrail_plugins",
    "build_model_armor_plugin",
    "compute_citation_groundedness",
    "parse_and_validate_critic_verdict",
]
