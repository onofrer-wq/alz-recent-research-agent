"""Rubric #16 (PII Redaction).

PII redaction engine supporting Google Cloud Sensitive Data Protection (DLP)
when ``PII_MODE=dlp`` and a deterministic regular-expression fallback for local
development, offline testing, or Cloud DLP unavailability.

Includes a ``ContextVar`` re-entrancy guard to prevent infinite recursion when
``PiiRedactingLogFilter`` invokes ``redact_text`` while ``redact_text`` itself
emits a warning log on Cloud DLP failure.
"""

from __future__ import annotations

import copy
import logging
import os
import re
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from google.adk.sessions.session import Session

logger = logging.getLogger(__name__)

_in_redact_guard: ContextVar[bool] = ContextVar("_in_redact_guard", default=False)

DLP_INFO_TYPES: tuple[str, ...] = (
    "PERSON_NAME",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "US_SOCIAL_SECURITY_NUMBER",
    "DATE_OF_BIRTH",
    "STREET_ADDRESS",
    "CREDIT_CARD_NUMBER",
)

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CREDIT_CARD_RE = re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b")
_MRN_RE = re.compile(
    r"\b(?:MRN|Medical\s+Record(?:\s+Number)?)\s*[:#-]?\s*[A-Z0-9-]{4,}\b",
    re.IGNORECASE,
)
_DOB_LABELED_RE = re.compile(
    r"(\b(?:DOB|date\s+of\s+birth|born(?:\s+on)?)\s*[:=-]?\s*)"
    r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2},?\s+\d{4})",
    re.IGNORECASE,
)
_DOB_STANDALONE_RE = re.compile(
    r"\b(?:(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])/(?:19\d{2}|20[0-2]\d)|"
    r"19\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01]))\b"
)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]\d{3}[-.\s]\d{4}(?!\d)")
_PERSON_CONTEXT_RE = re.compile(
    r"((?i:\b(?:my\s+(?:mother|father|mom|dad|grandmother|grandfather|husband|wife|sister|brother|uncle|aunt)|"
    r"patient(?:\s+named)?|my\s+name\s+is|caregiver)\s*[:,-]?\s+))"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})"
)


def _redact_with_regex(text: str) -> str:
    """Applies deterministic local regex replacements for common clinical/personal PII."""
    if not text:
        return text
    redacted = _EMAIL_RE.sub("[EMAIL_ADDRESS]", text)
    redacted = _SSN_RE.sub("[US_SOCIAL_SECURITY_NUMBER]", redacted)
    redacted = _CREDIT_CARD_RE.sub("[CREDIT_CARD_NUMBER]", redacted)
    redacted = _MRN_RE.sub("[MEDICAL_RECORD_NUMBER]", redacted)
    redacted = _DOB_LABELED_RE.sub(r"\1[DATE_OF_BIRTH]", redacted)
    redacted = _DOB_STANDALONE_RE.sub("[DATE_OF_BIRTH]", redacted)
    redacted = _PHONE_RE.sub("[PHONE_NUMBER]", redacted)
    redacted = _PERSON_CONTEXT_RE.sub(r"\1[PERSON_NAME]", redacted)
    return redacted


def _redact_with_dlp(text: str, project_id: str) -> str:
    """Calls Google Cloud Sensitive Data Protection (DLP) ``deidentify_content``."""
    from google.cloud import dlp_v2

    client = dlp_v2.DlpServiceClient()
    parent = f"projects/{project_id}/locations/global"
    inspect_config = {
        "info_types": [{"name": info_type} for info_type in DLP_INFO_TYPES],
    }
    deidentify_config = {
        "info_type_transformations": {
            "transformations": [
                {
                    "primitive_transformation": {
                        "replace_with_info_type_config": {},
                    }
                }
            ]
        }
    }
    response = client.deidentify_content(
        request={
            "parent": parent,
            "inspect_config": inspect_config,
            "deidentify_config": deidentify_config,
            "item": {"value": text},
        }
    )
    return str(response.item.value)


def redact_text(
    text: str,
    *,
    mode: str | None = None,
    project_id: str | None = None,
) -> str:
    """Redacts PII from ``text`` using Cloud DLP or local regex fallback.

    Protected by a ``ContextVar`` re-entrancy guard so that any log emitted
    during a Cloud DLP failure falls back directly to local regex without
    recursively re-entering Cloud DLP.

    Args:
        text: Input string potentially containing PII or PHI.
        mode: Optional override for ``PII_MODE`` (``"local"`` or ``"dlp"``).
        project_id: Optional override for ``GOOGLE_CLOUD_PROJECT``.

    Returns:
        Redacted string with sensitive entities replaced by ``[INFO_TYPE]`` tokens.
    """
    if not isinstance(text, str) or not text:
        return text

    if _in_redact_guard.get():
        return _redact_with_regex(text)

    token = _in_redact_guard.set(True)
    try:
        resolved_mode = (mode if mode is not None else os.environ.get("PII_MODE", "local")).strip().lower()
        resolved_project = (
            project_id if project_id is not None else os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        ).strip()

        if resolved_mode == "dlp" and resolved_project and resolved_project != "your-gcp-project-id":
            try:
                return _redact_with_dlp(text, resolved_project)
            except Exception as exc:
                logger.warning(
                    "Cloud DLP deidentify_content failed; falling back to local regex redaction: %s",
                    type(exc).__name__,
                    extra={"event": "PII_DLP_FALLBACK", "error_type": type(exc).__name__},
                )
                return _redact_with_regex(text)

        return _redact_with_regex(text)
    finally:
        _in_redact_guard.reset(token)


def redact_value(value: Any, *, mode: str | None = None, project_id: str | None = None) -> Any:
    """Recursively redacts PII across strings, dictionaries, lists, and tuples.

    Args:
        value: Arbitrary value to sanitize.
        mode: Optional override for ``PII_MODE``.
        project_id: Optional override for ``GOOGLE_CLOUD_PROJECT``.

    Returns:
        Sanitized copy of ``value`` with all strings redacted.
    """
    if isinstance(value, str):
        return redact_text(value, mode=mode, project_id=project_id)
    if isinstance(value, dict):
        return {k: redact_value(v, mode=mode, project_id=project_id) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(item, mode=mode, project_id=project_id) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item, mode=mode, project_id=project_id) for item in value)
    return value


def redact_session_copy(
    session: Session,
    *,
    mode: str | None = None,
    project_id: str | None = None,
) -> Session:
    """Creates a deep copy of an ADK ``Session`` with all event text and state redacted.

    Used before persisting session transcripts into long-term memory so that
    raw user PII/PHI never enters the memory store while leaving the active
    in-flight session object untouched.

    Args:
        session: Source ADK ``Session`` instance.
        mode: Optional override for ``PII_MODE``.
        project_id: Optional override for ``GOOGLE_CLOUD_PROJECT``.

    Returns:
        A deep-copied ``Session`` with all text parts and string state values redacted.
    """
    if hasattr(session, "model_copy"):
        sanitized: Session = session.model_copy(deep=True)
    else:
        sanitized = copy.deepcopy(session)

    if getattr(sanitized, "state", None) and isinstance(sanitized.state, dict):
        sanitized.state = redact_value(sanitized.state, mode=mode, project_id=project_id)

    for event in getattr(sanitized, "events", []) or []:
        content = getattr(event, "content", None)
        if content is None:
            continue
        for part in getattr(content, "parts", None) or []:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str) and part_text:
                part.text = redact_text(part_text, mode=mode, project_id=project_id)

    return sanitized
