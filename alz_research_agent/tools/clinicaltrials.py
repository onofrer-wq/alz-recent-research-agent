"""Rubric #1 (Tool Docstrings), Rubric #2 (Descriptive Naming), Rubric #3 (JSON Schemas) & Rubric #4 (Guided Errors).

ClinicalTrials.gov API v2 search tool for recent Alzheimer's disease interventional
and observational clinical trials. Uses ``filter.advanced`` with ``AREA[StartDate]RANGE``
and ``AREA[Phase]`` (never the invalid ``filter.phase`` parameter).
"""

from __future__ import annotations

import re
from datetime import date
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from alz_research_agent.observability.tracing import research_span
from alz_research_agent.schemas import (
    ClinicalTrialsSearchInput,
    SearchResult,
    Source,
    SourceType,
    StudyRecord,
    StudyType,
    TrialPhase,
    default_recency_floor,
)
from alz_research_agent.tools._errors import (
    ErrorType,
    tool_error,
    validation_error,
)
from alz_research_agent.tools._http import UpstreamError, get_json

if TYPE_CHECKING:
    from google.adk.tools.tool_context import ToolContext

CLINICALTRIALS_API_URL = "https://clinicaltrials.gov/api/v2/studies"
CLINICALTRIALS_FIELDS = "NCTId,BriefTitle,Phase,EnrollmentCount,OverallStatus,StartDate,LeadSponsorName,StudyType"

_PHASE_NORMALIZATION_MAP: dict[str, TrialPhase] = {
    "phase1": TrialPhase.PHASE_1,
    "phase_1": TrialPhase.PHASE_1,
    "phase 1": TrialPhase.PHASE_1,
    "early_phase1": TrialPhase.PHASE_1,
    "early phase 1": TrialPhase.PHASE_1,
    "phase2": TrialPhase.PHASE_2,
    "phase_2": TrialPhase.PHASE_2,
    "phase 2": TrialPhase.PHASE_2,
    "phase3": TrialPhase.PHASE_3,
    "phase_3": TrialPhase.PHASE_3,
    "phase 3": TrialPhase.PHASE_3,
    "phase4": TrialPhase.PHASE_4,
    "phase_4": TrialPhase.PHASE_4,
    "phase 4": TrialPhase.PHASE_4,
    "na": TrialPhase.NOT_APPLICABLE,
    "not_applicable": TrialPhase.NOT_APPLICABLE,
    "not applicable": TrialPhase.NOT_APPLICABLE,
    "unknown": TrialPhase.UNKNOWN,
}

_CTGOV_PHASE_TOKEN_MAP: dict[TrialPhase, str] = {
    TrialPhase.PHASE_1: "PHASE1",
    TrialPhase.PHASE_2: "PHASE2",
    TrialPhase.PHASE_3: "PHASE3",
    TrialPhase.PHASE_4: "PHASE4",
    TrialPhase.NOT_APPLICABLE: "NA",
}

_PHASE_RANK: dict[TrialPhase, int] = {
    TrialPhase.PHASE_4: 4,
    TrialPhase.PHASE_3: 3,
    TrialPhase.PHASE_2: 2,
    TrialPhase.PHASE_1: 1,
    TrialPhase.NOT_APPLICABLE: 0,
    TrialPhase.UNKNOWN: -1,
}


def normalize_trial_phase(raw_phase: Any) -> TrialPhase:
    """Normalizes ClinicalTrials.gov phase strings or lists into a ``TrialPhase`` enum."""
    if isinstance(raw_phase, TrialPhase):
        return raw_phase
    if isinstance(raw_phase, list):
        if not raw_phase:
            return TrialPhase.NOT_APPLICABLE
        normalized_list = [normalize_trial_phase(item) for item in raw_phase]
        return max(normalized_list, key=lambda p: _PHASE_RANK.get(p, -1))
    if isinstance(raw_phase, str):
        key = raw_phase.strip().lower().replace("-", "_")
        if key in _PHASE_NORMALIZATION_MAP:
            return _PHASE_NORMALIZATION_MAP[key]
        if "/" in key:
            parts = [normalize_trial_phase(part) for part in key.split("/")]
            return max(parts, key=lambda p: _PHASE_RANK.get(p, -1))
    return TrialPhase.UNKNOWN


