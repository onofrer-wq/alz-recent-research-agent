"""Rubric #6 (History Compaction), Rubric #7 (Persistent Session State) & Rubric #8 (Asynchronous Memory Operations).

Memory and session management package providing persistent session services,
event compaction, static context caching, and asynchronous PII-redacted memory
consolidation.
"""

from alz_research_agent.memory.background import (
    MemoryConsolidationPlugin,
    drain_background_memory_tasks,
    schedule_memory_consolidation,
)
from alz_research_agent.memory.compaction import (
    COMPACTION_PROMPT_TEMPLATE,
    build_cache_config,
    build_compaction_config,
)
from alz_research_agent.memory.services import (
    build_memory_service,
    build_session_service,
)

__all__ = [
    "COMPACTION_PROMPT_TEMPLATE",
    "MemoryConsolidationPlugin",
    "build_cache_config",
    "build_compaction_config",
    "build_memory_service",
    "build_session_service",
    "drain_background_memory_tasks",
    "schedule_memory_consolidation",
]
