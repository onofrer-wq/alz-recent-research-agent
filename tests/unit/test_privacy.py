"""Rubric #16 (PII Redaction).

Unit tests for local regex PII redaction, Cloud DLP fallback with re-entrancy
protection, ``PiiRedactingLogFilter``, ``redact_session_copy``, and ``PiiRedactionPlugin``.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from google.adk.events.event import Event
from google.adk.sessions.session import Session
from google.genai import types

from alz_research_agent.observability.logging_config import configure_logging
from alz_research_agent.privacy.log_filter import PiiRedactingLogFilter
from alz_research_agent.privacy.pii_plugin import PiiRedactionPlugin
from alz_research_agent.privacy.pii_redactor import redact_session_copy, redact_text


def test_regex_fallback_covers_email_phone_ssn_name_and_dob() -> None:
    raw = (
        "My mother Maria Garcia (DOB: 1948-05-12, SSN 123-45-6789) can be reached at "
        "(555) 234-5678 or maria.garcia@clinic.org regarding her MRN: AB-99281."
    )
    scrubbed = redact_text(raw, mode="local")

    assert "Maria Garcia" not in scrubbed
    assert "[PERSON_NAME]" in scrubbed
    assert "1948-05-12" not in scrubbed
    assert "[DATE_OF_BIRTH]" in scrubbed
    assert "123-45-6789" not in scrubbed
    assert "[US_SOCIAL_SECURITY_NUMBER]" in scrubbed
    assert "(555) 234-5678" not in scrubbed
    assert "[PHONE_NUMBER]" in scrubbed
    assert "maria.garcia@clinic.org" not in scrubbed
    assert "[EMAIL_ADDRESS]" in scrubbed
    assert "AB-99281" not in scrubbed
    assert "[MEDICAL_RECORD_NUMBER]" in scrubbed


def test_dlp_failure_falls_back_to_regex_without_infinite_recursion(monkeypatch) -> None:
    configure_logging("INFO")
    monkeypatch.setenv("PII_MODE", "dlp")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "alz-prod-project")

    def _boom_dlp(text: str, project_id: str) -> str:
        raise RuntimeError(f"Simulated DLP outage for {project_id}")

    monkeypatch.setattr("alz_research_agent.privacy.pii_redactor._redact_with_dlp", _boom_dlp)

    result = redact_text("Contact patient John Doe at john.doe@example.com or 555-867-5309.")
    assert "john.doe@example.com" not in result
    assert "[EMAIL_ADDRESS]" in result
    assert "555-867-5309" not in result
    assert "[PHONE_NUMBER]" in result
    assert "John Doe" not in result
    assert "[PERSON_NAME]" in result


def test_pii_redacting_log_filter_scrubs_message_and_extras() -> None:
    log_filter = PiiRedactingLogFilter()
    logger = logging.getLogger("alz_research_agent.test_filter")
    record = logger.makeRecord(
        name=logger.name,
        level=logging.INFO,
        fn="test_privacy.py",
        lno=70,
        msg="User email is %s and phone is %s",
        args=("alice@hospital.org", "555-111-2222"),
        exc_info=None,
        extra={
            "user_note": "Patient Robert Smith born on 1951-11-03",
            "nested_meta": {
                "contact": "bob@hospital.org",
                "ssn": "999-88-7777",
            },
        },
    )

    assert log_filter.filter(record) is True
    assert "alice@hospital.org" not in record.getMessage()
    assert "[EMAIL_ADDRESS]" in record.getMessage()
    assert "[PHONE_NUMBER]" in record.getMessage()
    assert "Robert Smith" not in record.user_note
    assert "[PERSON_NAME]" in record.user_note
    assert "[DATE_OF_BIRTH]" in record.user_note
    assert record.nested_meta["contact"] == "[EMAIL_ADDRESS]"
    assert record.nested_meta["ssn"] == "[US_SOCIAL_SECURITY_NUMBER]"


def test_redact_session_copy_redacts_copy_and_leaves_original_intact() -> None:
    original_session = Session(
        id="sess-pii-01",
        app_name="alz_research_agent",
        user_id="user-01",
        state={"caregiver_email": "family@example.com"},
        events=[
            Event(
                author="user",
                content=types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(
                            text=(
                                "My mother Elena Morales (DOB: 1945-08-19) has early "
                                "Alzheimer's; email family@example.com."
                            )
                        )
                    ],
                ),
            )
        ],
    )

    redacted_copy = redact_session_copy(original_session, mode="local")

    # Original session must remain completely intact
    orig_text = original_session.events[0].content.parts[0].text
    assert "Elena Morales" in orig_text
    assert "1945-08-19" in orig_text
    assert "family@example.com" in orig_text
    assert original_session.state["caregiver_email"] == "family@example.com"

    # Deep copy must have all PII redacted
    copy_text = redacted_copy.events[0].content.parts[0].text
    assert "Elena Morales" not in copy_text
    assert "[PERSON_NAME]" in copy_text
    assert "1945-08-19" not in copy_text
    assert "[DATE_OF_BIRTH]" in copy_text
    assert "family@example.com" not in copy_text
    assert "[EMAIL_ADDRESS]" in copy_text
    assert redacted_copy.state["caregiver_email"] == "[EMAIL_ADDRESS]"


async def test_pii_redaction_plugin_redacts_user_message_and_model_request() -> None:
    plugin = PiiRedactionPlugin()
    user_msg = types.Content(
        role="user",
        parts=[types.Part.from_text(text="Email me at caregiver@domain.com about lecanemab.")],
    )
    returned = await plugin.on_user_message_callback(
        invocation_context=SimpleNamespace(),
        user_message=user_msg,
    )
    assert returned is not None
    assert "[EMAIL_ADDRESS]" in user_msg.parts[0].text
    assert "caregiver@domain.com" not in user_msg.parts[0].text

    llm_request = SimpleNamespace(
        contents=[
            types.Content(
                role="user",
                parts=[types.Part.from_text(text="Patient Carlos Ruiz SSN 111-22-3333")],
            ),
            types.Content(
                role="model",
                parts=[types.Part.from_text(text="Summarizing recent Phase 3 trials.")],
            ),
        ]
    )
    model_cb_result = await plugin.before_model_callback(
        callback_context=SimpleNamespace(),
        llm_request=llm_request,
    )
    assert model_cb_result is None
    assert "[PERSON_NAME]" in llm_request.contents[0].parts[0].text
    assert "[US_SOCIAL_SECURITY_NUMBER]" in llm_request.contents[0].parts[0].text
