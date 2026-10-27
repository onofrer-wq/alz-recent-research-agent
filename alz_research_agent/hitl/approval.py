"""Rubric #12 (Human-in-the-Loop Hooks).

Dual-path Human-in-the-Loop (HITL) approval architecture governing the release of
any response that touches clinical treatment, dosing, or low-groundedness claims:

1. ``adk_confirmation`` mode (local / interactive ``adk web`` with ``InMemorySessionService``):
   ``build_release_tool("adk_confirmation")`` wraps ``release_final_answer_to_user``
   in ``FunctionTool(..., require_confirmation=needs_human_review)``. When
   ``needs_human_review`` evaluates to ``True``, ADK natively pauses the tool call
   and emits a confirmation request event before resuming execution.
2. ``state_gate`` mode (production with ``DatabaseSessionService`` or
   ``VertexAiSessionService``): Because ADK's native ``require_confirmation`` pause/resume
   mechanism is experimental and unsupported on persistent database or Vertex AI
   session services, ``build_release_tool("state_gate")`` sets
   ``require_confirmation=False`` and delegates enforcement to
   ``HumanApprovalGatePlugin`` + session state (``approval_status`` and
   ``record_human_approval_decision``). ``HumanApprovalGatePlugin`` verifies that
   the human user's message literally contains ``APPROVE`` or ``REJECT`` so the
   LLM can never self-approve a gated draft.
"""

from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, Any

from google.adk.plugins import BasePlugin
from google.adk.tools.function_tool import FunctionTool

from alz_research_agent.tools._errors import ErrorType, tool_error, tool_success

if TYPE_CHECKING:
    from google.adk.tools.base_tool import BaseTool
    from google.adk.tools.tool_context import ToolContext

SCIENTIFIC_LITERACY_DISCLAIMER = (
    "\n\n---\n*Scientific Literacy Disclaimer: This synthesis is provided strictly "
    "for scientific literacy and educational purposes based on Alzheimer's disease "
    "research from the last 3 years. It does NOT constitute medical advice, clinical "
    "diagnosis, or a treatment recommendation. Always consult a qualified physician "
    "or neurologist before making any clinical or medication decisions.*"
)

_TREATMENT_TEXT_RE = re.compile(
    r"(?:"
    r"\b\d+(?:\.\d+)?\s*(?:mg/kg|mg|mcg|g|ml)\b|"
    r"\btitrat(?:e|ion|ing)\b|"
    r"\bshould\s+(?:start|stop|begin|discontinue)\s+taking\b|"
    r"\bshould\s+take\b|"
    r"\boff-label\b|"
    r"\bprescrib(?:e|ing|ed)\b|"
    r"\brecommended\s+dos(?:e|age|ing)\b|"
    r"\bdosing\s+(?:schedule|regimen)\b|"
    r"\binfusion\s+schedule\b"
    r")",
    re.IGNORECASE,
)


def _extract_state_dict(tool_context: Any) -> dict[str, Any]:
    """Returns a mutable or readable dictionary view of ``tool_context.state``."""
    if tool_context is None:
        return {}
    state = getattr(tool_context, "state", None)
    if isinstance(state, dict):
        return state
    to_dict = getattr(state, "to_dict", None)
    if callable(to_dict):
        try:
            return dict(to_dict())
        except Exception:
            return {}
    return {}


def _set_state_value(tool_context: Any, key: str, value: Any) -> None:
    """Safely writes ``key=value`` into ``tool_context.state`` if available."""
    if tool_context is None:
        return
    state = getattr(tool_context, "state", None)
    if state is None:
        return
    try:
        state[key] = value
    except Exception:
        pass


