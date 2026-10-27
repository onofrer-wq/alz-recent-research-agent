"""Rubric #1 (Tool Docstrings), Rubric #2 (Descriptive Naming), Rubric #3 (JSON Schemas) & Rubric #4 (Guided Errors).

Deterministic evidence grading, critique loop termination (``exit_critique_loop``),
and Alzheimer's question scope/stakes classification tools.
"""

from __future__ import annotations

import re
from collections.abc import Generator
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ValidationError

from alz_research_agent.schemas import (
    Confidence,
    EvidenceGrade,
    EvidenceGradeInput,
    QuestionScope,
    SourceType,
    StudyType,
    TrialPhase,
    default_recency_floor,
)
from alz_research_agent.tools._errors import (
    ErrorType,
    tool_error,
    tool_success,
    validation_error,
)

if TYPE_CHECKING:
    from google.adk.tools.tool_context import ToolContext

_POPULATION_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "patients",
        "patient",
        "participants",
        "participant",
        "subjects",
        "subject",
        "adults",
        "adult",
        "individuals",
        "people",
        "humans",
        "human",
        "cohort",
        "group",
        "study",
        "disease",
        "alzheimers",
        "alzheimer",
        "ad",
    }
)

_PRECLINICAL_SPECIES_TOKENS: frozenset[str] = frozenset(
    {
        "mouse",
        "mice",
        "murine",
        "rat",
        "rats",
        "5xfad",
        "app",
        "ps1",
        "transgenic",
        "organoid",
        "organoids",
        "vitro",
        "culture",
        "cell",
        "cells",
        "drosophila",
        "zebrafish",
    }
)

_EARLY_STAGE_TOKENS: frozenset[str] = frozenset({"mci", "mild", "early", "prodromal", "preclinical", "asymptomatic"})
_LATE_STAGE_TOKENS: frozenset[str] = frozenset({"late", "advanced", "severe", "moderate-to-severe"})

_EMERGENCY_PATTERNS: tuple[str, ...] = (
    "chest pain",
    "stroke",
    "seizure",
    "unconscious",
    "suicid",
    "overdose",
    "call 911",
    "medical emergency",
    "stopped breathing",
    "sudden paralysis",
    "acute delirium",
)

_TREATMENT_PATTERNS: tuple[str, ...] = (
    "should i take",
    "should my mother take",
    "should my father take",
    "should my mom take",
    "should my dad take",
    "should she take",
    "should he take",
    "should we start",
    "should take",
    "what dose",
    "dosage",
    "prescribe",
    "prescription",
    "where to buy",
    "where can i get",
    "how to get",
    "start taking",
    "stop taking",
    "switch medication",
    "recommend a treatment",
    "which drug should",
    "is it safe for my",
    "can my mother take",
    "can my father take",
)

_ALZHEIMERS_DOMAIN_KEYWORDS: dict[str, str] = {
    "alzheimer": "alzheimers",
    "amyloid": "amyloid",
    "abeta": "amyloid",
    "plaque": "amyloid",
    "tau": "tau",
    "p-tau": "tau",
    "ptau": "tau",
    "neurofibrillary": "tau",
    "lecanemab": "anti_amyloid_immunotherapy",
    "donanemab": "anti_amyloid_immunotherapy",
    "aducanumab": "anti_amyloid_immunotherapy",
    "remternetug": "anti_amyloid_immunotherapy",
    "trontinemab": "anti_amyloid_immunotherapy",
    "biomarker": "biomarkers",
    "plasma": "biomarkers",
    "csf": "biomarkers",
    "pet": "neuroimaging",
    "mri": "neuroimaging",
    "microglia": "neuroinflammation",
    "trem2": "neuroinflammation",
    "neuroinflammation": "neuroinflammation",
    "apoe": "genetics",
    "apoe4": "genetics",
    "dementia": "dementia",
    "mci": "mild_cognitive_impairment",
    "cognitive": "cognition",
    "neurodegenerat": "neurodegeneration",
    "synapse": "synaptic_plasticity",
    "glp-1": "metabolic_therapeutics",
    "semaglutide": "metabolic_therapeutics",
}


class _AwaitableDict(dict[str, Any]):
    """Dictionary that can be used directly OR awaited in async test contexts."""

    def __await__(self) -> Generator[Any, None, dict[str, Any]]:
        async def _return_self() -> dict[str, Any]:
            return self

        return _return_self().__await__()


def _normalize_population_tokens(text: str) -> set[str]:
    """Tokenizes a population description into normalized, non-stopword tokens."""
    raw_tokens = re.findall(r"[a-z0-9]+", text.lower().replace("'", ""))
    filtered = {tok for tok in raw_tokens if tok not in _POPULATION_STOPWORDS and len(tok) > 1}
    return filtered or set(raw_tokens)


