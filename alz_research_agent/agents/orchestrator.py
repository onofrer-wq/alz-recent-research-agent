"""Rubric #9 (Multi-Agent Patterns), Rubric #12 (Human-in-the-Loop Hooks) & Rubric #10 (Strategic Model Routing).

Root Orchestrator agent (``alz_orchestrator_agent``) implementing the
Coordinator-via-``AgentTool`` pattern:

Why ``AgentTool(agent=research_pipeline)`` instead of sub-agent transfer?
    If the Orchestrator delegated via ``sub_agents`` transfer, control of the
    final conversational turn would remain with the last sub-agent in the
    pipeline, bypassing the Orchestrator's human-in-the-loop release gate.
    By wrapping ``research_pipeline`` in an ``AgentTool``, the entire
    ``SequentialAgent`` (``research_agent`` -> ``consult_critique_loop``)
    executes as a tool call and returns control to ``alz_orchestrator_agent``,
    which then inspects ``consultant_draft`` / ``critic_feedback`` and invokes
    ``release_final_answer_to_user`` to enforce human approval and attach the
    mandatory scientific literacy disclaimer.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.tools import load_memory
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from alz_research_agent.agents.pipeline import (
    build_research_pipeline,
    research_pipeline,
)
from alz_research_agent.agents.prompts import build_instruction
from alz_research_agent.config import AgentRole, Settings, get_model_for_role
from alz_research_agent.hitl import (
    build_release_tool,
    record_human_approval_decision,
)
from alz_research_agent.tools import classify_alzheimers_question_scope


def build_orchestrator_agent(settings: Settings | None = None) -> LlmAgent:
    """Constructs the root ``alz_orchestrator_agent`` ``LlmAgent``.

    Args:
        settings: Optional ``Settings`` override.

    Returns:
        Configured root ``LlmAgent`` equipped with scope classification,
        ``AgentTool(agent=research_pipeline)``, HITL release/approval tools,
        and ``load_memory``.
    """
    pipeline = build_research_pipeline(settings) if settings is not None else research_pipeline
    hitl_mode = settings.hitl_mode if settings is not None else None
    release_tool = build_release_tool(hitl_mode)
    pipeline_tool = AgentTool(agent=pipeline)

    return LlmAgent(
        name="alz_orchestrator_agent",
        description=(
            "User-facing Alzheimer's recent research scientific literacy coordinator "
            "managing question scope classification, the research_pipeline AgentTool, "
            "long-term memory retrieval, and the human-in-the-loop release gate."
        ),
        model=get_model_for_role(AgentRole.ORCHESTRATOR, settings),
        instruction=build_instruction("ORCHESTRATOR"),
        generate_content_config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            temperature=0.2,
        ),
        tools=[
            classify_alzheimers_question_scope,
            pipeline_tool,
            release_tool,
            record_human_approval_decision,
            load_memory,
        ],
    )


orchestrator_agent: LlmAgent = build_orchestrator_agent()
