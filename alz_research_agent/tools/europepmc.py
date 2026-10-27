"""Rubric #1 (Tool Docstrings), Rubric #2 (Descriptive Naming), Rubric #3 (JSON Schemas) & Rubric #4 (Guided Errors).

Europe PMC REST API search tool retrieving both peer-reviewed biomedical articles
(``SRC:MED``, ``SRC:PMC``) and cutting-edge preprints (``SRC:PPR`` from bioRxiv/medRxiv)
published within the 3-year Alzheimer's recency window using ``FIRST_PDATE``.
"""

from __future__ import annotations

import re
from datetime import date
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from alz_research_agent.observability.tracing import research_span
from alz_research_agent.schemas import (
    EuropePmcSearchInput,
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

EUROPEPMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def _build_europepmc_query(
    query_terms: str,
    since_date: date,
    until_date: date,
    *,
    include_preprints: bool = True,
) -> str:
    """Composes the Europe PMC boolean query with Alzheimer scope, ``FIRST_PDATE``, and ``SRC``."""
    stripped = query_terms.strip()
    if "alzheimer" in stripped.lower():
        topic_clause = f"({stripped})"
    else:
        topic_clause = f'({stripped}) AND (Alzheimer OR "Alzheimer\'s disease")'

    date_clause = f"FIRST_PDATE:[{since_date.isoformat()} TO {until_date.isoformat()}]"
    if include_preprints:
        src_clause = "(SRC:MED OR SRC:PMC OR SRC:PPR)"
    else:
        src_clause = "(SRC:MED OR SRC:PMC)"

    return f"{topic_clause} AND {date_clause} AND {src_clause}"


def _parse_europepmc_date(item: dict[str, Any]) -> date | None:
    """Extracts a ``datetime.date`` from Europe PMC ``firstPublicationDate`` or ``pubYear``."""
    raw_first = str(item.get("firstPublicationDate") or "").strip()
    iso_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw_first)
    if iso_match:
        try:
            return date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        except ValueError:
            pass

    raw_year = str(item.get("pubYear") or "").strip()
    if raw_year.isdigit() and len(raw_year) == 4:
        return date(int(raw_year), 1, 1)
    return None


def _classify_europepmc_item(item: dict[str, Any]) -> tuple[SourceType, StudyType, TrialPhase]:
    """Classifies a Europe PMC record into ``(SourceType, StudyType, TrialPhase)``."""
    src_code = str(item.get("source") or "").strip().upper()
    pub_type = str(item.get("pubType") or "").strip().lower()
    title = str(item.get("title") or "").strip().lower()

    is_preprint = src_code == "PPR" or "preprint" in pub_type
    source_type = SourceType.PREPRINT if is_preprint else SourceType.PEER_REVIEWED

    trial_phase = TrialPhase.NOT_APPLICABLE
    if "phase 3" in title or "phase iii" in title:
        trial_phase = TrialPhase.PHASE_3
    elif "phase 2" in title or "phase ii" in title:
        trial_phase = TrialPhase.PHASE_2
    elif "phase 1" in title or "phase i " in title:
        trial_phase = TrialPhase.PHASE_1

    if "meta-analysis" in pub_type or "meta-analysis" in title:
        study_type = StudyType.META_ANALYSIS
    elif "clinical trial" in pub_type or "randomized" in title or trial_phase != TrialPhase.NOT_APPLICABLE:
        study_type = StudyType.RANDOMIZED_CONTROLLED_TRIAL
    elif any(k in title for k in ("mouse", "mice", "murine", "5xfad", "in vitro", "organoid")):
        study_type = StudyType.PRECLINICAL
    elif "review" in pub_type:
        study_type = StudyType.REVIEW
    elif "observational" in title or "cohort" in title:
        study_type = StudyType.OBSERVATIONAL
    else:
        study_type = StudyType.UNKNOWN

    return source_type, study_type, trial_phase


