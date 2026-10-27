"""Rubric #9 (Multi-Agent Patterns).

Composite workflow agents assembling the iterative ``consult_critique_loop``
(``LoopAgent`` with ``max_iterations=3``) and the end-to-end ``research_pipeline``
(``SequentialAgent`` chaining ``research_agent`` -> ``consult_critique_loop``).
"""

from __future__ import annotations

from google.adk.agents import LoopAgent, SequentialAgent

from alz_research_agent.agents.consultant_agent import (
    build_consultant_agent,
    consultant_agent,
)
from alz_research_agent.agents.critic_agent import build_critic_agent, critic_agent
from alz_research_agent.agents.research_agent import (
    build_research_agent,
    research_agent,
)
from alz_research_agent.config import Settings


def build_consult_critique_loop(settings: Settings | None = None) -> LoopAgent:
    """Constructs a fresh ``consult_critique_loop`` ``LoopAgent`` (``max_iterations=3``)."""
    c_agent = build_consultant_agent(settings) if settings is not None else consultant_agent
    k_agent = build_critic_agent(settings) if settings is not None else critic_agent
    return LoopAgent(
        name="consult_critique_loop",
        description=(
            "Iterative synthesis-and-critique loop (up to 3 iterations) between "
            "alz_consultant_agent and alz_evidence_critic_agent until exit_critique_loop "
            "escalates."
        ),
        sub_agents=[c_agent, k_agent],
        max_iterations=3,
    )


def build_research_pipeline(settings: Settings | None = None) -> SequentialAgent:
    """Constructs a fresh ``research_pipeline`` ``SequentialAgent``."""
    r_agent = build_research_agent(settings) if settings is not None else research_agent
    loop = build_consult_critique_loop(settings) if settings is not None else consult_critique_loop
    return SequentialAgent(
        name="research_pipeline",
        description=(
            "Executes recent (< 3 years) Alzheimer's literature collection via "
            "alz_researcher_agent followed by the iterative consult_critique_loop, "
            "leaving research_findings, consultant_draft, and critic_feedback in "
            "session state."
        ),
        sub_agents=[r_agent, loop],
    )


consult_critique_loop: LoopAgent = build_consult_critique_loop()
research_pipeline: SequentialAgent = build_research_pipeline()