def _parse_critic_verdict_requires_review(raw_verdict: Any) -> bool:
    """Checks whether ``critic_verdict`` (as dict, model, or JSON text) flags human review."""
    if raw_verdict is None:
        return False
    if isinstance(raw_verdict, dict):
        return bool(raw_verdict.get("requires_human_review", False))
    if hasattr(raw_verdict, "requires_human_review"):
        return bool(getattr(raw_verdict, "requires_human_review", False))
    if isinstance(raw_verdict, str):
        stripped = raw_verdict.strip()
        if not stripped:
            return False
        for candidate in (stripped, _extract_embedded_json_object(stripped)):
            if not candidate:
                continue
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return bool(parsed.get("requires_human_review", False))
            except Exception:
                continue
    return False


def _extract_embedded_json_object(text: str) -> str | None:
    """Extracts the first balanced ``{...}`` JSON object from markdown or prose."""
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return None


def _is_approved(tool_context: Any) -> bool:
    """Returns ``True`` if ADK ``tool_confirmation`` or session ``approval_status`` is approved."""
    if tool_context is None:
        return False
    confirmation = getattr(tool_context, "tool_confirmation", None)
    if confirmation is not None and bool(getattr(confirmation, "confirmed", False)):
        return True
    state = _extract_state_dict(tool_context)
    return str(state.get("approval_status", "")).strip().lower() == "approved"


def _ensure_disclaimer(answer_markdown: str) -> str:
    """Appends the constitutional scientific literacy disclaimer if not already present."""
    lowered = answer_markdown.lower()
    if "not constitute medical advice" in lowered or "not medical advice" in lowered:
        return answer_markdown
    return f"{answer_markdown.rstrip()}{SCIENTIFIC_LITERACY_DISCLAIMER}"


def needs_human_review(
    answer_markdown: str,
    tool_context: ToolContext | None = None,
) -> bool:
    """Determines whether a candidate answer requires human-in-the-loop approval before release.

    Evaluates three independent safety triggers:
    1. ``requires_human_review`` is ``True`` in ``tool_context.state``.
    2. ``critic_verdict`` in ``tool_context.state`` (dict or JSON string) has
       ``requires_human_review=True``.
    3. ``answer_markdown`` contains clinical dosing, titration, prescribing, off-label,
       or ``"should start/stop taking"`` directives.

    Args:
        answer_markdown: Candidate response text prepared for the end user.
        tool_context: Optional ADK ``ToolContext`` carrying session state.

    Returns:
        ``True`` if human review is required before releasing the answer, ``False`` otherwise.
    """
    state = _extract_state_dict(tool_context)
    if bool(state.get("requires_human_review", False)):
        return True
    if _parse_critic_verdict_requires_review(state.get("critic_verdict")):
        return True
    if isinstance(answer_markdown, str) and _TREATMENT_TEXT_RE.search(answer_markdown):
        return True
    return False


