"""Rubric #6 (History Compaction) & Rubric #10 (Strategic Model Routing).

Sliding-window event compaction (``EventsCompactionConfig`` + ``LlmEventSummarizer``)
and static prefix context caching (``ContextCacheConfig``) for the ADK ``App``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps.app import EventsCompactionConfig
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.models.google_llm import Gemini

from alz_research_agent.config import AgentRole, Settings, get_model_for_role, get_settings

if TYPE_CHECKING:
    from google.adk.models.base_llm import BaseLlm

COMPACTION_PROMPT_TEMPLATE = (
    "You are compacting the conversation history of the Alzheimer's Recent Research "
    "Scientific Literacy Assistant. Produce a dense, faithful summary that strictly "
    "preserves:\n"
    "1. User Questions & Clamped Recency Window (since_date_to_use).\n"
    "2. All Cited Record IDs (PMIDs, NCT IDs, Europe PMC IDs) and publication dates.\n"
    "3. Evidence Confidence Grades (high, moderate, low) and any SPECULATIVE "
    "EXTRAPOLATION labels attached to claims.\n"
    "4. Human-in-the-Loop Approval State (requires_human_review, approval_status: "
    "pending / approved / rejected / released, and any pending final_answer draft).\n\n"
    "Conversation History:\n{conversation_history}"
)


def build_compaction_config(
    settings: Settings | None = None,
    *,
    llm: BaseLlm | None = None,
) -> EventsCompactionConfig:
    """Builds the ADK ``EventsCompactionConfig`` with an ``LlmEventSummarizer``.

    Uses the ``SUMMARIZER`` model tier from strategic model routing unless a custom
    ``BaseLlm`` instance is injected (e.g., in offline tests).

    Args:
        settings: Optional ``Settings`` override.
        llm: Optional ``BaseLlm`` instance for ``LlmEventSummarizer``.

    Returns:
        Configured ``EventsCompactionConfig``.
    """
    cfg = settings or get_settings()
    summarizer_llm = llm or Gemini(model=get_model_for_role(AgentRole.SUMMARIZER, cfg))
    summarizer = LlmEventSummarizer(
        llm=summarizer_llm,
        prompt_template=COMPACTION_PROMPT_TEMPLATE,
    )
    return EventsCompactionConfig(
        compaction_interval=max(1, cfg.compaction_interval),
        overlap_size=max(0, cfg.compaction_overlap),
        summarizer=summarizer,
    )


def build_cache_config(
    settings: Settings | None = None,
) -> ContextCacheConfig:
    """Builds the ADK ``ContextCacheConfig`` to cache the static constitutional prompt prefix.

    Configures 10 cache intervals, a 30-minute (1800s) TTL, and a 2048-token
    minimum threshold.

    Args:
        settings: Optional ``Settings`` override (reserved for future tuning).

    Returns:
        Configured ``ContextCacheConfig``.
    """
    del settings
    return ContextCacheConfig(
        cache_intervals=10,
        ttl_seconds=1800,
        min_tokens=2048,
    )