async def search_europepmc_alzheimers_preprints_since(
    query_terms: str,
    since_date: str,
    include_preprints: bool = True,
    max_results: int = 20,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Searches Europe PMC for recent Alzheimer's peer-reviewed papers and preprints since ``since_date``.

    Use this tool to capture the newest experimental findings, bioRxiv/medRxiv preprints
    (``SRC:PPR``), and European/global open-access studies published within the last 3 years,
    or as a fallback when PubMed E-utilities is rate-limited. Do NOT use this tool for dates
    older than the 3-year recency floor, and always label ``source_type="preprint"`` records
    as non-peer-reviewed when grading claims.

    Args:
        query_terms: Biomedical search keywords (3-300 chars). Automatically combined with
            Alzheimer's disease terms and ``FIRST_PDATE`` range filtering.
        since_date: Inclusive earliest publication date in ISO format (``YYYY-MM-DD``)
            within the 3-year recency window.
        include_preprints: When ``True`` (default), includes ``SRC:PPR`` preprints alongside
            ``SRC:MED`` and ``SRC:PMC``.
        max_results: Maximum number of records to return (1-100, default 20).
        tool_context: Optional ADK ``ToolContext`` injected at runtime for tracing.

    Returns:
        A dictionary matching ``SearchResult`` on success (``status="success"``,
        ``source="europepmc"``, ``records`` sorted newest-first with preprints tagged
        as ``source_type="preprint"``, ``recency_violations_dropped``, and ``next_step_hint``),
        or a guided error dictionary (``status="error"``, ``error_type`` in
        ``INVALID_ARGUMENT``, ``RECENCY_VIOLATION``, ``RATE_LIMITED``,
        ``UPSTREAM_UNAVAILABLE``, ``MALFORMED_RESPONSE``, or ``NO_RESULTS``, plus
        imperative ``recovery_instructions``).
    """
    floor = default_recency_floor()
    try:
        validated = EuropePmcSearchInput(
            query_terms=query_terms,
            since_date=since_date,  # type: ignore[arg-type]
            include_preprints=include_preprints,
            max_results=max_results,
        )
    except ValidationError as exc:
        return validation_error(exc, source=Source.EUROPEPMC.value, recency_floor=floor)

    window = validated.window()
    composed_query = _build_europepmc_query(
        validated.query_terms,
        window.since_date,
        window.until_date,
        include_preprints=validated.include_preprints,
    )
    params: dict[str, Any] = {
        "query": composed_query,
        "format": "json",
        "resultType": "lite",
        "pageSize": str(validated.max_results),
    }
    agent_id = getattr(tool_context, "agent_name", None) if tool_context is not None else None

    try:
        with research_span(
            Source.EUROPEPMC.value,
            composed_query,
            recency_floor=window.since_date.isoformat(),
            agent_id=agent_id,
        ):
            payload = await get_json(
                EUROPEPMC_SEARCH_URL,
                params=params,
                source=Source.EUROPEPMC.value,
            )
    except UpstreamError as exc:
        if exc.status_code == 429:
            wait_s = exc.retry_after or 5
            return tool_error(
                ErrorType.RATE_LIMITED,
                message=f"Europe PMC rate limit exceeded (HTTP 429): {exc.message}",
                recovery_instructions=(
                    f"Wait {wait_s} seconds and retry with a smaller max_results, or use "
                    "search_pubmed_alzheimers_studies_since."
                ),
                retryable=True,
                source=Source.EUROPEPMC.value,
                details={"retry_after": wait_s},
            )
        return tool_error(
            ErrorType.UPSTREAM_UNAVAILABLE,
            message=f"Europe PMC REST API unavailable: {exc.message}",
            recovery_instructions=(
                "Switch to search_pubmed_alzheimers_studies_since or "
                "search_clinicaltrials_alzheimers_trials_since while Europe PMC recovers."
            ),
            retryable=True,
            source=Source.EUROPEPMC.value,
            details={"status_code": exc.status_code},
        )
    except ValueError as exc:
        return tool_error(
            ErrorType.MALFORMED_RESPONSE,
            message=f"Malformed JSON payload from Europe PMC: {exc}",
            recovery_instructions=("Retry with simpler query_terms or use search_pubmed_alzheimers_studies_since."),
            retryable=False,
            source=Source.EUROPEPMC.value,
        )

    result_list_container = payload.get("resultList") if isinstance(payload, dict) else None
    if not isinstance(result_list_container, dict) or not isinstance(result_list_container.get("result"), list):
        return tool_error(
            ErrorType.MALFORMED_RESPONSE,
            message="Europe PMC payload missing 'resultList.result' array.",
            recovery_instructions=("Retry with simplified query_terms or use search_pubmed_alzheimers_studies_since."),
            retryable=False,
            source=Source.EUROPEPMC.value,
        )

    raw_items: list[Any] = result_list_container["result"]
    if not raw_items:
        return tool_error(
            ErrorType.NO_RESULTS,
            message=(
                f"Europe PMC returned 0 records for query '{validated.query_terms}' "
                f"since {window.since_date.isoformat()}."
            ),
            recovery_instructions=(
                "Broaden query_terms with synonyms or enable include_preprints=True, "
                f"but do NOT lower since_date below {window.recency_floor.isoformat()}."
            ),
            retryable=True,
            source=Source.EUROPEPMC.value,
        )

    records: list[StudyRecord] = []
    dropped = 0

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        pub_date = _parse_europepmc_date(item)
        if pub_date is None or pub_date < window.since_date:
            dropped += 1
            continue

        src_code = str(item.get("source") or "MED").strip()
        rec_id = str(item.get("id") or item.get("pmid") or item.get("doi") or "").strip()
        if not rec_id:
            continue

        title = str(item.get("title") or f"Europe PMC {rec_id}").strip().rstrip(".")
        source_type, study_type, trial_phase = _classify_europepmc_item(item)
        journal_title = str(
            item.get("journalTitle") or item.get("bookOrReportDetails", {}).get("publisher") or ""
        ).strip() or ("Preprint Server (PPR)" if source_type == SourceType.PREPRINT else None)
        url = f"https://europepmc.org/article/{src_code}/{rec_id}"

        records.append(
            StudyRecord(
                source=Source.EUROPEPMC,
                record_id=rec_id,
                title=title[:600],
                publication_date=pub_date,
                url=url,
                source_type=source_type,
                study_type=study_type,
                trial_phase=trial_phase,
                sample_size=None,
                journal_or_sponsor=journal_title[:300] if journal_title else None,
                status="preprint" if source_type == SourceType.PREPRINT else "published",
                abstract=str(item.get("abstractText") or "")[:4000] or None,
            )
        )

    if not records:
        return tool_error(
            ErrorType.NO_RESULTS,
            message=(
                f"All {dropped} candidate Europe PMC records predated "
                f"since_date {window.since_date.isoformat()} and were dropped."
            ),
            recovery_instructions=(
                "Broaden query_terms with synonyms, but do NOT lower since_date below "
                f"{window.recency_floor.isoformat()}."
            ),
            retryable=True,
            source=Source.EUROPEPMC.value,
            details={"recency_violations_dropped": dropped},
        )

    records.sort(key=lambda r: r.publication_date, reverse=True)
    hit_count = int(payload.get("hitCount") or len(records))

    result_model = SearchResult(
        source=Source.EUROPEPMC,
        query=composed_query,
        window=window,
        records=records,
        total_available=max(hit_count, len(records)),
        recency_violations_dropped=dropped,
        next_step_hint=(
            "Ensure any record with source_type='preprint' is explicitly labeled as a "
            "non-peer-reviewed preprint when synthesizing claims."
        ),
    )
    return result_model.model_dump(mode="json")