def release_final_answer_to_user(
    answer_markdown: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Releases the final synthesized answer to the user after verifying human approval gates.

    Use this tool in the Orchestrator agent as the final step before presenting a scientific
    synthesis to the user. If the response triggers ``needs_human_review`` and has not yet
    been approved by a human reviewer, this tool stores the draft in ``state["final_answer"]``,
    sets ``state["approval_status"] = "pending"``, and returns an ``APPROVAL_REQUIRED`` error
    so the user can reply with ``APPROVE`` or ``REJECT``.

    Args:
        answer_markdown: Markdown synthesis prepared for the end user.
        tool_context: Optional ADK ``ToolContext`` carrying session state and confirmation.

    Returns:
        On success, a dictionary with ``status="success"``, ``final_answer`` (including the
        mandatory constitutional disclaimer), ``approval_status="released"``, and
        ``human_reviewed`` boolean. When human approval is still required, a guided error
        dictionary with ``status="error"``, ``error_type="APPROVAL_REQUIRED"``, and
        ``recovery_instructions`` instructing that a human reviewer must approve and the user
        must respond ``APPROVE`` or ``REJECT``.
    """
    if not isinstance(answer_markdown, str) or not answer_markdown.strip():
        return tool_error(
            ErrorType.INVALID_ARGUMENT,
            message="answer_markdown must be a non-empty string.",
            recovery_instructions="Pass the complete synthesized markdown response to release_final_answer_to_user.",
            retryable=True,
            source="release_final_answer_to_user",
        )

    review_required = needs_human_review(answer_markdown, tool_context)
    approved = _is_approved(tool_context)

    if review_required and not approved:
        _set_state_value(tool_context, "final_answer", answer_markdown)
        _set_state_value(tool_context, "approval_status", "pending")
        _set_state_value(tool_context, "requires_human_review", True)
        return tool_error(
            ErrorType.APPROVAL_REQUIRED,
            message=(
                "This response touches clinical treatment, dosing, or flagged claims and "
                "requires explicit human-in-the-loop approval before release."
            ),
            recovery_instructions=(
                "A human reviewer must approve this draft before it can be released. "
                "Do NOT call record_human_approval_decision yourself; ask the user to "
                "reply literally with APPROVE or REJECT."
            ),
            retryable=False,
            source="release_final_answer_to_user",
            details={"approval_status": "pending", "requires_human_review": True},
        )

    final_with_disclaimer = _ensure_disclaimer(answer_markdown)
    _set_state_value(tool_context, "final_answer", final_with_disclaimer)
    _set_state_value(tool_context, "approval_status", "released")

    return tool_success(
        final_answer=final_with_disclaimer,
        approval_status="released",
        human_reviewed=bool(review_required and approved),
    )


def record_human_approval_decision(
    decision: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Records a human reviewer's explicit ``APPROVE`` or ``REJECT`` decision in session state.

    Use this tool ONLY after the human user has explicitly replied with ``APPROVE`` or
    ``REJECT`` to a pending treatment-related review gate. ``HumanApprovalGatePlugin``
    verifies the human user's raw message and blocks any attempt by the model to call
    this tool without a matching literal ``APPROVE`` or ``REJECT`` from the user.

    Args:
        decision: Human decision string (``"APPROVE"``, ``"approved"``, ``"REJECT"``,
            or ``"rejected"``).
        tool_context: Optional ADK ``ToolContext`` whose ``state["approval_status"]``
            is updated to ``"approved"`` or ``"rejected"``.

    Returns:
        On success, a dictionary with ``status="success"``, ``approval_status``
        (``"approved"`` or ``"rejected"``), and ``next_step`` instructions. On invalid
        input, a guided error dictionary with ``status="error"``,
        ``error_type="INVALID_ARGUMENT"``, and ``recovery_instructions``.
    """
    cleaned = str(decision or "").strip().lower()
    if cleaned in ("approve", "approved"):
        normalized = "approved"
        next_step = (
            "Human approval recorded. Now call release_final_answer_to_user with the "
            "pending final_answer from session state."
        )
    elif cleaned in ("reject", "rejected"):
        normalized = "rejected"
        next_step = (
            "Human reviewer rejected the draft. Do NOT release the treatment-related "
            "content; provide only a high-level non-clinical overview and advise "
            "consulting a qualified physician."
        )
    else:
        return tool_error(
            ErrorType.INVALID_ARGUMENT,
            message=f"Invalid approval decision '{decision}'. Expected APPROVE or REJECT.",
            recovery_instructions=("Pass 'APPROVE' or 'REJECT' matching the human reviewer's literal response."),
            retryable=True,
            source="record_human_approval_decision",
        )

    _set_state_value(tool_context, "approval_status", normalized)
    return tool_success(
        approval_status=normalized,
        decision=normalized,
        next_step=next_step,
    )


def _extract_user_content_text(tool_context: Any) -> str:
    """Extracts concatenated text from ``tool_context.user_content`` if available."""
    if tool_context is None:
        return ""
    user_content = getattr(tool_context, "user_content", None)
    if user_content is None:
        return ""
    parts = getattr(user_content, "parts", None) or []
    texts = [str(getattr(part, "text", "") or "") for part in parts if getattr(part, "text", None)]
    return " ".join(texts).strip()


class HumanApprovalGatePlugin(BasePlugin):
    """ADK ``BasePlugin`` enforcing state-based human approval gates on tool calls."""

    def __init__(self, name: str = "human_approval_gate_plugin") -> None:
        """Initializes the human approval gate plugin."""
        super().__init__(name=name)

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict[str, Any] | None:
        """Prevents model self-approval and blocks unapproved treatment releases.

        Args:
            tool: ADK tool about to be executed.
            tool_args: Arguments supplied to the tool call.
            tool_context: Active ADK ``ToolContext``.

        Returns:
            A guided error dictionary if the call violates the HITL gate, or ``None``
            to allow the tool execution to proceed.
        """
        tool_name = getattr(tool, "name", "")

        if tool_name == "record_human_approval_decision":
            user_text = _extract_user_content_text(tool_context)
            raw_decision = str(tool_args.get("decision", "")).strip().lower()
            wants_approve = raw_decision in ("approve", "approved")
            wants_reject = raw_decision in ("reject", "rejected")

            has_literal_approve = bool(re.search(r"\bAPPROVE\b", user_text))
            has_literal_reject = bool(re.search(r"\bREJECT\b", user_text))

            if wants_approve and has_literal_approve and not has_literal_reject:
                return None
            if wants_reject and has_literal_reject and not has_literal_approve:
                return None

            return tool_error(
                ErrorType.POLICY_BLOCKED,
                message=(
                    "HumanApprovalGatePlugin blocked record_human_approval_decision: the "
                    "latest human message does not contain a matching literal APPROVE or REJECT."
                ),
                recovery_instructions=(
                    "Do NOT call record_human_approval_decision until the human user replies "
                    "literally with APPROVE or REJECT."
                ),
                retryable=False,
                source="human_approval_gate_plugin",
            )

        if tool_name == "release_final_answer_to_user":
            answer_md = str(tool_args.get("answer_markdown", ""))
            if needs_human_review(answer_md, tool_context) and not _is_approved(tool_context):
                _set_state_value(tool_context, "final_answer", answer_md)
                _set_state_value(tool_context, "approval_status", "pending")
                _set_state_value(tool_context, "requires_human_review", True)
                return tool_error(
                    ErrorType.APPROVAL_REQUIRED,
                    message=(
                        "HumanApprovalGatePlugin blocked release_final_answer_to_user: "
                        "human approval is required before releasing treatment-related content."
                    ),
                    recovery_instructions=(
                        "A human reviewer must approve this draft before it can be released. "
                        "Ask the user to reply literally with APPROVE or REJECT."
                    ),
                    retryable=False,
                    source="human_approval_gate_plugin",
                    details={"approval_status": "pending", "requires_human_review": True},
                )

        return None


def build_release_tool(hitl_mode: str | None = None) -> FunctionTool:
    """Builds the ``FunctionTool`` for ``release_final_answer_to_user`` according to ``HITL_MODE``.

    Args:
        hitl_mode: Optional override for ``HITL_MODE`` (``"adk_confirmation"`` or
            ``"state_gate"``).

    Returns:
        Configured ADK ``FunctionTool`` with ``require_confirmation`` set to the
        ``needs_human_review`` predicate in ``adk_confirmation`` mode or ``False``
        in ``state_gate`` mode.
    """
    resolved_mode = (
        (hitl_mode if hitl_mode is not None else os.environ.get("HITL_MODE", "adk_confirmation")).strip().lower()
    )

    if resolved_mode == "adk_confirmation":
        return FunctionTool(
            release_final_answer_to_user,
            require_confirmation=needs_human_review,
        )
    return FunctionTool(
        release_final_answer_to_user,
        require_confirmation=False,
    )
