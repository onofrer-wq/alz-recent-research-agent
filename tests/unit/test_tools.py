"""Rubric #1 (Tool Docstrings), Rubric #2 (Descriptive Naming), Rubric #3 (JSON Schemas) & Rubric #4 (Guided Errors).

Offline unit tests using ``respx`` for all literature search tools, evidence grading,
critique loop escalation, question scope classification, and JSON Schema export.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx

from alz_research_agent.schemas import (
    EXPORTED_SCHEMA_MODELS,
    default_recency_floor,
    export_all_schemas,
)
from alz_research_agent.tools import (
    CRITIC_TOOLS,
    RESEARCH_TOOLS,
    ErrorType,
    classify_alzheimers_question_scope,
    close_shared_async_client,
    exit_critique_loop,
    fetch_pubmed_abstracts_by_pmid,
    grade_evidence_claim,
    normalize_trial_phase,
    search_clinicaltrials_alzheimers_trials_since,
    search_europepmc_alzheimers_preprints_since,
    search_pubmed_alzheimers_studies_since,
)
from alz_research_agent.tools.clinicaltrials import CLINICALTRIALS_API_URL
from alz_research_agent.tools.europepmc import EUROPEPMC_SEARCH_URL
from alz_research_agent.tools.pubmed import EFETCH_URL, ESEARCH_URL, ESUMMARY_URL


@pytest.fixture(autouse=True)
async def _cleanup_http_client():
    yield
    await close_shared_async_client()


def _recent_iso(days_ago: int = 120) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def test_all_tools_have_complete_google_docstrings() -> None:
    all_tools = [*RESEARCH_TOOLS, *CRITIC_TOOLS, classify_alzheimers_question_scope]
    for fn in all_tools:
        doc = fn.__doc__ or ""
        assert len(doc.splitlines()) >= 10, f"Docstring too short on {fn.__name__}"
        assert "Args:" in doc, f"Missing Args section in {fn.__name__}"
        assert "Returns:" in doc, f"Missing Returns section in {fn.__name__}"
        assert "recovery_instructions" in doc, f"Docstring of {fn.__name__} must document recovery_instructions"


@respx.mock
async def test_pubmed_rejects_old_dates_with_recency_floor_hint_without_network() -> None:
    floor = default_recency_floor().isoformat()
    res = await search_pubmed_alzheimers_studies_since(
        query_terms="lecanemab amyloid clearance",
        since_date="2018-01-01",
    )
    assert res["status"] == "error"
    assert res["error_type"] == ErrorType.RECENCY_VIOLATION.value
    assert floor in res["recovery_instructions"]
    assert len(respx.calls) == 0


@respx.mock
async def test_pubmed_rejects_invalid_arguments_without_calling_network() -> None:
    res = await search_pubmed_alzheimers_studies_since(
        query_terms="ab",
        since_date=_recent_iso(90),
        max_results=500,
    )
    assert res["status"] == "error"
    assert res["error_type"] == ErrorType.INVALID_ARGUMENT.value
    assert "query_terms" in res["message"] or "max_results" in res["message"]
    assert len(respx.calls) == 0


@respx.mock
async def test_pubmed_search_success_post_filters_old_records_and_sorts_newest_first() -> None:
    since = _recent_iso(300)
    newer_date = (date.today() - timedelta(days=30)).strftime("%Y %b %d")
    older_valid_date = (date.today() - timedelta(days=180)).strftime("%Y/%m/%d")

    respx.get(ESEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "esearchresult": {
                    "count": "3",
                    "idlist": ["39000001", "39000002", "31000000"],
                }
            },
        )
    )
    respx.get(ESUMMARY_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {
                    "uids": ["39000001", "39000002", "31000000"],
                    "39000001": {
                        "uid": "39000001",
                        "title": "Observational plasma p-tau217 cohort in Alzheimer's.",
                        "pubdate": older_valid_date,
                        "pubtype": ["Journal Article", "Observational Study"],
                        "fulljournalname": "JAMA Neurology",
                    },
                    "39000002": {
                        "uid": "39000002",
                        "title": "Phase 3 randomized controlled trial of donanemab.",
                        "pubdate": newer_date,
                        "pubtype": ["Randomized Controlled Trial", "Clinical Trial, Phase III"],
                        "fulljournalname": "New England Journal of Medicine",
                    },
                    "31000000": {
                        "uid": "31000000",
                        "title": "Outdated 2019 amyloid study.",
                        "pubdate": "2019 Jan 10",
                        "pubtype": ["Journal Article"],
                        "fulljournalname": "Old Journal",
                    },
                }
            },
        )
    )

    res = await search_pubmed_alzheimers_studies_since(
        query_terms="donanemab p-tau217",
        since_date=since,
        max_results=10,
    )
    assert res["status"] == "success"
    assert res["source"] == "pubmed"
    assert res["recency_violations_dropped"] == 1
    assert len(res["records"]) == 2
    assert res["records"][0]["record_id"] == "39000002"
    assert res["records"][0]["study_type"] == "randomized_controlled_trial"
    assert res["records"][0]["trial_phase"] == "phase_3"
    assert res["records"][1]["record_id"] == "39000001"
    assert res["records"][1]["study_type"] == "observational"


@respx.mock
async def test_pubmed_no_results_returns_guided_error() -> None:
    respx.get(ESEARCH_URL).mock(return_value=httpx.Response(200, json={"esearchresult": {"count": "0", "idlist": []}}))
    res = await search_pubmed_alzheimers_studies_since(
        query_terms="nonexistent_biomarker_xyz987",
        since_date=_recent_iso(60),
    )
    assert res["status"] == "error"
    assert res["error_type"] == ErrorType.NO_RESULTS.value
    assert "since_date" in res["recovery_instructions"]


@respx.mock
async def test_pubmed_429_rate_limit_maps_to_retryable_with_retry_after() -> None:
    respx.get(ESEARCH_URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "12"}, text="Too Many Requests")
    )
    res = await search_pubmed_alzheimers_studies_since(
        query_terms="lecanemab",
        since_date=_recent_iso(60),
    )
    assert res["status"] == "error"
    assert res["error_type"] == ErrorType.RATE_LIMITED.value
    assert res["retryable"] is True
    assert "12" in res["recovery_instructions"]


@respx.mock
async def test_pubmed_malformed_payload_maps_to_malformed_response() -> None:
    respx.get(ESEARCH_URL).mock(return_value=httpx.Response(200, text="<<<not-json>>>"))
    res = await search_pubmed_alzheimers_studies_since(
        query_terms="lecanemab",
        since_date=_recent_iso(60),
    )
    assert res["status"] == "error"
    assert res["error_type"] == ErrorType.MALFORMED_RESPONSE.value


@respx.mock
async def test_fetch_pubmed_abstracts_by_pmid_parses_xml_and_missing_ids() -> None:
    xml_body = """<?xml version="1.0" ?>
    <PubmedArticleSet>
      <PubmedArticle>
        <MedlineCitation>
          <PMID>39000002</PMID>
          <Article>
            <Journal>
              <JournalIssue>
                <PubDate><Year>2025</Year><Month>Mar</Month><Day>15</Day></PubDate>
              </JournalIssue>
            </Journal>
            <ArticleTitle>Lecanemab Phase 3 Biomarker Outcomes</ArticleTitle>
            <Abstract>
              <AbstractText Label="METHODS">Enrolled 1795 participants with early AD.</AbstractText>
              <AbstractText Label="RESULTS">Reduced amyloid plaques at 18 months.</AbstractText>
            </Abstract>
          </Article>
        </MedlineCitation>
      </PubmedArticle>
    </PubmedArticleSet>
    """
    respx.get(EFETCH_URL).mock(return_value=httpx.Response(200, text=xml_body))

    res = await fetch_pubmed_abstracts_by_pmid(["39000002", "39999999"])
    assert res["status"] == "success"
    assert len(res["articles"]) == 1
    article = res["articles"][0]
    assert article["pmid"] == "39000002"
    assert "1795 participants" in article["abstract"]
    assert article["publication_date"] == "2025-03-15"
    assert article["url"] == "https://pubmed.ncbi.nlm.nih.gov/39000002/"
    assert res["not_found_pmids"] == ["39999999"]


@respx.mock
async def test_clinicaltrials_uses_filter_advanced_never_filter_phase_and_normalizes() -> None:
    since = _recent_iso(240)
    recent_start = (date.today() - timedelta(days=45)).strftime("%Y-%m")

    route = respx.get(CLINICALTRIALS_API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "totalCount": 1,
                "studies": [
                    {
                        "protocolSection": {
                            "identificationModule": {
                                "nctId": "NCT06123456",
                                "briefTitle": "Subcutaneous Lecanemab Maintenance Trial in Early AD",
                            },
                            "statusModule": {
                                "overallStatus": "RECRUITING",
                                "startDateStruct": {"date": recent_start},
                            },
                            "designModule": {
                                "phases": ["PHASE2", "PHASE3"],
                                "enrollmentInfo": {"count": 450},
                            },
                            "sponsorCollaboratorsModule": {
                                "leadSponsor": {"name": "Eisai Inc."},
                            },
                        }
                    }
                ],
            },
        )
    )

    res = await search_clinicaltrials_alzheimers_trials_since(
        since_date=since,
        intervention_terms="lecanemab",
        phases=["phase_2", "phase_3"],
        max_results=15,
    )
    assert res["status"] == "success"
    assert len(route.calls) == 1
    sent_params = dict(route.calls[0].request.url.params)
    assert "filter.phase" not in sent_params
    assert "filter.advanced" in sent_params
    assert f"AREA[StartDate]RANGE[{since},MAX]" in sent_params["filter.advanced"]
    assert "AREA[Phase](PHASE2 OR PHASE3)" in sent_params["filter.advanced"]

    rec = res["records"][0]
    assert rec["record_id"] == "NCT06123456"
    assert rec["trial_phase"] == "phase_3"
    assert rec["sample_size"] == 450
    assert rec["source_type"] == "trial_registry"
    assert rec["study_type"] == "trial_registration"
    assert normalize_trial_phase("PHASE1/PHASE2") == "phase_2"


@respx.mock
async def test_clinicaltrials_400_returns_guided_error() -> None:
    respx.get(CLINICALTRIALS_API_URL).mock(return_value=httpx.Response(400, text="Invalid filter expression"))
    res = await search_clinicaltrials_alzheimers_trials_since(
        since_date=_recent_iso(100),
        intervention_terms="donanemab",
    )
    assert res["status"] == "error"
    assert res["error_type"] == ErrorType.INVALID_ARGUMENT.value
    assert "filter.advanced" in res["recovery_instructions"]


@respx.mock
async def test_europepmc_tags_preprints_and_includes_first_pdate_in_query() -> None:
    since = _recent_iso(200)
    preprint_date = (date.today() - timedelta(days=20)).isoformat()
    journal_date = (date.today() - timedelta(days=90)).isoformat()

    route = respx.get(EUROPEPMC_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "hitCount": 2,
                "resultList": {
                    "result": [
                        {
                            "id": "PPR881122",
                            "source": "PPR",
                            "title": "Preprint: TREM2 agonism in 5xFAD mouse microglia.",
                            "pubType": "preprint",
                            "firstPublicationDate": preprint_date,
                        },
                        {
                            "id": "39112233",
                            "source": "MED",
                            "title": "Randomized trial of anti-tau antibody in early Alzheimer's.",
                            "pubType": "journal article",
                            "firstPublicationDate": journal_date,
                            "journalTitle": "Lancet Neurology",
                        },
                    ]
                },
            },
        )
    )

    res = await search_europepmc_alzheimers_preprints_since(
        query_terms="TREM2 microglia",
        since_date=since,
        include_preprints=True,
    )
    assert res["status"] == "success"
    sent_query = dict(route.calls[0].request.url.params)["query"]
    assert f"FIRST_PDATE:[{since} TO " in sent_query
    assert "SRC:PPR" in sent_query

    assert len(res["records"]) == 2
    assert res["records"][0]["record_id"] == "PPR881122"
    assert res["records"][0]["source_type"] == "preprint"
    assert res["records"][1]["record_id"] == "39112233"
    assert res["records"][1]["source_type"] == "peer_reviewed"


def test_grade_evidence_claim_high_phase3_and_low_extrapolated_preprint() -> None:
    high_res = grade_evidence_claim(
        claim="Donanemab slowed clinical decline by 35% in low/medium tau early AD.",
        supporting_record_ids=["39000002"],
        study_type="randomized_controlled_trial",
        studied_population="early symptomatic Alzheimer patients with low-to-medium tau",
        target_population="early symptomatic Alzheimer patients with low-to-medium tau",
        trial_phase="phase_3",
        sample_size=1736,
        source_type="peer_reviewed",
    )
    assert high_res["status"] == "success"
    assert high_res["confidence"] == "high"
    assert high_res["extrapolation"] is False

    low_res = grade_evidence_claim(
        claim="Intranasal peptide reverses cognitive loss in late-stage Alzheimer's.",
        supporting_record_ids=["PPR881122"],
        study_type="observational",
        studied_population="mild cognitive impairment (MCI) cohort",
        target_population="advanced late-stage Alzheimer patients",
        trial_phase="not_applicable",
        sample_size=45,
        source_type="preprint",
    )
    assert low_res["status"] == "success"
    assert low_res["confidence"] == "low"
    assert low_res["extrapolation"] is True
    assert "SPECULATIVE" in low_res["note"].upper()

    bad_res = grade_evidence_claim(
        claim="tiny",
        supporting_record_ids=[],
        study_type="not_a_study_type",
        studied_population="mice",
        target_population="humans",
    )
    assert bad_res["status"] == "error"
    assert bad_res["error_type"] == ErrorType.INVALID_ARGUMENT.value


def test_exit_critique_loop_sets_escalate_and_skip_summarization() -> None:
    fake_ctx = SimpleNamespace(
        actions=SimpleNamespace(escalate=False, skip_summarization=False),
        state={},
    )
    res = exit_critique_loop(
        reason="All claims carry graded citations from 2024-2026 and speculative labels.",
        tool_context=fake_ctx,
    )
    assert res["status"] == "success"
    assert fake_ctx.actions.escalate is True
    assert fake_ctx.actions.skip_summarization is True
    assert fake_ctx.state["critic_approved"] is True


def test_classify_question_scope_covers_four_cases_and_clamps_date() -> None:
    floor = default_recency_floor().isoformat()

    # 1. Informational + date clamping (requests since 2015 -> clamped to floor)
    ctx_info = SimpleNamespace(state={})
    res_info = classify_alzheimers_question_scope(
        "What biomarkers for Alzheimer's p-tau217 have emerged since 2015?",
        tool_context=ctx_info,
    )
    assert res_info["status"] == "success"
    assert res_info["in_scope"] is True
    assert res_info["stakes"] == "informational"
    assert res_info["requires_human_review"] is False
    assert res_info["since_date_to_use"] == floor
    assert ctx_info.state["since_date_to_use"] == floor

    # 2. Treatment-related -> triggers human review
    ctx_tx = SimpleNamespace(state={})
    res_tx = classify_alzheimers_question_scope(
        "Should my mother take lecanemab and what dosage should we start?",
        tool_context=ctx_tx,
    )
    assert res_tx["in_scope"] is True
    assert res_tx["stakes"] == "treatment_related"
    assert res_tx["requires_human_review"] is True
    assert ctx_tx.state["requires_human_review"] is True

    # 3. Emergency
    res_em = classify_alzheimers_question_scope(
        "My father with Alzheimer's is having a seizure and chest pain right now!"
    )
    assert res_em["in_scope"] is True
    assert res_em["stakes"] == "emergency"

    # 4. Out of scope
    res_oos = classify_alzheimers_question_scope("How do I configure a Kubernetes ingress controller on GKE?")
    assert res_oos["in_scope"] is False


def test_export_all_schemas_writes_13_files(tmp_path: Path) -> None:
    written = export_all_schemas(tmp_path)
    assert len(written) == 13
    assert len(EXPORTED_SCHEMA_MODELS) == 13
    for path in written:
        assert path.exists()
        assert path.suffix == ".json"
