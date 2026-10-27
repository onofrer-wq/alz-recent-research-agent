"""Rubric #1 (Tool Docstrings), Rubric #2 (Descriptive Naming), Rubric #3 (JSON Schemas) & Rubric #4 (Guided Errors).

Public exports for all ADK tools and grouped toolsets ``RESEARCH_TOOLS`` and
``CRITIC_TOOLS``.
"""

from __future__ import annotations

from alz_research_agent.tools._errors import (
    ErrorType,
    is_tool_error,
    tool_error,
    tool_success,
    validation_error,
)
from alz_research_agent.tools._http import (
    UpstreamError,
    close_shared_async_client,
    get_json,
    get_shared_async_client,
    get_text,
)
from alz_research_agent.tools.clinicaltrials import (
    normalize_trial_phase,
    search_clinicaltrials_alzheimers_trials_since,
)
from alz_research_agent.tools.europepmc import (
    search_europepmc_alzheimers_preprints_since,
)
from alz_research_agent.tools.evidence import (
    classify_alzheimers_question_scope,
    exit_critique_loop,
    grade_evidence_claim,
)
from alz_research_agent.tools.pubmed import (
    fetch_pubmed_abstracts_by_pmid,
    parse_pubmed_date,
    search_pubmed_alzheimers_studies_since,
)

RESEARCH_TOOLS = [
    search_pubmed_alzheimers_studies_since,
    fetch_pubmed_abstracts_by_pmid,
    search_clinicaltrials_alzheimers_trials_since,
    search_europepmc_alzheimers_preprints_since,
]

CRITIC_TOOLS = [
    grade_evidence_claim,
    exit_critique_loop,
]

__all__ = [
    "CRITIC_TOOLS",
    "ErrorType",
    "RESEARCH_TOOLS",
    "UpstreamError",
    "classify_alzheimers_question_scope",
    "close_shared_async_client",
    "exit_critique_loop",
    "fetch_pubmed_abstracts_by_pmid",
    "get_json",
    "get_shared_async_client",
    "get_text",
    "grade_evidence_claim",
    "is_tool_error",
    "normalize_trial_phase",
    "parse_pubmed_date",
    "search_clinicaltrials_alzheimers_trials_since",
    "search_europepmc_alzheimers_preprints_since",
    "search_pubmed_alzheimers_studies_since",
    "tool_error",
    "tool_success",
    "validation_error",
]