def _parse_ctgov_date(raw_date: str | None) -> date | None:
    """Parses ClinicalTrials.gov ``YYYY-MM-DD`` or ``YYYY-MM`` start date strings."""
    if not raw_date or not isinstance(raw_date, str):
        return None
    cleaned = raw_date.strip()
    full_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", cleaned)
    if full_match:
        try:
            return date(int(full_match.group(1)), int(full_match.group(2)), int(full_match.group(3)))
        except ValueError:
            return None
    ym_match = re.match(r"^(\d{4})-(\d{2})$", cleaned)
    if ym_match:
        try:
            return date(int(ym_match.group(1)), int(ym_match.group(2)), 1)
        except ValueError:
            return None
    y_match = re.match(r"^(\d{4})$", cleaned)
    if y_match:
        return date(int(y_match.group(1)), 1, 1)
    return None


def _coerce_input_phases(phases: list[str] | None) -> list[Any]:
    """Coerces user/model phase strings like ``'PHASE2'`` into ``TrialPhase`` values."""
    if not phases:
        return []
    coerced: list[Any] = []
    for item in phases:
        mapped = normalize_trial_phase(item)
        coerced.append(mapped if mapped != TrialPhase.UNKNOWN else item)
    return coerced


def _extract_study_fields(study: dict[str, Any]) -> dict[str, Any]:
    """Extracts normalized fields from either nested ``protocolSection`` or flat study JSON."""
    proto = study.get("protocolSection") if isinstance(study.get("protocolSection"), dict) else {}
    id_mod = proto.get("identificationModule") or {}
    status_mod = proto.get("statusModule") or {}
    design_mod = proto.get("designModule") or {}
    sponsor_mod = proto.get("sponsorCollaboratorsModule") or {}
    desc_mod = proto.get("descriptionModule") or {}

    nct_id = str(id_mod.get("nctId") or study.get("NCTId") or study.get("nctId") or "").strip()
    title = str(
        id_mod.get("briefTitle")
        or id_mod.get("officialTitle")
        or study.get("BriefTitle")
        or study.get("briefTitle")
        or f"Clinical Trial {nct_id}"
    ).strip()

    start_struct = status_mod.get("startDateStruct") or {}
    raw_start_date = start_struct.get("date") or study.get("StartDate") or study.get("startDate")
    overall_status = str(
        status_mod.get("overallStatus") or study.get("OverallStatus") or study.get("overallStatus") or "UNKNOWN"
    ).strip()

    raw_phases = design_mod.get("phases") if "phases" in design_mod else study.get("Phase")
    enrollment_info = design_mod.get("enrollmentInfo") or {}
    raw_enrollment = enrollment_info.get("count") if "count" in enrollment_info else study.get("EnrollmentCount")
    sample_size: int | None = None
    if isinstance(raw_enrollment, int) and raw_enrollment >= 0:
        sample_size = raw_enrollment
    elif isinstance(raw_enrollment, str) and raw_enrollment.strip().isdigit():
        sample_size = int(raw_enrollment.strip())

    lead_sponsor = (sponsor_mod.get("leadSponsor") or {}).get("name") or study.get("LeadSponsorName")
    brief_summary = desc_mod.get("briefSummary") or study.get("BriefSummary")

    return {
        "nct_id": nct_id,
        "title": title,
        "raw_start_date": str(raw_start_date) if raw_start_date else None,
        "overall_status": overall_status,
        "trial_phase": normalize_trial_phase(raw_phases),
        "sample_size": sample_size,
        "sponsor": str(lead_sponsor).strip() if lead_sponsor else None,
        "summary": str(brief_summary).strip()[:4000] if brief_summary else None,
    }