def _detect_extrapolation(studied: str, target: str) -> bool:
    """Determines whether applying findings from ``studied`` to ``target`` is an extrapolation."""
    studied_tokens = _normalize_population_tokens(studied)
    target_tokens = _normalize_population_tokens(target)

    if not studied_tokens or not target_tokens:
        return studied.strip().lower() != target.strip().lower()

    # Cross-species extrapolation (e.g., mice/organoids -> human patients)
    studied_preclinical = bool(studied_tokens & _PRECLINICAL_SPECIES_TOKENS)
    target_preclinical = bool(target_tokens & _PRECLINICAL_SPECIES_TOKENS)
    if studied_preclinical != target_preclinical:
        return True

    # Disease stage mismatch (e.g., MCI/early -> advanced/late-stage Alzheimer's)
    if (studied_tokens & _EARLY_STAGE_TOKENS) and (target_tokens & _LATE_STAGE_TOKENS):
        return True
    if (studied_tokens & _LATE_STAGE_TOKENS) and (target_tokens & _EARLY_STAGE_TOKENS):
        return True

    overlap = len(studied_tokens & target_tokens)
    union = len(studied_tokens | target_tokens)
    jaccard = overlap / union if union else 1.0
    return jaccard < 0.4


def _compute_confidence(inp: EvidenceGradeInput) -> Confidence:
    """Applies the deterministic rubric table to assign ``Confidence``."""
    n = inp.sample_size or 0

    if (
        inp.source_type in (SourceType.PREPRINT, SourceType.TRIAL_REGISTRY)
        or inp.study_type
        in (
            StudyType.PRECLINICAL,
            StudyType.CASE_SERIES,
            StudyType.TRIAL_REGISTRATION,
            StudyType.UNKNOWN,
        )
        or inp.trial_phase == TrialPhase.PHASE_1
    ):
        return Confidence.LOW

    if inp.study_type == StudyType.RANDOMIZED_CONTROLLED_TRIAL and inp.trial_phase == TrialPhase.PHASE_3:
        if n >= 200:
            return Confidence.HIGH
        if n >= 50:
            return Confidence.MODERATE
        return Confidence.LOW

    if inp.study_type == StudyType.META_ANALYSIS:
        if inp.sample_size is None or n >= 300:
            return Confidence.HIGH
        if n >= 100:
            return Confidence.MODERATE
        return Confidence.LOW

    if inp.trial_phase in (TrialPhase.PHASE_2, TrialPhase.PHASE_4):
        return Confidence.MODERATE if n >= 40 or inp.sample_size is None else Confidence.LOW

    if inp.study_type == StudyType.OBSERVATIONAL:
        return Confidence.MODERATE if n >= 150 else Confidence.LOW

    if inp.study_type == StudyType.REVIEW:
        return Confidence.MODERATE

    return Confidence.LOW


