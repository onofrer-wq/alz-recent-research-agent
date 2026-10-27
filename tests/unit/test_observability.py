"""Rubric #13 (Structured JSON Logging), Rubric #14 (Intent vs. Outcome Capture) & Rubric #15 (Distributed Tracing).

Unit tests for execution context propagation, JSON log formatting with Cloud Trace
correlation, OpenTelemetry ``research_span`` instrumentation, and ``IntentOutcomePlugin``.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from alz_research_agent.observability.context import (
    agent_scope,
    current_context,
    reset_execution_context,
)
from alz_research_agent.observability.intent_outcome_plugin import (
    AUDIT_LOGGER_NAME,
    IntentOutcomePlugin,
)
from alz_research_agent.observability.logging_config import (
    JsonFormatter,
    configure_logging,
)
from alz_research_agent.observability.tracing import current_trace_ids, research_span
from alz_research_agent.privacy.log_filter import PiiRedactingLogFilter


def test_json_formatter_emits_mandatory_keys_and_agent_scope(monkeypatch) -> None:
    reset_execution_context()
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "alz-test-project")

    formatter = JsonFormatter(project_id="alz-test-project")
    logger = logging.getLogger("alz_research_agent.test_formatter")

    with agent_scope(
        "alz_researcher_agent",
        session_id="sess-101",
        invocation_id="inv-202",
        user_id="user-303",
    ):
        record = logger.makeRecord(
            name=logger.name,
            level=logging.INFO,
            fn="test_observability.py",
            lno=42,
            msg="Searching PubMed for lecanemab phase 3 data",
            args=(),
            exc_info=None,
            extra={
                "event": "LITERATURE_QUERY",
                "trace_id": "0123456789abcdef0123456789abcdef",
                "span_id": "0123456789abcdef",
                "source_api": "pubmed",
            },
        )
        formatted = formatter.format(record)

    assert current_context()["agent_id"] is None

    payload = json.loads(formatted)
    mandatory_keys = {
        "timestamp",
        "severity",
        "logger",
        "message",
        "event",
        "agent_id",
        "session_id",
        "invocation_id",
        "user_id",
        "trace_id",
        "span_id",
        "logging.googleapis.com/trace",
    }
    assert mandatory_keys.issubset(payload.keys())
    assert payload["agent_id"] == "alz_researcher_agent"
    assert payload["session_id"] == "sess-101"
    assert payload["invocation_id"] == "inv-202"
    assert payload["user_id"] == "user-303"
    assert payload["event"] == "LITERATURE_QUERY"
    assert payload["source_api"] == "pubmed"
    assert (
        payload["logging.googleapis.com/trace"] == "projects/alz-test-project/traces/0123456789abcdef0123456789abcdef"
    )


async def test_intent_outcome_plugin_emits_paired_tool_events_with_redacted_email() -> None:
    reset_execution_context()
    plugin = IntentOutcomePlugin()

    captured_records: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured_records.append(record)

    handler = _ListHandler()
    handler.addFilter(PiiRedactingLogFilter())
    audit_logger = logging.getLogger(AUDIT_LOGGER_NAME)
    audit_logger.setLevel(logging.INFO)
    audit_logger.addHandler(handler)

    try:
        fake_tool = SimpleNamespace(name="search_pubmed_recent")
        fake_tool_ctx = SimpleNamespace(
            agent_name="alz_researcher_agent",
            function_call_id="fc-001",
        )
        raw_tool_args = {
            "query": "donanemab amyloid clearance contact caregiver@example.org for details",
            "max_results": 5,
        }

        await plugin.before_tool_callback(
            tool=fake_tool,
            tool_args=raw_tool_args,
            tool_context=fake_tool_ctx,
        )
        await plugin.after_tool_callback(
            tool=fake_tool,
            tool_args=raw_tool_args,
            tool_context=fake_tool_ctx,
            result={"status": "ok", "papers": [{"pmid": "39000001"}, {"pmid": "39000002"}]},
        )
    finally:
        audit_logger.removeHandler(handler)

    assert len(captured_records) == 2
    intent_rec, outcome_rec = captured_records[0], captured_records[1]

    assert getattr(intent_rec, "event", None) == "AGENT_INTENT"
    assert getattr(intent_rec, "tool_name", None) == "search_pubmed_recent"
    tool_args_logged = getattr(intent_rec, "tool_args", {})
    assert "[EMAIL_ADDRESS]" in tool_args_logged["query"]
    assert "caregiver@example.org" not in tool_args_logged["query"]

    assert getattr(outcome_rec, "event", None) == "AGENT_OUTCOME"
    assert getattr(outcome_rec, "tool_name", None) == "search_pubmed_recent"
    assert getattr(outcome_rec, "status", None) == "ok"
    assert getattr(outcome_rec, "error_type", None) is None
    assert getattr(outcome_rec, "result_count", None) == 2
    assert getattr(outcome_rec, "intent_matched_outcome", None) is True
    assert isinstance(getattr(outcome_rec, "latency_ms", None), float)


def test_research_span_records_name_and_attributes_with_in_memory_tracer(monkeypatch) -> None:
    reset_execution_context()
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", lambda name, *a, **kw: provider.get_tracer(name))

    with agent_scope("alz_researcher_agent"):
        with research_span(
            "pubmed",
            "Alzheimer's tau PET biomarker inquiry from patient@example.com",
            recency_floor="2023-10-27",
        ) as active_span:
            assert active_span is not None
            trace_id, span_id = current_trace_ids()
            assert trace_id is not None and len(trace_id) == 32
            assert span_id is not None and len(span_id) == 16

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    finished = spans[0]
    assert finished.name == "research.search.pubmed"
    attrs = dict(finished.attributes or {})
    assert attrs["agent.id"] == "alz_researcher_agent"
    assert attrs["research.source"] == "pubmed"
    assert attrs["recency.floor"] == "2023-10-27"
    assert "[EMAIL_ADDRESS]" in str(attrs["research.query"])
    assert "patient@example.com" not in str(attrs["research.query"])


def test_configure_logging_is_idempotent() -> None:
    root_logger = configure_logging("INFO")
    count_first = len(root_logger.handlers)
    configure_logging("DEBUG")
    count_second = len(root_logger.handlers)
    assert count_first == count_second
