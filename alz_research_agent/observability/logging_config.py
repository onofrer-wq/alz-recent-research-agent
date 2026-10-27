"""Rubric #13 (Structured JSON Logging) & Rubric #15 (Distributed Tracing).

Structured single-line JSON logging formatter and idempotent root-logger setup.
Automatically injects execution context IDs (``agent_id``, ``session_id``,
``invocation_id``, ``user_id``), active OpenTelemetry ``trace_id`` and ``span_id``,
and ``logging.googleapis.com/trace`` for Google Cloud Logging / Cloud Trace
correlation, while filtering all messages and extras through ``PiiRedactingLogFilter``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

from alz_research_agent.observability.context import current_context
from alz_research_agent.observability.tracing import current_trace_ids
from alz_research_agent.privacy.log_filter import (
    RESERVED_LOG_RECORD_ATTRS,
    PiiRedactingLogFilter,
)

_HANDLER_MARKER_ATTR = "_alz_json_handler"


class JsonFormatter(logging.Formatter):
    """Formats ``LogRecord`` instances into single-line structured JSON objects."""

    def __init__(self, *, project_id: str | None = None) -> None:
        """Initializes the JSON formatter.

        Args:
            project_id: Optional GCP project ID used to populate
                ``logging.googleapis.com/trace``. Falls back to ``GOOGLE_CLOUD_PROJECT``.
        """
        super().__init__()
        self._project_id = project_id

    def _resolve_project_id(self) -> str | None:
        """Returns the configured GCP project ID if valid, else ``None``."""
        candidate = (
            self._project_id if self._project_id is not None else os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        ).strip()
        if candidate and candidate != "your-gcp-project-id":
            return candidate
        return None

    def format(self, record: logging.LogRecord) -> str:
        """Serializes ``record`` into a single-line JSON string.

        Args:
            record: Standard library ``LogRecord`` to format.

        Returns:
            JSON-encoded log line.
        """
        ctx = current_context()
        trace_id, span_id = current_trace_ids()

        resolved_trace_id = getattr(record, "trace_id", None) or trace_id
        resolved_span_id = getattr(record, "span_id", None) or span_id

        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "event": getattr(record, "event", None) or record.levelname,
            "agent_id": getattr(record, "agent_id", None) or ctx.get("agent_id"),
            "session_id": getattr(record, "session_id", None) or ctx.get("session_id"),
            "invocation_id": getattr(record, "invocation_id", None) or ctx.get("invocation_id"),
            "user_id": getattr(record, "user_id", None) or ctx.get("user_id"),
            "trace_id": resolved_trace_id,
            "span_id": resolved_span_id,
        }

        project_id = self._resolve_project_id()
        if project_id and resolved_trace_id:
            payload["logging.googleapis.com/trace"] = f"projects/{project_id}/traces/{resolved_trace_id}"

        for attr_name, attr_val in record.__dict__.items():
            if attr_name in RESERVED_LOG_RECORD_ATTRS or attr_name.startswith("_"):
                continue
            payload[attr_name] = attr_val

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(
    level: str | int | None = None,
    *,
    project_id: str | None = None,
) -> logging.Logger:
    """Idempotently configures the root logger with ``JsonFormatter`` and ``PiiRedactingLogFilter``.

    Args:
        level: Optional log level name or integer; defaults to ``LOG_LEVEL`` or ``INFO``.
        project_id: Optional GCP project ID for Cloud Trace log correlation.

    Returns:
        The configured root ``logging.Logger``.
    """
    resolved_level_raw = level if level is not None else os.environ.get("LOG_LEVEL", "INFO")
    if isinstance(resolved_level_raw, str):
        resolved_level = getattr(logging, resolved_level_raw.strip().upper(), logging.INFO)
    else:
        resolved_level = int(resolved_level_raw)

    root_logger = logging.getLogger()
    root_logger.setLevel(resolved_level)

    formatter = JsonFormatter(project_id=project_id)
    pii_filter = PiiRedactingLogFilter()

    existing_handler: logging.Handler | None = None
    for handler in root_logger.handlers:
        if getattr(handler, _HANDLER_MARKER_ATTR, False):
            existing_handler = handler
            break

    if existing_handler is None:
        stream_handler = logging.StreamHandler(sys.stderr)
        setattr(stream_handler, _HANDLER_MARKER_ATTR, True)
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(pii_filter)
        root_logger.addHandler(stream_handler)
    else:
        existing_handler.setFormatter(formatter)
        if not any(isinstance(f, PiiRedactingLogFilter) for f in existing_handler.filters):
            existing_handler.addFilter(pii_filter)

    return root_logger
