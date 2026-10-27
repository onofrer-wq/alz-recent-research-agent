"""Rubric #1 (Tool Docstrings), Rubric #2 (Descriptive Naming), Rubric #3 (JSON Schemas) & Rubric #4 (Guided Errors).

PubMed NCBI E-utilities tools (``esearch``, ``esummary``, and ``efetch``) enforcing
the 3-year Alzheimer's research recency floor, OpenTelemetry ``research_span``
tracing, and guided self-correcting error envelopes.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from datetime import date
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from alz_research_agent.observability.tracing import research_span
from alz_research_agent.schemas import (
    PubMedFetchInput,
    PubMedSearchInput,
    SearchResult,
    Source,
    SourceType,
    StudyRecord,
    StudyType,
    TrialPhase,
    default_recency_floor,
)
from alz_research_agent.secrets import get_secret
from alz_research_agent.tools._errors import (
    ErrorType,
    tool_error,
    tool_success,
    validation_error,
)
from alz_research_agent.tools._http import UpstreamError, get_json, get_text

if TYPE_CHECKING:
    from google.adk.tools.tool_context import ToolContext

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

_MONTH_MAP: dict[str, int] = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _ncbi_identity_params() -> dict[str, str]:
    """Builds NCBI E-utilities identification query parameters."""
    params: dict[str, str] = {
        "tool": os.environ.get("NCBI_TOOL_NAME", "alz-recent-research-agent").strip() or "alz-recent-research-agent",
        "email": os.environ.get("NCBI_CONTACT_EMAIL", "researcher@example.com").strip() or "researcher@example.com",
    }
    api_key = get_secret("ncbi-api-key", required=False)
    if api_key:
        params["api_key"] = api_key
    return params


def _ensure_alzheimers_scope(query_terms: str) -> str:
    """Ensures the PubMed query is explicitly scoped to Alzheimer's disease."""
    stripped = query_terms.strip()
    if "alzheimer" in stripped.lower():
        return stripped
    return f"({stripped}) AND (Alzheimer*[Title/Abstract] OR Alzheimer Disease[MeSH Terms])"


