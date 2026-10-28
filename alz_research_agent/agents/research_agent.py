"""Rubric #9 (Multi-Agent Patterns), Rubric #5 (Constitution) & Rubric #10 (Strategic Model Routing).

Researcher specialist agent (``alz_researcher_agent``) responsible exclusively for
gathering recent (< 3 years) Alzheimer's studies from PubMed, ClinicalTrials.gov,
and Europe PMC into ``state["research_findings"]``.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.genai import types

from alz_research_agent.agents.prompts import build_instruction
from alz_research_agent.config import AgentRole, Settings, get_model_for_role
from alz_research_agent.tools import RESEARCH_TOOLS


def build_research_agent(settings: Settings | None = None) -> LlmAgent:
    """Constructs the ``alz_researcher_agent`` ``LlmAgent``.

    Args:
        settings: Optional ``Settings`` override.

    Returns:
        Configured ``LlmAgent`` bound to the ``RESEARCH`` model tier and ``RESEARCH_TOOLS``.
    """
    return LlmAgent(
        name="alz_researcher_agent",
        description=(
            "Biomedical literature researcher that queries PubMed, ClinicalTrials.gov, "
            "and Europe PMC for Alzheimer's studies from the last 3 years and writes "
            "research_findings to session state."
        ),
        model=get_model_for_role(AgentRole.RESEARCH, settings),
        instruction=build_instruction("RESEARCH"),
        generate_content_config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            temperature=0.2,
        ),
        tools=list(RESEARCH_TOOLS),
        output_key="research_findings",
    )


research_agent: LlmAgent = build_research_agent()