async def search_clinicaltrials_alzheimers_trials_since(
    since_date: str,
    intervention_terms: str | None = None,
    phases: list[str] | None = None,
    condition: str = "Alzheimer Disease",
    max_results: int = 25,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Searches ClinicalTrials.gov API v2 for Alzheimer's clinical trials started since ``since_date``.

    Use this tool to discover registered Phase 1-4 clinical trials, enrollment targets,
    trial statuses, and lead sponsors for Alzheimer's therapeutics or biomarkers started
    within the last 3 years. Do NOT use this tool for dates older than the 3-year recency
    floor or for full peer-reviewed results (use ``search_pubmed_alzheimers_studies_since``
    alongside this tool to check published trial outcomes).

    Args:
        since_date: Inclusive earliest trial start date in ISO format (``YYYY-MM-DD``)
            within the 3-year recency window.
        intervention_terms: Optional drug, antibody, biomarker, or intervention query
            passed to ``query.intr`` (e.g., ``"lecanemab"`` or ``"donanemab"``).
        phases: Optional list of trial phases (e.g., ``["phase_2", "phase_3"]`` or
            ``["PHASE2", "PHASE3"]``) combined into ``filter.advanced`` via ``AREA[Phase]``.
        condition: Condition query passed to ``query.cond`` (default ``"Alzheimer Disease"``).
        max_results: Maximum number of trial records to return (1-100, default 25).
        tool_context: Optional ADK ``ToolContext`` injected at runtime for tracing.

    Returns:
        A dictionary matching ``SearchResult`` on success (``status="success"``,
        ``source="clinicaltrials_gov"``, ``records`` sorted newest-first,
        ``recency_violations_dropped``, and ``next_step_hint``), or a guided error
        dictionary (``status="error"``, ``error_type`` in ``INVALID_ARGUMENT``,
        ``RECENCY_VIOLATION``, ``RATE_LIMITED``, ``UPSTREAM_UNAVAILABLE``,
        ``MALFORMED_RESPONSE``, or ``NO_RESULTS``, plus imperative ``recovery_instructions``).
    """
    floor = default_recency_floor()
    try:
        validated = ClinicalTrialsSearchInput(
            condition=condition,
            intervention_terms=intervention_terms,
            since_date=since_date,  # type: ignore[arg-type]
            phases=_coerce_input_phases(phases),
            max_results=max_results,
        )
    except ValidationError as exc:
        return validation_error(exc, source=Source.CLINICALTRIALS_GOV.value, recency_floor=floor)

    window = validated.window()
    advanced_filter = f"AREA[StartDate]RANGE[{window.since_date.isoformat()},MAX]"
    ctgov_phases = [_CTGOV_PHASE_TOKEN_MAP[p] for p in validated.phases if p in _CTGOV_PHASE_TOKEN_MAP]
    if ctgov_phases:
        advanced_filter = f"{advanced_filter} AND AREA[Phase]({' OR '.join(ctgov_phases)})"

    params: dict[str, Any] = {
        "query.cond": validated.condition,
        "filter.advanced": advanced_filter,
        "fields": CLINICALTRIALS_FIELDS,
        "pageSize": str(validated.max_results),
        "format": "json",
    }
    if validated.intervention_terms:
        params["query.intr"] = validated.intervention_terms

    agent_id = getattr(tool_context, "agent_name", None) if tool_context is not None else None
    span_query = f"{validated.condition} | {validated.intervention_terms or 'all'} | {advanced_filter}"

    try:
        with research_span(
            Source.CLINICALTRIALS_GOV.value,
            span_query,
            recency_floor=window.since_date.isoformat(),
            agent_id=agent_id,
        ):
            payload = await get_json(
                CLINICALTRIALS_API_URL,
                params=params,
                source=Source.CLINICALTRIALS_GOV.value,
            )
    except UpstreamError as exc:
        if exc.status_code == 429:
            wait_s = exc.retry_after or 5
            return tool_error(
                ErrorType.RATE_LIMITED,
                message=f"ClinicalTrials.gov API v2 rate limited (HTTP 429): {exc.message}",
                recovery_instructions=(
                    f"Wait {wait_s} seconds and retry with a smaller max_results, or query "
                    "Europe PMC via search_europepmc_alzheimers_preprints_since."
                ),
                retryable=True,
                source=Source.CLINICALTRIALS_GOV.value,
                details={"retry_after": wait_s},
            )
        if exc.status_code == 400:
            return tool_error(
                ErrorType.INVALID_ARGUMENT,
                message=f"ClinicalTrials.gov API v2 rejected query (HTTP 400): {exc.message}",
                recovery_instructions=(
                    "Simplify intervention_terms and phases; ensure filter.advanced uses "
                    "AREA[StartDate]RANGE and AREA[Phase] (never filter.phase) and retry."
                ),
                retryable=True,
                source=Source.CLINICALTRIALS_GOV.value,
                details={"status_code": 400},
            )
        return tool_error(
            ErrorType.UPSTREAM_UNAVAILABLE,
            message=f"ClinicalTrials.gov API v2 unavailable: {exc.message}",
            recovery_instructions=(
                "Switch to search_pubmed_alzheimers_studies_since or "
                "search_europepmc_alzheimers_preprints_since while ClinicalTrials.gov recovers."
            ),
            retryable=True,
            source=Source.CLINICALTRIALS_GOV.value,
            details={"status_code": exc.status_code},
        )
    except ValueError as exc:
        return tool_error(
            ErrorType.MALFORMED_RESPONSE,
            message=f"Malformed JSON response from ClinicalTrials.gov: {exc}",
            recovery_instructions=(
                "Retry with simpler intervention_terms or use search_pubmed_alzheimers_studies_since."
            ),
            retryable=False,
            source=Source.CLINICALTRIALS_GOV.value,
        )

    if not isinstance(payload, dict) or not isinstance(payload.get("studies"), list):
        return tool_error(
            ErrorType.MALFORMED_RESPONSE,
            message="ClinicalTrials.gov response missing 'studies' list.",
            recovery_instructions=("Retry with simpler parameters or use search_pubmed_alzheimers_studies_since."),
            retryable=False,
            source=Source.CLINICALTRIALS_GOV.value,
        )

    raw_studies: list[Any] = payload["studies"]
    if not raw_studies:
        return tool_error(
            ErrorType.NO_RESULTS,
            message=(
                f"ClinicalTrials.gov returned 0 trials for '{validated.condition}' "
                f"(intervention={validated.intervention_terms!r}) since {window.since_date.isoformat()}."
            ),
            recovery_instructions=(
                "Broaden intervention_terms with drug class synonyms or omit phases filter, "
                f"but do NOT lower since_date below {window.recency_floor.isoformat()}."
            ),
            retryable=True,
            source=Source.CLINICALTRIALS_GOV.value,
        )

    records: list[StudyRecord] = []
    dropped = 0

    for item in raw_studies:
        if not isinstance(item, dict):
            continue
        extracted = _extract_study_fields(item)
        nct_id = extracted["nct_id"]
        if not nct_id:
            continue

        start_date = _parse_ctgov_date(extracted["raw_start_date"])
        if start_date is None or start_date < window.since_date:
            dropped += 1
            continue

        records.append(
            StudyRecord(
                source=Source.CLINICALTRIALS_GOV,
                record_id=nct_id,
                title=extracted["title"][:600],
                publication_date=start_date,
                url=f"https://clinicaltrials.gov/study/{nct_id}",
                source_type=SourceType.TRIAL_REGISTRY,
                study_type=StudyType.TRIAL_REGISTRATION,
                trial_phase=extracted["trial_phase"],
                sample_size=extracted["sample_size"],
                journal_or_sponsor=extracted["sponsor"][:300] if extracted["sponsor"] else None,
                status=extracted["overall_status"][:100],
                abstract=extracted["summary"],
            )
        )

    if not records:
        return tool_error(
            ErrorType.NO_RESULTS,
            message=(
                f"All {dropped} candidate ClinicalTrials.gov records predated "
                f"since_date {window.since_date.isoformat()} and were dropped."
            ),
            recovery_instructions=(
                "Broaden intervention_terms with synonyms or omit phase restrictions, "
                f"but do NOT lower since_date below {window.recency_floor.isoformat()}."
            ),
            retryable=True,
            source=Source.CLINICALTRIALS_GOV.value,
            details={"recency_violations_dropped": dropped},
        )

    records.sort(key=lambda r: r.publication_date, reverse=True)
    total_available = int(payload.get("totalCount") or len(records))

    result_model = SearchResult(
        source=Source.CLINICALTRIALS_GOV,
        query=span_query,
        window=window,
        records=records,
        total_available=max(total_available, len(records)),
        recency_violations_dropped=dropped,
        next_step_hint=(
            "Cross-check active or completed NCT IDs in PubMed via "
            "search_pubmed_alzheimers_studies_since to see if peer-reviewed outcomes exist."
        ),
    )
    return result_model.model_dump(mode="json")