def grade_evidence_claim(
    claim: str,
    supporting_record_ids: list[str],
    study_type: str,
    studied_population: str,
    target_population: str,
    trial_phase: str = "not_applicable",
    sample_size: int | None = None,
    source_type: str = "peer_reviewed",
) -> dict[str, Any]:
    """Deterministically grades the methodological confidence and extrapolation status of a claim.

    Use this tool in the Critic agent to evaluate every scientific claim in the Consultant's
    draft before approving or requesting revisions. Do NOT guess confidence scores in free text;
    always call this tool with the exact study design, phase, sample size, and populations.

    Args:
        claim: Specific scientific claim being evaluated (5-1000 chars).
        supporting_record_ids: Non-empty list of PMIDs, NCT IDs, or Europe PMC IDs supporting
            the claim.
        study_type: Study design enum value (``randomized_controlled_trial``, ``observational``,
            ``meta_analysis``, ``preclinical``, ``case_series``, ``trial_registration``,
            ``review``, or ``unknown``).
        studied_population: Population or experimental model actually studied in the source
            (e.g., ``"early MCI patients"`` or ``"5xFAD mice"``).
        target_population: Population to which the claim is applied in the draft
            (e.g., ``"advanced Alzheimer patients"``).
        trial_phase: Clinical trial phase (``phase_1`` through ``phase_4``, ``not_applicable``,
            or ``unknown``).
        sample_size: Optional participant or sample count (N >= 0).
        source_type: Source vetting category (``peer_reviewed``, ``preprint``, or
            ``trial_registry``).

    Returns:
        On success, a dictionary with ``status="success"`` and all ``EvidenceGrade`` fields
        (``claim``, ``supporting_record_ids``, ``study_type``, ``trial_phase``, ``sample_size``,
        ``source_type``, ``confidence``, ``extrapolation``, and ``note``). On validation error,
        a guided error dictionary with ``status="error"``, ``error_type="INVALID_ARGUMENT"``,
        and imperative ``recovery_instructions``.
    """
    try:
        validated = EvidenceGradeInput(
            claim=claim,
            supporting_record_ids=supporting_record_ids,
            study_type=study_type,  # type: ignore[arg-type]
            studied_population=studied_population,
            target_population=target_population,
            trial_phase=trial_phase,  # type: ignore[arg-type]
            sample_size=sample_size,
            source_type=source_type,  # type: ignore[arg-type]
        )
    except ValidationError as exc:
        return validation_error(exc, source="grade_evidence_claim")

    confidence = _compute_confidence(validated)
    extrapolation = _detect_extrapolation(
        validated.studied_population,
        validated.target_population,
    )

    note_parts: list[str] = [
        f"Graded {confidence.value.upper()} confidence based on study_type={validated.study_type.value}, "
        f"trial_phase={validated.trial_phase.value}, sample_size={validated.sample_size}, "
        f"source_type={validated.source_type.value}."
    ]
    if extrapolation:
        note_parts.append(
            f"SPECULATIVE EXTRAPOLATION: Evidence from '{validated.studied_population}' is being "
            f"extrapolated to '{validated.target_population}'. This claim must be explicitly "
            "labeled as speculative in the final synthesis."
        )

    grade = EvidenceGrade(
        claim=validated.claim,
        supporting_record_ids=validated.supporting_record_ids,
        study_type=validated.study_type,
        trial_phase=validated.trial_phase,
        sample_size=validated.sample_size,
        source_type=validated.source_type,
        confidence=confidence,
        extrapolation=extrapolation,
        note=" ".join(note_parts)[:1000],
    )
    dumped = grade.model_dump(mode="json")
    return tool_success(dumped, grade=dumped)


