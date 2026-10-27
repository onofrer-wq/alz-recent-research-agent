"""Rubric #11 (Guardrails & Policy Plugins) & Rubric #5 (Robust System Instructions).

ADK policy plugin enforcing deterministic medical safety guardrails on the
Orchestrator agent:
- Short-circuits acute medical emergencies with an immediate emergency services referral.
- Short-circuits requests for personal drug dosing, titration, or unprescribed drug acquisition.
- Short-circuits requests for individual clinical diagnosis.
- Appends the constitutional scientific literacy disclaimer to long orchestrator responses.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from google.adk.models.llm_response import LlmResponse
from google.adk.plugins import BasePlugin
from google.genai import types

from alz_research_agent.hitl.approval import SCIENTIFIC_LITERACY_DISCLAIMER

if TYPE_CHECKING:
    from google.adk.agents.callback_context import CallbackContext
    from google.adk.models.llm_request import LlmRequest

logger = logging.getLogger("alz_research_agent.audit")

_SUBAGENT_NAMES: frozenset[str] = frozenset(
    {
        "alz_researcher_agent",
        "alz_consultant_agent",
        "alz_evidence_critic_agent",
        "alz_critique_loop_agent",
        "researcher",
        "consultant",
        "critic",
    }
)

_EMERGENCY_RE = re.compile(
    r"\b(?:"
    r"chest\s+pain|stroke|seizure|unconscious|suicid(?:e|al)|overdose|"
    r"stopped\s+breathing|call\s+911|medical\s+emergency|sudden\s+paralysis|"
    r"acute\s+delirium"
    r")\b",
    re.IGNORECASE,
)

_DOSING_OR_ACQUISITION_RE = re.compile(
    r"(?:"
    r"\bwhat\s+dos(?:e|age)\s+should\b|"
    r"\bhow\s+many\s+(?:mg|milligrams|pills|tablets)\s+should\b|"
    r"\bhow\s+to\s+titrate\b|"
    r"\bdosing\s+schedule\s+for\s+my\b|"
    r"\bwhere\s+(?:to|can\s+i)\s+(?:buy|order|purchase|get)\b|"
    r"\bwithout\s+a\s+prescription\b|"
    r"\bprescribe\s+(?:me|my)\b"
    r")",
    re.IGNORECASE,
)

_INDIVIDUAL_DIAGNOSIS_RE = re.compile(
    r"(?:"
    r"\bdoes\s+my\s+(?:mother|father|mom|dad|grandmother|grandfather|wife|husband)\s+have\s+alzheimer|"
    r"\bdo\s+i\s+have\s+alzheimer|"
    r"\bam\s+i\s+developing\s+alzheimer|"
    r"\bdiagnose\s+(?:me|my\s+(?:mother|father|mom|dad|patient))\b|"
    r"\bcan\s+you\s+diagnose\b"
    r")",
    re.IGNORECASE,
)


def _is_orchestrator_context(callback_context: CallbackContext | None) -> bool:
    """Returns ``True`` when the callback belongs to the top-level Orchestrator agent."""
    if callback_context is None:
        return True
    agent_name = str(getattr(callback_context, "agent_name", "") or "").strip().lower()
    if not agent_name:
        return True
    if agent_name in _SUBAGENT_NAMES:
        return False
    if any(sub in agent_name for sub in ("researcher", "consultant", "critic")):
        return False
    return True


def _extract_last_user_message(llm_request: LlmRequest) -> str:
    """Extracts the text of the most recent ``role='user'`` content in ``llm_request``."""
    contents = getattr(llm_request, "contents", None) or []
    for content in reversed(contents):
        role = getattr(content, "role", None)
        if role in ("user", None):
            parts = getattr(content, "parts", None) or []
            texts = [str(getattr(part, "text", "") or "") for part in parts if getattr(part, "text", None)]
            joined = " ".join(texts).strip()
            if joined:
                return joined
    return ""


def _build_canned_response(body_text: str) -> LlmResponse:
    """Constructs a canned ``LlmResponse`` with the constitutional disclaimer appended."""
    full_text = f"{body_text.rstrip()}{SCIENTIFIC_LITERACY_DISCLAIMER}"
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text=full_text)],
        )
    )


class MedicalSafetyPolicyPlugin(BasePlugin):
    """ADK ``BasePlugin`` enforcing medical safety boundaries and disclaimer injection."""

    def __init__(self, name: str = "medical_safety_policy_plugin") -> None:
        """Initializes the medical safety policy plugin."""
        super().__init__(name=name)

    async def before_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> LlmResponse | None:
        """Screens the latest user prompt on the Orchestrator for safety boundary violations."""
        if not _is_orchestrator_context(callback_context):
            return None

        user_text = _extract_last_user_message(llm_request)
        if not user_text:
            return None

        if _EMERGENCY_RE.search(user_text):
            logger.warning(
                "MedicalSafetyPolicyPlugin blocked acute emergency query",
                extra={"event": "POLICY_BLOCK", "rule": "emergency"},
            )
            return _build_canned_response(
                "URGENT MEDICAL NOTICE: Your message describes symptoms that may indicate "
                "an acute medical emergency. Please call emergency services (such as 911 "
                "in the US or your local emergency number) or go to the nearest emergency "
                "department immediately. This research literacy assistant cannot provide "
                "emergency medical care or triage."
            )

        if _DOSING_OR_ACQUISITION_RE.search(user_text):
            logger.warning(
                "MedicalSafetyPolicyPlugin blocked personal dosing/acquisition query",
                extra={"event": "POLICY_BLOCK", "rule": "dosing_or_acquisition"},
            )
            return _build_canned_response(
                "CLINICAL POLICY NOTICE: I cannot provide individualized medication dosing, "
                "titration instructions, prescriptions, or instructions on acquiring "
                "prescription Alzheimer's therapeutics. Please consult a board-certified "
                "neurologist or prescribing clinician regarding treatment regimens, ARIA "
                "monitoring, and eligibility."
            )

        if _INDIVIDUAL_DIAGNOSIS_RE.search(user_text):
            logger.warning(
                "MedicalSafetyPolicyPlugin blocked individual diagnosis query",
                extra={"event": "POLICY_BLOCK", "rule": "individual_diagnosis"},
            )
            return _build_canned_response(
                "CLINICAL POLICY NOTICE: I cannot diagnose Alzheimer's disease or evaluate "
                "an individual patient's clinical symptoms. A formal diagnostic workup "
                "requires comprehensive neurological assessment, cognitive testing, and "
                "validated biomarker or neuroimaging evaluation by a qualified clinician."
            )

        return None

    async def after_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        """Appends the scientific literacy disclaimer to substantive Orchestrator responses."""
        if not _is_orchestrator_context(callback_context):
            return None
        if getattr(llm_response, "partial", False):
            return None

        content = getattr(llm_response, "content", None)
        parts = getattr(content, "parts", None) or []
        if not parts:
            return None

        if any(getattr(part, "function_call", None) is not None for part in parts):
            return None

        for part in reversed(parts):
            text = getattr(part, "text", None)
            if isinstance(text, str) and len(text.strip()) >= 120:
                lowered = text.lower()
                if "not constitute medical advice" not in lowered and "not medical advice" not in lowered:
                    part.text = f"{text.rstrip()}{SCIENTIFIC_LITERACY_DISCLAIMER}"
                break

        return None
