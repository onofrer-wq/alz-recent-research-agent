"""Rubric #16 (PII Redaction) & Rubric #13 (Structured JSON Logging).

Logging filter that scrubs PII from ``LogRecord.msg`` and any string or nested
collection extra attributes before records are serialized by ``JsonFormatter``.
"""

from __future__ import annotations

import logging

from alz_research_agent.privacy.pii_redactor import redact_text, redact_value

RESERVED_LOG_RECORD_ATTRS: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
        "event",
        "agent_id",
        "session_id",
        "invocation_id",
        "user_id",
        "trace_id",
        "span_id",
        "logging.googleapis.com/trace",
    }
)


class PiiRedactingLogFilter(logging.Filter):
    """Logging filter that redacts PII in log messages and structured extra fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Sanitizes ``record`` in-place and always returns ``True`` without raising.

        Args:
            record: Standard library ``LogRecord`` to sanitize.

        Returns:
            Always ``True`` so the sanitized record continues down the handler chain.
        """
        try:
            if record.args:
                rendered = record.getMessage()
                record.msg = redact_text(rendered)
                record.args = ()
            elif isinstance(record.msg, str):
                record.msg = redact_text(record.msg)

            for attr_name, attr_val in list(record.__dict__.items()):
                if attr_name in RESERVED_LOG_RECORD_ATTRS or attr_name.startswith("_"):
                    continue
                if isinstance(attr_val, (str, dict, list, tuple)):
                    record.__dict__[attr_name] = redact_value(attr_val)
        except Exception:
            return True
        return True