def exit_critique_loop(
    reason: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Signals that the Consultant's draft passed methodological critique and exits the ``LoopAgent``.

    Use this tool ONLY when all claims in the Consultant's draft are backed by recent (< 3 years)
    citations, cutting-edge experimental findings are highlighted, and any optimistic
    extrapolations are explicitly labeled as speculative. Do NOT call this tool if ungraded
    claims or recency violations remain.

    Args:
        reason: Concise justification explaining why the draft meets all rubric criteria.
        tool_context: Optional ADK ``ToolContext`` whose ``actions.escalate`` and
            ``actions.skip_summarization`` flags are set to ``True`` and whose ``state``
            records ``critic_approved=True``.

    Returns:
        On success, a dictionary with ``status="success"``, ``critic_approved=True``,
        ``escalated=True``, ``skip_summarization=True``, and ``reason``. On invalid input,
        a guided error dictionary with ``status="error"``, ``error_type="INVALID_ARGUMENT"``,
        and ``recovery_instructions``.
    """
    if not isinstance(reason, str) or len(reason.strip()) < 3:
        return tool_error(
            ErrorType.INVALID_ARGUMENT,
            message="exit_critique_loop requires a non-empty reason (at least 3 characters).",
            recovery_instructions=("Pass a concise reason string summarizing why the Consultant draft is approved."),
            retryable=True,
            source="exit_critique_loop",
        )

    cleaned_reason = reason.strip()
    if tool_context is not None:
        actions = getattr(tool_context, "actions", None)
        if actions is not None:
            actions.escalate = True
            actions.skip_summarization = True
        state = getattr(tool_context, "state", None)
        if isinstance(state, dict):
            state["critic_approved"] = True
            state["critic_exit_reason"] = cleaned_reason
        elif state is not None:
            try:
                state["critic_approved"] = True
                state["critic_exit_reason"] = cleaned_reason
            except Exception:
                pass

    return tool_success(
        critic_approved=True,
        escalated=True,
        skip_summarization=True,
        reason=cleaned_reason,
    )


def _extract_requested_since_date(question: str, floor: date) -> tuple[date | None, date]:
    """Detects relative ('last N years') or explicit ('since 2024') dates and clamps to ``floor``."""
    lowered = question.lower()
    today = date.today()
    candidate: date | None = None

    iso_match = re.search(r"\b((?:19|20)\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b", lowered)
    if iso_match:
        try:
            candidate = date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        except ValueError:
            candidate = None

    if candidate is None:
        rel_match = re.search(r"\b(?:last|past)\s+(\d{1,2})\s+years?\b", lowered)
        if rel_match:
            years = max(1, int(rel_match.group(1)))
            try:
                candidate = date(today.year - years, today.month, today.day)
            except ValueError:
                candidate = date(today.year - years, today.month, 28)

    if candidate is None:
        year_match = re.search(r"\b(?:since|from|after|in)\s+((?:19|20)\d{2})\b", lowered)
        if year_match:
            candidate = date(int(year_match.group(1)), 1, 1)

    if candidate is None:
        return None, floor

    clamped = max(candidate, floor)
    if clamped > today:
        clamped = floor
    return clamped, clamped


def classify_alzheimers_question_scope(
    question: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Classifies whether a user question is in Alzheimer's scope, its stakes, and its recency date.

    Use this tool at the start of orchestration to determine whether the user query is
    ``in_scope``, whether it represents an ``informational``, ``treatment_related``, or
    ``emergency`` inquiry, and what clamped ``since_date_to_use`` should be passed to
    downstream literature search tools. Do NOT skip this classification before delegating
    to the research pipeline.

    Args:
        question: Raw or PII-redacted user question string.
        tool_context: Optional ADK ``ToolContext`` whose ``state`` is populated with
            ``user_question``, ``question_scope``, ``requires_human_review``, and
            ``since_date_to_use``.

    Returns:
        On success, a dictionary with ``status="success"``, ``in_scope``, ``topic_tags``,
        ``stakes`` (``"informational"``, ``"treatment_related"``, or ``"emergency"``),
        ``requested_since_date``, ``since_date_to_use``, ``requires_human_review``, and
        ``reason``. On invalid empty input, a guided error dictionary with
        ``status="error"``, ``error_type="INVALID_ARGUMENT"``, and ``recovery_instructions``.
    """
    if not isinstance(question, str) or len(question.strip()) < 3:
        return _AwaitableDict(
            tool_error(
                ErrorType.INVALID_ARGUMENT,
                message="Question must be a string of at least 3 characters.",
                recovery_instructions="Provide the user's scientific question text (at least 3 chars).",
                retryable=True,
                source="classify_alzheimers_question_scope",
            )
        )

    cleaned = question.strip()
    lowered = cleaned.lower()
    floor = default_recency_floor()

    topic_tags: list[str] = []
    for kw, tag in _ALZHEIMERS_DOMAIN_KEYWORDS.items():
        if kw in lowered and tag not in topic_tags:
            topic_tags.append(tag)

    is_emergency = any(pat in lowered for pat in _EMERGENCY_PATTERNS)
    is_treatment = any(pat in lowered for pat in _TREATMENT_PATTERNS)
    in_scope = bool(topic_tags) or is_emergency

    stakes: Literal["informational", "treatment_related", "emergency"]
    if is_emergency:
        stakes = "emergency"
        in_scope = True
        if "emergency_triage" not in topic_tags:
            topic_tags.append("emergency_triage")
        reason = (
            "Query contains acute medical emergency indicators; immediate clinical emergency "
            "guidance takes precedence over literature search."
        )
    elif is_treatment and in_scope:
        stakes = "treatment_related"
        if "treatment_decision" not in topic_tags:
            topic_tags.append("treatment_decision")
        reason = (
            "Query asks for treatment, dosing, or medication advice related to Alzheimer's "
            "disease; requires human-in-the-loop review before release."
        )
    elif in_scope:
        stakes = "informational"
        reason = (
            "Query is an informational scientific literacy question within the Alzheimer's "
            "disease and neurodegeneration research domain."
        )
    else:
        stakes = "informational"
        reason = "Query does not pertain to Alzheimer's disease, dementia, or neurodegenerative biomedical research."

    requested_since, since_date_to_use = _extract_requested_since_date(cleaned, floor)
    requires_human_review = stakes == "treatment_related"

    scope_model = QuestionScope(
        in_scope=in_scope,
        topic_tags=topic_tags,
        stakes=stakes,
        requested_since_date=requested_since,
        reason=reason,
    )
    scope_dict = scope_model.model_dump(mode="json")

    if tool_context is not None:
        state = getattr(tool_context, "state", None)
        if state is not None:
            try:
                state["user_question"] = cleaned
                state["question_scope"] = scope_dict
                state["requires_human_review"] = requires_human_review
                state["since_date_to_use"] = since_date_to_use.isoformat()
            except Exception:
                pass

    return _AwaitableDict(
        tool_success(
            scope_dict,
            question_scope=scope_dict,
            requires_human_review=requires_human_review,
            since_date_to_use=since_date_to_use.isoformat(),
        )
    )
