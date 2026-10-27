"""Rubric #15 (Distributed Tracing).

OpenTelemetry configuration for Google Cloud Trace integration via ADK telemetry
utilities and a ``research_span`` context manager that instruments every external
literature search call with ``agent.id``, ``research.source``, ``recency.floor``,
and the sanitized/truncated search query.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, NamedTuple

from opentelemetry import trace

from alz_research_agent.observability.context import current_context
from alz_research_agent.privacy.pii_redactor import redact_text

logger = logging.getLogger(__name__)

_MAX_QUERY_ATTR_LEN = 200


class TraceIds(NamedTuple):
    """Container for hexadecimal OpenTelemetry ``(trace_id, span_id)``."""

    trace_id: str | None
    span_id: str | None

    def get(self, key: str, default: str | None = None) -> str | None:
        """Provides dictionary-style ``.get()`` access in addition to tuple unpacking."""
        if key == "trace_id":
            return self.trace_id if self.trace_id is not None else default
        if key == "span_id":
            return self.span_id if self.span_id is not None else default
        return default


def configure_tracing(
    *,
    otel_to_cloud: bool | None = None,
    service_name: str | None = None,
) -> bool:
    """Configures OpenTelemetry exporters for Google Cloud when ``OTEL_TO_CLOUD=true``.

    Calls ``get_gcp_exporters`` (from ``google.adk.telemetry.google_cloud``) with
    traces, metrics, and logs enabled and passes the resulting hooks to
    ``maybe_set_otel_providers`` (from ``google.adk.telemetry.setup``). Never
    raises an exception if Cloud credentials or project settings are unavailable.

    Args:
        otel_to_cloud: Optional override for the ``OTEL_TO_CLOUD`` environment variable.
        service_name: Optional override for ``OTEL_SERVICE_NAME``.

    Returns:
        ``True`` if Cloud OpenTelemetry providers were configured, ``False`` otherwise.
    """
    if service_name:
        os.environ.setdefault("OTEL_SERVICE_NAME", service_name)

    resolved_flag = (
        otel_to_cloud
        if otel_to_cloud is not None
        else os.environ.get("OTEL_TO_CLOUD", "false").strip().lower() in ("true", "1", "yes")
    )
    if not resolved_flag:
        return False

    try:
        from google.adk.telemetry.google_cloud import get_gcp_exporters
        from google.adk.telemetry.setup import maybe_set_otel_providers

        otel_hooks = get_gcp_exporters(
            enable_cloud_tracing=True,
            enable_cloud_metrics=True,
            enable_cloud_logging=True,
        )
        maybe_set_otel_providers(otel_hooks_to_setup=[otel_hooks])
        return True
    except Exception as exc:
        logger.warning(
            "Failed to initialize Google Cloud OpenTelemetry exporters; continuing with local tracing: %s",
            type(exc).__name__,
            extra={"event": "OTEL_CLOUD_SETUP_WARNING", "error_type": type(exc).__name__},
        )
        return False


def current_trace_ids() -> TraceIds:
    """Returns the active OpenTelemetry ``(trace_id, span_id)`` formatted as hex strings.

    Returns:
        ``TraceIds(trace_id, span_id)`` where each element is a lowercase hexadecimal
        string when a valid span is active, or ``(None, None)`` otherwise.
    """
    try:
        span = trace.get_current_span()
        if span is None:
            return TraceIds(None, None)
        span_ctx = span.get_span_context()
        if span_ctx is None or not getattr(span_ctx, "is_valid", False):
            return TraceIds(None, None)
        return TraceIds(f"{span_ctx.trace_id:032x}", f"{span_ctx.span_id:016x}")
    except Exception:
        return TraceIds(None, None)


@contextmanager
def research_span(
    source: str,
    query: str,
    *,
    recency_floor: str | int = 3,
    agent_id: str | None = None,
) -> Iterator[Any]:
    """Opens an OpenTelemetry span named ``research.search.<source>`` for literature queries.

    Sets ``agent.id``, ``research.source``, ``recency.floor``, and the PII-redacted,
    truncated query on the span.

    Args:
        source: Short identifier of the literature API (e.g., ``"pubmed"``, ``"semantic_scholar"``).
        query: Search query string sent to the external literature API.
        recency_floor: Minimum publication date or recency window (in years or ISO date).
        agent_id: Optional explicit agent identifier; defaults to the active context ``agent_id``.

    Yields:
        The active OpenTelemetry ``Span`` object.
    """
    normalized_source = source.strip().lower().replace(" ", "_") or "unknown"
    span_name = f"research.search.{normalized_source}"
    resolved_agent_id = agent_id or current_context().get("agent_id") or "unknown"
    sanitized_query = redact_text(query or "")[:_MAX_QUERY_ATTR_LEN]

    tracer = trace.get_tracer("alz_research_agent.tracing")
    with tracer.start_as_current_span(span_name) as span:
        span.set_attribute("agent.id", resolved_agent_id)
        span.set_attribute("research.source", normalized_source)
        span.set_attribute("recency.floor", str(recency_floor))
        span.set_attribute("research.query", sanitized_query)
        span.set_attribute("query", sanitized_query)
        yield span
