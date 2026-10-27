"""Rubric #4 (Guided Error Handling).

Standardized error taxonomy (``ErrorType``) and structured response envelopes
that provide imperative ``recovery_instructions`` so ADK agents can self-correct
invalid arguments, recency violations, rate limits, or upstream outages.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from alz_research_agent.schemas import default_recency_floor


class ErrorType(StrEnum):
    """Canonical machine-readable error classifications for all ADK tools."""

    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    RECENCY_VIOLATION = "RECENCY_VIOLATION"
    RATE_LIMITED = "RATE_LIMITED"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    NO_RESULTS = "NO_RESULTS"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    INTERNAL = "INTERNAL"


def tool_error(
    error_type: ErrorType | str,
    message: str,
    recovery_instructions: str,
    *,
    retryable: bool = False,
    source: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Constructs a standardized guided error envelope for an ADK tool.

    Args:
        error_type: Classification from ``ErrorType``.
        message: Concise human- and model-readable error summary.
        recovery_instructions: Imperative instruction telling the calling agent
            exactly how to adjust its next action.
        retryable: Whether the same or adjusted tool call can be retried.
        source: Optional upstream data source name (e.g., ``"pubmed"``).
        details: Optional structured diagnostic metadata.

    Returns:
        Dictionary with ``status="error"`` and guided recovery fields.
    """
    resolved_type = ErrorType(error_type).value if not isinstance(error_type, ErrorType) else error_type.value
    envelope: dict[str, Any] = {
        "status": "error",
        "error_type": resolved_type,
        "message": message,
        "recovery_instructions": recovery_instructions,
        "retryable": bool(retryable),
    }
    if source is not None:
        envelope["source"] = str(source)
    if details is not None:
        envelope["details"] = details
    return envelope


def tool_success(
    payload: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Ensures a tool response dictionary carries ``status='success'``.

    Args:
        payload: Base dictionary to copy and mark as successful.
        **kwargs: Additional key-value pairs merged into the response.

    Returns:
        Dictionary guaranteed to have ``status="success"``.
    """
    result: dict[str, Any] = {"status": "success"}
    if payload:
        result.update(payload)
    if kwargs:
        result.update(kwargs)
    result["status"] = "success"
    return result


def is_tool_error(payload: Any) -> bool:
    """Returns ``True`` if ``payload`` is a structured tool error dictionary."""
    return isinstance(payload, dict) and payload.get("status") == "error"


def validation_error(
    exc: ValidationError,
    *,
    source: str | None = None,
    recency_floor: date | None = None,
) -> dict[str, Any]:
    """Translates a Pydantic ``ValidationError`` into a guided ``tool_error`` envelope.

    Detects recency floor violations and classifies them as ``RECENCY_VIOLATION``
    while always including the exact ``recency_floor`` ISO date when a date field
    fails validation.

    Args:
        exc: Pydantic ``ValidationError`` raised during input parsing.
        source: Optional tool source identifier.
        recency_floor: Optional override for the 3-year recency floor date.

    Returns:
        Guided error dictionary with imperative ``recovery_instructions``.
    """
    floor_str = (recency_floor or default_recency_floor()).isoformat()
    errors = exc.errors()
    field_summaries: list[str] = []
    touches_date = False
    is_recency_violation = False

    for err in errors:
        loc_parts = [str(part) for part in err.get("loc", ())]
        loc_str = ".".join(loc_parts) if loc_parts else "input"
        msg = str(err.get("msg", "Invalid value"))
        field_summaries.append(f"{loc_str}: {msg}")

        lowered_msg = msg.lower()
        if any(k in loc_str for k in ("since_date", "recency_floor", "until_date")):
            touches_date = True
        if "recency floor" in lowered_msg or "older than the recency floor" in lowered_msg:
            is_recency_violation = True
            touches_date = True

    joined_summary = "; ".join(field_summaries) or str(exc)

    if is_recency_violation:
        return tool_error(
            ErrorType.RECENCY_VIOLATION,
            message=f"Recency window violation: {joined_summary}",
            recovery_instructions=(
                f"Replace since_date with an ISO date (YYYY-MM-DD) on or after the "
                f"3-year recency floor ({floor_str}) and retry the search."
            ),
            retryable=True,
            source=source,
            details={"recency_floor": floor_str, "validation_errors": field_summaries},
        )

    if touches_date:
        return tool_error(
            ErrorType.INVALID_ARGUMENT,
            message=f"Invalid date argument: {joined_summary}",
            recovery_instructions=(
                f"Correct the since_date field to use ISO format YYYY-MM-DD on or after "
                f"the recency floor ({floor_str}) and retry."
            ),
            retryable=True,
            source=source,
            details={"recency_floor": floor_str, "validation_errors": field_summaries},
        )

    offending_fields = ", ".join(sorted({".".join(str(p) for p in e.get("loc", ())) or "input" for e in errors}))
    return tool_error(
        ErrorType.INVALID_ARGUMENT,
        message=f"Invalid tool arguments ({offending_fields}): {joined_summary}",
        recovery_instructions=(
            f"Fix the invalid field(s) [{offending_fields}] to match the tool schema "
            f"constraints ({joined_summary}) and invoke the tool again."
        ),
        retryable=True,
        source=source,
        details={"validation_errors": field_summaries},
    )