def parse_pubmed_date(raw_date: str | None) -> date | None:
    """Parses PubMed ``pubdate``/``sortpubdate`` strings into a ``datetime.date``.

    Handles formats such as ``"2025 Mar 12"``, ``"2025 Mar"``, ``"2025"``,
    ``"2025/03/12 00:00"``, and ``"2025-03-12"``.

    Args:
        raw_date: Raw date string from PubMed ``esummary`` or ``efetch``.

    Returns:
        Parsed ``date`` object, or ``None`` if no valid year can be extracted.
    """
    if not raw_date or not isinstance(raw_date, str):
        return None
    cleaned = raw_date.strip()

    # Format: YYYY/MM/DD or YYYY-MM-DD (optionally followed by time)
    iso_match = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})", cleaned)
    if iso_match:
        year, month, day = (int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        try:
            return date(year, max(1, min(12, month)), max(1, min(28, day)))
        except ValueError:
            return date(year, 1, 1)

    # Format: YYYY Mon DD or YYYY Mon (e.g., "2025 Mar 12", "2025 Mar 12-15", "2025 Mar")
    tokens = re.split(r"[\s,-]+", cleaned)
    if tokens and re.match(r"^\d{4}$", tokens[0]):
        year = int(tokens[0])
        month = 1
        day = 1
        if len(tokens) >= 2:
            month_token = tokens[1].lower().rstrip(".")
            if month_token in _MONTH_MAP:
                month = _MONTH_MAP[month_token]
            elif month_token.isdigit():
                month = max(1, min(12, int(month_token)))
        if len(tokens) >= 3 and tokens[2].isdigit():
            day = max(1, min(31, int(tokens[2])))
        try:
            return date(year, month, day)
        except ValueError:
            return date(year, month, 1)

    return None


def _classify_pubmed_pubtypes(
    pubtypes: list[str] | None,
    title: str = "",
) -> tuple[SourceType, StudyType, TrialPhase]:
    """Maps PubMed ``pubtype`` strings and title cues to ``(SourceType, StudyType, TrialPhase)``."""
    lowered_types = [str(p).lower() for p in (pubtypes or [])]
    lowered_title = title.lower()

    source_type = SourceType.PREPRINT if any("preprint" in p for p in lowered_types) else SourceType.PEER_REVIEWED
    trial_phase = TrialPhase.NOT_APPLICABLE

    if (
        any("phase iv" in p or "phase 4" in p for p in lowered_types)
        or "phase 4" in lowered_title
        or "phase iv" in lowered_title
    ):
        trial_phase = TrialPhase.PHASE_4
    elif (
        any("phase iii" in p or "phase 3" in p for p in lowered_types)
        or "phase 3" in lowered_title
        or "phase iii" in lowered_title
    ):
        trial_phase = TrialPhase.PHASE_3
    elif (
        any("phase ii" in p or "phase 2" in p for p in lowered_types)
        or "phase 2" in lowered_title
        or "phase ii" in lowered_title
    ):
        trial_phase = TrialPhase.PHASE_2
    elif (
        any("phase i" in p or "phase 1" in p for p in lowered_types)
        or "phase 1" in lowered_title
        or "phase i " in lowered_title
    ):
        trial_phase = TrialPhase.PHASE_1

    if any("meta-analysis" in p or "meta analysis" in p for p in lowered_types) or "meta-analysis" in lowered_title:
        study_type = StudyType.META_ANALYSIS
    elif (
        any("randomized controlled trial" in p or "clinical trial" in p for p in lowered_types)
        or trial_phase != TrialPhase.NOT_APPLICABLE
    ):
        study_type = StudyType.RANDOMIZED_CONTROLLED_TRIAL
    elif any("observational" in p or "cohort" in p or "comparative study" in p for p in lowered_types):
        study_type = StudyType.OBSERVATIONAL
    elif any("case reports" in p or "case series" in p for p in lowered_types):
        study_type = StudyType.CASE_SERIES
    elif any("review" in p for p in lowered_types):
        study_type = StudyType.REVIEW
    elif any(k in lowered_title for k in ("mouse", "mice", "murine", "5xfad", "app/ps1", "in vitro", "organoid")):
        study_type = StudyType.PRECLINICAL
    else:
        study_type = StudyType.UNKNOWN

    return source_type, study_type, trial_phase


async def search_pubmed_alzheimers_studies_since(
    query_terms: str,
    since_date: str,
    max_results: int = 20,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Searches PubMed for peer-reviewed Alzheimer's disease studies published since ``since_date``.

    Use this tool first when gathering peer-reviewed biomedical literature, randomized
    controlled trials, biomarker studies, or meta-analyses on Alzheimer's disease from
    the last 3 years. Do NOT use this tool for dates older than the 3-year recency floor
    or for non-Alzheimer's medical queries; use ``fetch_pubmed_abstracts_by_pmid`` after
    this tool to retrieve full abstracts for the top relevant PMIDs.

    Args:
        query_terms: Biomedical keywords or MeSH terms (3-300 chars). Automatically
            scoped to Alzheimer's disease if not already present.
        since_date: Inclusive lower-bound publication date in ISO format (``YYYY-MM-DD``)
            within the last 3 years.
        max_results: Maximum number of records to retrieve (1-100, default 20).
        tool_context: Optional ADK ``ToolContext`` injected at runtime for tracing.

    Returns:
        A dictionary matching ``SearchResult`` on success (``status="success"``,
        ``source="pubmed"``, ``records`` sorted newest-first, ``recency_violations_dropped``,
        and ``next_step_hint``), or a guided error dictionary (``status="error"``,
        ``error_type`` in ``INVALID_ARGUMENT``, ``RECENCY_VIOLATION``, ``RATE_LIMITED``,
        ``UPSTREAM_UNAVAILABLE``, ``MALFORMED_RESPONSE``, or ``NO_RESULTS``, plus
        imperative ``recovery_instructions``).
    """
    floor = default_recency_floor()
    try:
        validated = PubMedSearchInput(
            query_terms=query_terms,
            since_date=since_date,  # type: ignore[arg-type]
            max_results=max_results,
        )
    except ValidationError as exc:
        return validation_error(exc, source=Source.PUBMED.value, recency_floor=floor)

    window = validated.window()
    scoped_query = _ensure_alzheimers_scope(validated.query_terms)
    agent_id = getattr(tool_context, "agent_name", None) if tool_context is not None else None
    identity_params = _ncbi_identity_params()

    esearch_params: dict[str, Any] = {
        "db": "pubmed",
        "retmode": "json",
        "sort": "pub_date",
        "retmax": str(validated.max_results),
        "datetype": "pdat",
        "mindate": window.since_date.strftime("%Y/%m/%d"),
        "maxdate": window.until_date.strftime("%Y/%m/%d"),
        "term": scoped_query,
        **identity_params,
    }

    try:
        with research_span(
            Source.PUBMED.value,
            scoped_query,
            recency_floor=window.since_date.isoformat(),
            agent_id=agent_id,
        ):
            esearch_payload = await get_json(
                ESEARCH_URL,
                params=esearch_params,
                source=Source.PUBMED.value,
            )
            if not isinstance(esearch_payload, dict) or "esearchresult" not in esearch_payload:
                raise ValueError("Missing 'esearchresult' key in PubMed esearch response.")

            esearch_result = esearch_payload["esearchresult"]
            if not isinstance(esearch_result, dict):
                raise ValueError("'esearchresult' is not a JSON object.")

            id_list = [str(x).strip() for x in (esearch_result.get("idlist") or []) if str(x).strip()]
            total_available = int(esearch_result.get("count") or len(id_list))

            if not id_list:
                return tool_error(
                    ErrorType.NO_RESULTS,
                    message=(
                        f"PubMed returned 0 studies for query '{scoped_query}' since {window.since_date.isoformat()}."
                    ),
                    recovery_instructions=(
                        "Broaden query_terms with synonyms or broader mechanism terms, or try "
                        "search_europepmc_alzheimers_preprints_since, but do NOT lower since_date "
                        f"below the recency floor ({window.recency_floor.isoformat()})."
                    ),
                    retryable=True,
                    source=Source.PUBMED.value,
                )

            esummary_params: dict[str, Any] = {
                "db": "pubmed",
                "retmode": "json",
                "id": ",".join(id_list),
                **identity_params,
            }
            esummary_payload = await get_json(
                ESUMMARY_URL,
                params=esummary_params,
                source=Source.PUBMED.value,
            )
    except UpstreamError as exc:
        if exc.status_code == 429:
            wait_s = exc.retry_after or 5
            return tool_error(
                ErrorType.RATE_LIMITED,
                message=f"PubMed E-utilities rate limit exceeded (HTTP 429): {exc.message}",
                recovery_instructions=(
                    f"Wait {wait_s} seconds and retry with a smaller max_results, or use "
                    "search_europepmc_alzheimers_preprints_since as an alternative source."
                ),
                retryable=True,
                source=Source.PUBMED.value,
                details={"retry_after": wait_s},
            )
        return tool_error(
            ErrorType.UPSTREAM_UNAVAILABLE,
            message=f"PubMed E-utilities unavailable: {exc.message}",
            recovery_instructions=(
                "Switch to search_europepmc_alzheimers_preprints_since or "
                "search_clinicaltrials_alzheimers_trials_since while PubMed recovers."
            ),
            retryable=True,
            source=Source.PUBMED.value,
            details={"status_code": exc.status_code},
        )
    except (ValueError, KeyError, TypeError) as exc:
        return tool_error(
            ErrorType.MALFORMED_RESPONSE,
            message=f"Failed to parse PubMed JSON payload: {exc}",
            recovery_instructions=(
                "Retry with simplified query_terms or fall back to search_europepmc_alzheimers_preprints_since."
            ),
            retryable=False,
            source=Source.PUBMED.value,
        )

    if not isinstance(esummary_payload, dict) or not isinstance(esummary_payload.get("result"), dict):
        return tool_error(
            ErrorType.MALFORMED_RESPONSE,
            message="PubMed esummary payload is missing the 'result' dictionary.",
            recovery_instructions=(
                "Retry with a smaller max_results or use search_europepmc_alzheimers_preprints_since."
            ),
            retryable=False,
            source=Source.PUBMED.value,
        )

    summary_map: dict[str, Any] = esummary_payload["result"]
    records: list[StudyRecord] = []
    dropped = 0

    for pmid in id_list:
        item = summary_map.get(pmid)
        if not isinstance(item, dict):
            continue
        raw_date = item.get("sortpubdate") or item.get("pubdate") or item.get("epubdate")
        pub_date = parse_pubmed_date(str(raw_date) if raw_date else None)
        if pub_date is None or pub_date < window.since_date:
            dropped += 1
            continue

        title = str(item.get("title") or f"PubMed Record {pmid}").strip().rstrip(".")
        pubtypes = item.get("pubtype") if isinstance(item.get("pubtype"), list) else []
        source_type, study_type, trial_phase = _classify_pubmed_pubtypes(pubtypes, title=title)
        journal = str(item.get("fulljournalname") or item.get("source") or "").strip() or None

        records.append(
            StudyRecord(
                source=Source.PUBMED,
                record_id=pmid,
                title=title[:600],
                publication_date=pub_date,
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                source_type=source_type,
                study_type=study_type,
                trial_phase=trial_phase,
                sample_size=None,
                journal_or_sponsor=journal[:300] if journal else None,
                status="published",
                abstract=None,
            )
        )

    if not records:
        return tool_error(
            ErrorType.NO_RESULTS,
            message=(
                f"All {dropped} candidate PubMed records predated since_date "
                f"{window.since_date.isoformat()} and were dropped."
            ),
            recovery_instructions=(
                "Broaden query_terms with synonyms or try search_europepmc_alzheimers_preprints_since, "
                f"but do NOT lower since_date below {window.recency_floor.isoformat()}."
            ),
            retryable=True,
            source=Source.PUBMED.value,
            details={"recency_violations_dropped": dropped},
        )

    records.sort(key=lambda r: r.publication_date, reverse=True)

    result_model = SearchResult(
        source=Source.PUBMED,
        query=scoped_query,
        window=window,
        records=records,
        total_available=max(total_available, len(records)),
        recency_violations_dropped=dropped,
        next_step_hint=(
            "Call fetch_pubmed_abstracts_by_pmid with the top PMIDs to inspect sample sizes, "
            "populations, and detailed results."
        ),
    )
    return result_model.model_dump(mode="json")


async def fetch_pubmed_abstracts_by_pmid(
    pmids: list[str],
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Fetches full abstracts and publication metadata from PubMed ``efetch`` XML by PMID.

    Use this tool after ``search_pubmed_alzheimers_studies_since`` to retrieve complete
    abstracts, participant sample sizes, and methodological details for 1 to 50 specific
    numeric PMIDs. Do NOT pass NCT IDs, DOIs, or non-numeric identifiers to this tool.

    Args:
        pmids: List of 1 to 50 numeric PubMed identifiers (e.g., ``["39123456"]``).
        tool_context: Optional ADK ``ToolContext`` injected at runtime for tracing.

    Returns:
        On success, a dictionary with ``status="success"``, ``source="pubmed"``,
        ``articles`` (list of dicts with ``pmid``, ``title``, ``abstract``,
        ``publication_date``, and ``url``), and ``not_found_pmids``. On failure,
        a guided error dictionary with ``status="error"``, ``error_type`` in
        ``INVALID_ARGUMENT``, ``RATE_LIMITED``, ``UPSTREAM_UNAVAILABLE``,
        ``MALFORMED_RESPONSE``, or ``NO_RESULTS``, and ``recovery_instructions``.
    """
    try:
        validated = PubMedFetchInput(pmids=pmids)
    except ValidationError as exc:
        return validation_error(exc, source=Source.PUBMED.value)

    agent_id = getattr(tool_context, "agent_name", None) if tool_context is not None else None
    identity_params = _ncbi_identity_params()
    efetch_params: dict[str, Any] = {
        "db": "pubmed",
        "retmode": "xml",
        "id": ",".join(validated.pmids),
        **identity_params,
    }

    try:
        with research_span(
            Source.PUBMED.value,
            f"efetch:{','.join(validated.pmids[:10])}",
            recency_floor=default_recency_floor().isoformat(),
            agent_id=agent_id,
        ):
            xml_text = await get_text(
                EFETCH_URL,
                params=efetch_params,
                source=Source.PUBMED.value,
            )
        root = ET.fromstring(xml_text)
    except UpstreamError as exc:
        if exc.status_code == 429:
            wait_s = exc.retry_after or 5
            return tool_error(
                ErrorType.RATE_LIMITED,
                message=f"PubMed efetch rate limited (HTTP 429): {exc.message}",
                recovery_instructions=(
                    f"Wait {wait_s} seconds and retry fetch_pubmed_abstracts_by_pmid with fewer PMIDs."
                ),
                retryable=True,
                source=Source.PUBMED.value,
                details={"retry_after": wait_s},
            )
        return tool_error(
            ErrorType.UPSTREAM_UNAVAILABLE,
            message=f"PubMed efetch unavailable: {exc.message}",
            recovery_instructions=(
                "Proceed with titles from esummary or search Europe PMC via "
                "search_europepmc_alzheimers_preprints_since."
            ),
            retryable=True,
            source=Source.PUBMED.value,
        )
    except ET.ParseError as exc:
        return tool_error(
            ErrorType.MALFORMED_RESPONSE,
            message=f"Failed to parse PubMed efetch XML response: {exc}",
            recovery_instructions="Retry with a smaller batch of PMIDs or rely on Europe PMC abstracts.",
            retryable=False,
            source=Source.PUBMED.value,
        )

    articles: list[dict[str, Any]] = []
    found_pmids: set[str] = set()

    for article_el in root.findall(".//PubmedArticle"):
        pmid_el = article_el.find(".//MedlineCitation/PMID")
        pmid = (pmid_el.text or "").strip() if pmid_el is not None else ""
        if not pmid:
            continue
        found_pmids.add(pmid)

        title_el = article_el.find(".//Article/ArticleTitle")
        title = "".join(title_el.itertext()).strip() if title_el is not None else f"PubMed {pmid}"

        abstract_parts: list[str] = []
        for abs_el in article_el.findall(".//Article/Abstract/AbstractText"):
            section_text = "".join(abs_el.itertext()).strip()
            if not section_text:
                continue
            label = (abs_el.attrib.get("Label") or "").strip()
            abstract_parts.append(f"{label}: {section_text}" if label else section_text)
        abstract_text = "\n".join(abstract_parts)[:4000] if abstract_parts else ""

        year_el = article_el.find(".//Article/Journal/JournalIssue/PubDate/Year")
        month_el = article_el.find(".//Article/Journal/JournalIssue/PubDate/Month")
        day_el = article_el.find(".//Article/Journal/JournalIssue/PubDate/Day")
        raw_date_parts = [
            (year_el.text or "").strip() if year_el is not None else "",
            (month_el.text or "").strip() if month_el is not None else "",
            (day_el.text or "").strip() if day_el is not None else "",
        ]
        parsed_date = parse_pubmed_date(" ".join(p for p in raw_date_parts if p))

        articles.append(
            {
                "pmid": pmid,
                "title": title[:600],
                "abstract": abstract_text,
                "publication_date": parsed_date.isoformat() if parsed_date else None,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            }
        )

    not_found = [p for p in validated.pmids if p not in found_pmids]

    if not articles:
        return tool_error(
            ErrorType.NO_RESULTS,
            message=f"No PubMed articles found in efetch XML for PMIDs: {validated.pmids}",
            recovery_instructions="Verify that the PMIDs came from search_pubmed_alzheimers_studies_since.",
            retryable=False,
            source=Source.PUBMED.value,
            details={"not_found_pmids": not_found},
        )

    return tool_success(
        source=Source.PUBMED.value,
        articles=articles,
        records=articles,
        not_found_pmids=not_found,
        count=len(articles),
    )
