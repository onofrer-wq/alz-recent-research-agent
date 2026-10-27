"""Rubric #11 (Guardrails & Policy Plugins), Rubric #17 (Automated Eval) & Rubric #10 (Model Routing).

Self-evaluation judge plugin (``SelfEvaluationJudgePlugin``) that executes after
the Consultant and Critic agents:
1. After the Consultant finishes, computes deterministic citation groundedness
   (fraction of cited PMIDs / NCT IDs / Europe PMC IDs that exist in the
   session's research dossier) and, when enabled, queries the ``JUDGE_MODEL`` tier
   via ``google-genai`` for a 0.0-1.0 groundedness score. Stores the minimum score
   in ``state["self_eval_groundedness"]`` and sets ``requires_human_review=True``
   whenever groundedness falls below ``0.7``.
2. After the Critic finishes, parses and validates ``state["critic_verdict"]``
   against the strict ``CriticVerdict`` Pydantic schema (accepting dicts, JSON strings,
   or JSON embedded in markdown prose), writes the normalized dict back to state,
   and sets ``requires_human_review=True`` if the verdict is invalid or requests
   human review. Never raises or breaks the pipeline if an error occurs.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any

from google.adk.plugins import BasePlugin
from pydantic import ValidationError

from alz_research_agent.schemas import CriticVerdict

if TYPE_CHECKING:
    from google.adk.agents.base_agent import BaseAgent
    from google.adk.agents.callback_context import CallbackContext
    from google.genai import types

logger = logging.getLogger("alz_research_agent.audit")

_RECORD_ID_RE = re.compile(r"\b(?:NCT\d{8}|PPR\d+|PMC\d+|\d{7,8})\b")


def _extract_state_dict(callback_context: Any) -> dict[str, Any]:
    """Returns a readable dictionary snapshot of ``callback_context.state``."""
    if callback_context is None:
        return {}
    state = getattr(callback_context, "state", None)
    if isinstance(state, dict):
        return state
    to_dict = getattr(state, "to_dict", None)
    if callable(to_dict):
        try:
            return dict(to_dict())
        except Exception:
            return {}
    return {}


def _set_state_value(callback_context: Any, key: str, value: Any) -> None:
    """Safely writes ``key=value`` into ``callback_context.state``."""
    if callback_context is None:
        return
    state = getattr(callback_context, "state", None)
    if state is None:
        return
    try:
        state[key] = value
    except Exception:
        pass


def _collect_known_record_ids(dossier_or_state: Any) -> set[str]:
    """Collects all known ``record_id`` strings from a dossier or session state."""
    known: set[str] = set()
    if dossier_or_state is None:
        return known

    if hasattr(dossier_or_state, "model_dump"):
        dossier_or_state = dossier_or_state.model_dump(mode="json")

    if isinstance(dossier_or_state, dict):
        for nested_key in ("research_dossier", "dossier"):
            if nested_key in dossier_or_state:
                known.update(_collect_known_record_ids(dossier_or_state[nested_key]))

        records = dossier_or_state.get("records")
        if isinstance(records, list):
            for rec in records:
                if isinstance(rec, dict) and rec.get("record_id"):
                    known.add(str(rec["record_id"]).strip())
                elif hasattr(rec, "record_id"):
                    known.add(str(rec.record_id).strip())

        known_ids_list = dossier_or_state.get("known_record_ids")
        if isinstance(known_ids_list, list):
            for item in known_ids_list:
                if item:
                    known.add(str(item).strip())
    elif isinstance(dossier_or_state, str):
        for match in _RECORD_ID_RE.findall(dossier_or_state):
            known.add(match)

    return known


def _collect_cited_record_ids(draft_text_or_dict: Any) -> set[str]:
    """Extracts all cited record IDs from a Consultant draft string, dict, or model."""
    cited: set[str] = set()
    if draft_text_or_dict is None:
        return cited

    if hasattr(draft_text_or_dict, "model_dump"):
        draft_text_or_dict = draft_text_or_dict.model_dump(mode="json")

    if isinstance(draft_text_or_dict, dict):
        for claim in draft_text_or_dict.get("claims") or []:
            if isinstance(claim, dict):
                for rid in claim.get("supporting_record_ids") or []:
                    if rid:
                        cited.add(str(rid).strip())
        for ref in draft_text_or_dict.get("references") or []:
            for match in _RECORD_ID_RE.findall(str(ref)):
                cited.add(match)
        if not cited:
            serialized = json.dumps(draft_text_or_dict, default=str)
            for match in _RECORD_ID_RE.findall(serialized):
                cited.add(match)
    elif isinstance(draft_text_or_dict, str):
        for match in _RECORD_ID_RE.findall(draft_text_or_dict):
            cited.add(match)

    return cited


def compute_citation_groundedness(
    draft_text_or_dict: Any,
    dossier_or_state: Any,
) -> float:
    """Computes the fraction of record IDs cited in ``draft_text_or_dict`` present in the dossier.

    Args:
        draft_text_or_dict: Consultant draft (markdown string, dict, or ``ConsultantDraft``).
        dossier_or_state: Research dossier (dict, ``ResearchDossier``, or session state dict).

    Returns:
        Groundedness ratio in ``[0.0, 1.0]``. Returns ``0.0`` when no IDs are cited or none
        of the cited IDs match the dossier.
    """
    cited = _collect_cited_record_ids(draft_text_or_dict)
    if not cited:
        return 0.0
    known = _collect_known_record_ids(dossier_or_state)
    if not known:
        return 0.0
    matched = cited & known
    return round(len(matched) / len(cited), 4)


def parse_and_validate_critic_verdict(raw_verdict: Any) -> CriticVerdict | None:
    """Parses and validates a ``CriticVerdict`` from a dict, model, or (possibly embedded) JSON text."""
    if raw_verdict is None:
        return None
    if isinstance(raw_verdict, CriticVerdict):
        return raw_verdict
    if isinstance(raw_verdict, dict):
        try:
            return CriticVerdict.model_validate(raw_verdict)
        except ValidationError:
            return None
    if isinstance(raw_verdict, str):
        stripped = raw_verdict.strip()
        if not stripped:
            return None
        candidates = [stripped]
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(stripped[start : end + 1])
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return CriticVerdict.model_validate(parsed)
            except Exception:
                continue
    return None


class SelfEvaluationJudgePlugin(BasePlugin):
    """ADK ``BasePlugin`` performing automated post-consultant groundedness and post-critic validation."""

    def __init__(
        self,
        *,
        name: str = "self_evaluation_judge_plugin",
        enable_llm_judge: bool = False,
        judge_model: str | None = None,
        judge_client: Any = None,
        groundedness_threshold: float = 0.7,
    ) -> None:
        """Initializes the self-evaluation judge plugin.

        Args:
            name: Unique plugin identifier.
            enable_llm_judge: Whether to invoke the ``JUDGE_MODEL`` tier via ``google-genai``
                in addition to deterministic citation groundedness.
            judge_model: Optional override for ``JUDGE_MODEL`` environment variable.
            judge_client: Optional injected GenAI client for offline testing.
            groundedness_threshold: Minimum acceptable groundedness score (default ``0.7``).
        """
        super().__init__(name=name)
        self.enable_llm_judge = enable_llm_judge
        self._judge_model = judge_model
        self._judge_client = judge_client
        self.groundedness_threshold = groundedness_threshold

    def _query_llm_judge(self, draft: Any, dossier: Any) -> float | None:
        """Calls the ``JUDGE_MODEL`` tier to score draft groundedness in ``[0.0, 1.0]``."""
        if not self.enable_llm_judge:
            return None
        model_name = self._judge_model or os.environ.get("JUDGE_MODEL", "").strip()
        if not model_name:
            return None

        try:
            client = self._judge_client
            if client is None:
                from google import genai

                client = genai.Client()

            prompt = (
                "You are a biomedical groundedness judge. Score how well the DRAFT is "
                "supported by the DOSSIER on a scale from 0.0 to 1.0. Respond with JSON "
                'only: {"groundedness": <float>}.\n\n'
                f"DOSSIER:\n{json.dumps(dossier, default=str)[:3000]}\n\n"
                f"DRAFT:\n{json.dumps(draft, default=str)[:3000]}"
            )
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            raw_text = str(getattr(response, "text", "") or "").strip()
            start = raw_text.find("{")
            end = raw_text.rfind("}")
            if start != -1 and end != -1 and end > start:
                raw_text = raw_text[start : end + 1]
            parsed = json.loads(raw_text)
            score = float(parsed["groundedness"])
            return max(0.0, min(1.0, score))
        except Exception as exc:
            logger.warning(
                "SelfEvaluationJudgePlugin LLM judge failed (%s); using cheap groundedness only.",
                type(exc).__name__,
                extra={"event": "SELF_EVAL_JUDGE_WARNING", "error_type": type(exc).__name__},
            )
            return None

    async def after_agent_callback(
        self,
        *,
        agent: BaseAgent,
        callback_context: CallbackContext,
    ) -> types.Content | None:
        """Evaluates consultant groundedness and critic verdict validity without raising."""
        try:
            agent_name = str(
                getattr(agent, "name", None) or getattr(callback_context, "agent_name", None) or ""
            ).lower()
            state = _extract_state_dict(callback_context)

            if "consultant" in agent_name:
                draft = state.get("consultant_draft") or state.get("draft_answer") or ""
                cheap_score = compute_citation_groundedness(draft, state)
                llm_score = self._query_llm_judge(draft, state.get("research_dossier"))
                final_score = min(cheap_score, llm_score) if llm_score is not None else cheap_score
                _set_state_value(callback_context, "self_eval_groundedness", final_score)
                if final_score < self.groundedness_threshold:
                    _set_state_value(callback_context, "requires_human_review", True)
                logger.info(
                    "Consultant self-eval groundedness=%.4f",
                    final_score,
                    extra={
                        "event": "SELF_EVAL_GROUNDEDNESS",
                        "groundedness": final_score,
                        "requires_human_review": final_score < self.groundedness_threshold,
                    },
                )

            elif "critic" in agent_name:
                raw_verdict = state.get("critic_verdict")
                validated = parse_and_validate_critic_verdict(raw_verdict)
                if validated is None:
                    _set_state_value(callback_context, "critic_verdict_valid", False)
                    _set_state_value(callback_context, "requires_human_review", True)
                    logger.warning(
                        "Critic output failed CriticVerdict schema validation; flagging human review.",
                        extra={"event": "SELF_EVAL_CRITIC_INVALID"},
                    )
                else:
                    normalized = validated.model_dump(mode="json")
                    _set_state_value(callback_context, "critic_verdict", normalized)
                    _set_state_value(callback_context, "critic_verdict_valid", True)
                    if validated.requires_human_review:
                        _set_state_value(callback_context, "requires_human_review", True)
        except Exception as exc:
            logger.warning(
                "SelfEvaluationJudgePlugin encountered unexpected error: %s",
                type(exc).__name__,
                extra={"event": "SELF_EVAL_PLUGIN_ERROR", "error_type": type(exc).__name__},
            )
        return None
