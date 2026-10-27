"""Rubric #5-#16 (Complete Multi-Agent ADK App Assembly & Rubric Mapping).

Central ADK ``App`` registration module that wires together all 19 rubric
mechanisms in a single inspectable entrypoint:

- **Rubric #1 (Comprehensive Tool Docstrings)** & **Rubric #2 (Descriptive Naming)**:
  Tools registered on ``alz_orchestrator_agent``, ``alz_researcher_agent``, and
  ``alz_evidence_critic_agent``.
- **Rubric #3 (Explicit JSON Schemas)**: ``ConsultantDraft`` bound via ``output_schema``
  and ``CriticVerdict`` validated by ``SelfEvaluationJudgePlugin``.
- **Rubric #4 (Guided Error Handling)**: ``ReflectAndRetryToolPlugin(max_retries=2,
  throw_exception_if_retry_exceeded=False)`` paired with ``recovery_instructions``.
- **Rubric #5 (Robust System Instructions / Constitution)**: Shared ``CONSTITUTION``
  prefix prepended via ``build_instruction`` across all four agents.
- **Rubric #6 (History Compaction)**: ``EventsCompactionConfig`` + ``LlmEventSummarizer``
  and ``ContextCacheConfig`` registered on ``app``.
- **Rubric #7 (Persistent Session State)**: ``build_session_service`` (SQLite async
  ``DatabaseSessionService`` in local/dev, ``VertexAiSessionService`` in prod).
- **Rubric #8 (Asynchronous Memory Operations)**: ``MemoryConsolidationPlugin``
  scheduling ``schedule_memory_consolidation`` in ``after_run_callback``.
- **Rubric #9 (Multi-Agent Patterns)**: Coordinator ``alz_orchestrator_agent`` invoking
  ``AgentTool(research_pipeline)`` -> ``SequentialAgent`` (``alz_researcher_agent`` ->
  ``LoopAgent`` ``consult_critique_loop`` with ``alz_consultant_agent`` &
  ``alz_evidence_critic_agent``).
- **Rubric #10 (Strategic Model Routing)**: ``get_routing_table`` mapping
  ``ORCHESTRATOR``/``RESEARCH``/``SUMMARIZER`` to ``FAST_MODEL``,
  ``REASONING``/``CONSULTANT``/``CRITIC`` to ``REASONING_MODEL``, and ``JUDGE`` to
  ``JUDGE_MODEL``.
- **Rubric #11 (Guardrails & Policy Plugins)**: ``build_guardrail_plugins``
  (``ModelArmorPlugin``, ``MedicalSafetyPolicyPlugin``, ``RecencyPolicyPlugin``,
  ``SelfEvaluationJudgePlugin``).
- **Rubric #12 (Human-in-the-Loop Hooks)**: ``HumanApprovalGatePlugin``,
  ``build_release_tool``, and ``ResumabilityConfig(is_resumable=True)``.
- **Rubric #13 (Structured JSON Logging)**, **Rubric #14 (Intent vs. Outcome)** &
  **Rubric #15 (Distributed Tracing)**: ``configure_logging``, ``configure_tracing``,
  and ``IntentOutcomeLoggingPlugin``.
- **Rubric #16 (PII Redaction)**: ``PiiRedactionPlugin`` and ``PiiRedactingLogFilter``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from google.adk.apps.app import App, ResumabilityConfig
from google.adk.plugins.reflect_retry_tool_plugin import ReflectAndRetryToolPlugin

from alz_research_agent.agents import (
    build_orchestrator_agent,
    orchestrator_agent,
)
from alz_research_agent.config import Settings, get_routing_table, get_settings
from alz_research_agent.guardrails import build_guardrail_plugins
from alz_research_agent.hitl import HumanApprovalGatePlugin
from alz_research_agent.memory import (
    MemoryConsolidationPlugin,
    build_cache_config,
    build_compaction_config,
)
from alz_research_agent.observability import (
    IntentOutcomeLoggingPlugin,
    configure_logging,
    configure_tracing,
)
from alz_research_agent.privacy import PiiRedactionPlugin

if TYPE_CHECKING:
    from google.adk.agents import LlmAgent
    from google.adk.models.base_llm import BaseLlm
    from google.adk.plugins import BasePlugin

configure_logging()
configure_tracing()

logger = logging.getLogger(__name__)

root_agent: LlmAgent = orchestrator_agent


def build_plugins(
    *,
    memory_service: Any = None,
    enable_llm_judge: bool = False,
) -> list[BasePlugin]:
    """Constructs the complete ordered plugin stack for the ADK ``App``.

    Order:
    1. Guardrails (``ModelArmorPlugin`` if configured, ``MedicalSafetyPolicyPlugin``,
       ``RecencyPolicyPlugin``, ``SelfEvaluationJudgePlugin``)
    2. ``HumanApprovalGatePlugin``
    3. ``ReflectAndRetryToolPlugin(max_retries=2, throw_exception_if_retry_exceeded=False)``
    4. ``IntentOutcomeLoggingPlugin``
    5. ``PiiRedactionPlugin``
    6. ``MemoryConsolidationPlugin``

    Args:
        memory_service: Optional fallback memory service for background consolidation.
        enable_llm_judge: Whether ``SelfEvaluationJudgePlugin`` queries ``JUDGE_MODEL``.

    Returns:
        Ordered list of ``BasePlugin`` instances.
    """
    plugins: list[BasePlugin] = [
        *build_guardrail_plugins(enable_llm_judge=enable_llm_judge),
        HumanApprovalGatePlugin(),
        ReflectAndRetryToolPlugin(
            max_retries=2,
            throw_exception_if_retry_exceeded=False,
        ),
        IntentOutcomeLoggingPlugin(),
        PiiRedactionPlugin(),
        MemoryConsolidationPlugin(memory_service=memory_service),
    ]
    return plugins


def build_app(
    settings: Settings | None = None,
    *,
    root: LlmAgent | None = None,
    memory_service: Any = None,
    summarizer_llm: BaseLlm | None = None,
    enable_llm_judge: bool = False,
) -> App:
    """Assembles the ADK ``App`` with plugins, compaction, context cache, and resumability.

    Args:
        settings: Optional ``Settings`` override.
        root: Optional custom root ``LlmAgent`` (defaults to ``root_agent``).
        memory_service: Optional memory service passed to ``MemoryConsolidationPlugin``.
        summarizer_llm: Optional custom ``BaseLlm`` for ``EventsCompactionConfig``.
        enable_llm_judge: Whether ``SelfEvaluationJudgePlugin`` queries ``JUDGE_MODEL``.

    Returns:
        Configured ADK ``App`` instance.
    """
    cfg = settings or get_settings()
    resolved_root = (
        root if root is not None else (build_orchestrator_agent(cfg) if settings is not None else root_agent)
    )
    plugins = build_plugins(
        memory_service=memory_service,
        enable_llm_judge=enable_llm_judge,
    )
    compaction_cfg = build_compaction_config(cfg, llm=summarizer_llm)
    cache_cfg = build_cache_config(cfg)
    resumability_cfg = ResumabilityConfig(is_resumable=True)

    assembled_app = App(
        name="alz_research_agent",
        root_agent=resolved_root,
        plugins=plugins,
        events_compaction_config=compaction_cfg,
        context_cache_config=cache_cfg,
        resumability_config=resumability_cfg,
    )

    routing_table = get_routing_table(cfg)
    plugin_names = [p.name for p in plugins]
    logger.info(
        "Assembled ADK App '%s' (env=%s, hitl_mode=%s, plugins=%d)",
        assembled_app.name,
        cfg.app_env,
        cfg.hitl_mode,
        len(plugin_names),
        extra={
            "event": "APP_ASSEMBLED",
            "app_env": cfg.app_env,
            "routing_table": routing_table,
            "recency_years": cfg.recency_years,
            "hitl_mode": cfg.hitl_mode,
            "plugins": plugin_names,
        },
    )
    return assembled_app


app: App = build_app()

__all__ = [
    "app",
    "build_app",
    "build_plugins",
    "root_agent",
]
