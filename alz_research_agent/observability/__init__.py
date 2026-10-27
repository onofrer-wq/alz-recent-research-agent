"""Rubric #13 (Structured JSON Logging), Rubric #14 (Intent vs. Outcome Capture) & Rubric #15 (Distributed Tracing).

Observability package providing execution context propagation, structured JSON
logging correlated with Cloud Trace, OpenTelemetry instrumentation, and ADK
intent-versus-outcome audit plugins.
"""

from alz_research_agent.observability.context import (
    agent_scope,
    current_context,
    reset_execution_context,
    set_execution_context,
)
from alz_research_agent.observability.intent_outcome_plugin import IntentOutcomePlugin
from alz_research_agent.observability.logging_config import JsonFormatter, configure_logging
from alz_research_agent.observability.tracing import (
    TraceIds,
    configure_tracing,
    current_trace_ids,
    research_span,
)

__all__ = [
    "IntentOutcomePlugin",
    "JsonFormatter",
    "TraceIds",
    "agent_scope",
    "configure_logging",
    "configure_tracing",
    "current_context",
    "current_trace_ids",
    "reset_execution_context",
    "research_span",
    "set_execution_context",
]
