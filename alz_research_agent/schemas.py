"""Rubric #3 (Explicit JSON Schemas) & Rubric #2 (Descriptive Naming Conventions).

Strict Pydantic v2 data contracts with ``extra="forbid"`` for all tool inputs,
literature records, evidence grades, consultant drafts, critic verdicts, and
question scope classifications. Supports deterministic JSON Schema export via
``python -m alz_research_agent.schemas --export schemas``.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def default_recency_floor(
    recency_years: int | None = None,
    *,
    today: date | None = None,
) -> date:
    """Computes the earliest allowed publication date for the recency window.

    Args:
        recency_years: Number of years back from ``today``; defaults to ``RECENCY_YEARS`` (3).
        today: Optional reference date; defaults to ``date.today()``.

    Returns:
        Earliest allowed ``date`` (inclusive).
    """
    ref = today or date.today()
    years = recency_years if recency_years is not None else int(os.environ.get("RECENCY_YEARS", "3"))
    target_year = ref.year - max(1, years)
    try:
        return date(target_year, ref.month, ref.day)
    except ValueError:
        return date(target_year, ref.month, 28)


def _validate_since_date_against_floor(value: date) -> date:
    """Validates that ``value`` is within the strict recency window and not in the future."""
    floor = default_recency_floor()
    today = date.today()
    if value < floor:
        raise ValueError(
            f"since_date {value.isoformat()} is older than the recency floor "
            f"{floor.isoformat()}. Use a date on or after {floor.isoformat()}."
        )
    if value > today:
        raise ValueError(f"since_date {value.isoformat()} cannot be in the future (today is {today.isoformat()}).")
    return value


class StrictModel(BaseModel):
    """Base Pydantic model enforcing ``extra='forbid'`` and whitespace stripping."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Source(StrEnum):
    """Supported biomedical literature and clinical trial registries."""

    PUBMED = "pubmed"
    CLINICALTRIALS_GOV = "clinicaltrials_gov"
    EUROPEPMC = "europepmc"
    SEMANTIC_SCHOLAR = "semantic_scholar"


class SourceType(StrEnum):
    """Publication vetting category of a biomedical record."""

    PEER_REVIEWED = "peer_reviewed"
    PREPRINT = "preprint"
    TRIAL_REGISTRY = "trial_registry"


class StudyType(StrEnum):
    """Methodological design classification of a study."""

    RANDOMIZED_CONTROLLED_TRIAL = "randomized_controlled_trial"
    OBSERVATIONAL = "observational"
    META_ANALYSIS = "meta_analysis"
    PRECLINICAL = "preclinical"
    CASE_SERIES = "case_series"
    TRIAL_REGISTRATION = "trial_registration"
    REVIEW = "review"
    UNKNOWN = "unknown"


class TrialPhase(StrEnum):
    """Clinical trial phase classification."""

    PHASE_1 = "phase_1"
    PHASE_2 = "phase_2"
    PHASE_3 = "phase_3"
    PHASE_4 = "phase_4"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class Confidence(StrEnum):
    """Methodological confidence grade assigned to an evidence claim."""

    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"


class RecencyWindow(StrictModel):
    """Bounded publication date window enforcing the 3-year recency floor."""

    since_date: date = Field(description="Inclusive lower bound for publication/start dates.")
    until_date: date = Field(
        default_factory=date.today,
        description="Inclusive upper bound for publication/start dates.",
    )
    recency_floor: date = Field(
        default_factory=default_recency_floor,
        description="Earliest date permitted by the 3-year scientific recency policy.",
    )

    @model_validator(mode="after")
    def _check_bounds(self) -> RecencyWindow:
        if self.since_date < self.recency_floor:
            raise ValueError(
                f"since_date {self.since_date.isoformat()} violates the recency floor {self.recency_floor.isoformat()}."
            )
        if self.since_date > self.until_date:
            raise ValueError(
                f"since_date {self.since_date.isoformat()} must not exceed until_date {self.until_date.isoformat()}."
            )
        return self


class StudyRecord(StrictModel):
    """Normalized biomedical study or clinical trial record."""

    source: Source = Field(description="Originating literature database or trial registry.")
    record_id: str = Field(
        min_length=1,
        max_length=120,
        description="Primary identifier (PMID, NCT ID, Europe PMC ID, or DOI).",
    )
    title: str = Field(min_length=1, max_length=600, description="Title of the study or trial.")
    publication_date: date = Field(description="Publication, posting, or trial start date.")
    url: str = Field(description="Canonical HTTPS URL to the study record.")
    source_type: SourceType = Field(description="Peer-reviewed, preprint, or trial registry category.")
    study_type: StudyType = Field(
        default=StudyType.UNKNOWN,
        description="Methodological design of the study.",
    )
    trial_phase: TrialPhase = Field(
        default=TrialPhase.NOT_APPLICABLE,
        description="Clinical trial phase if applicable.",
    )
    sample_size: int | None = Field(
        default=None,
        ge=0,
        description="Enrolled or analyzed participant count (N) if reported.",
    )
    journal_or_sponsor: str | None = Field(
        default=None,
        max_length=300,
        description="Journal title, preprint server, or lead clinical trial sponsor.",
    )
    status: str = Field(
        default="published",
        max_length=100,
        description="Publication or trial recruitment status.",
    )
    abstract: str | None = Field(
        default=None,
        max_length=4000,
        description="Bounded abstract or study summary text.",
    )

    @field_validator("url")
    @classmethod
    def _validate_https_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("StudyRecord.url must start with 'https://'.")
        return value


class ResearchDossier(StrictModel):
    """Aggregated collection of recent studies assembled by the Researcher agent."""

    question: str = Field(min_length=3, max_length=1000, description="User scientific question.")
    recency_window: RecencyWindow = Field(description="Enforced recency window for all searches.")
    records: list[StudyRecord] = Field(
        default_factory=list,
        description="Deduplicated study records passing the recency filter.",
    )
    sources_consulted: list[Source] = Field(
        default_factory=list,
        description="Literature sources queried to build this dossier.",
    )
    recency_violations_dropped: int = Field(
        default=0,
        ge=0,
        description="Count of upstream records discarded for predating the recency floor.",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Methodological or search coverage notes for downstream agents.",
    )


class EvidenceGrade(StrictModel):
    """Methodological quality and extrapolation assessment for a single scientific claim."""

    claim: str = Field(min_length=5, max_length=1000, description="Specific scientific claim evaluated.")
    supporting_record_ids: list[str] = Field(
        min_length=1,
        description="At least one PMID, NCT ID, or Europe PMC ID supporting the claim.",
    )
    study_type: StudyType = Field(description="Study design of the primary supporting evidence.")
    trial_phase: TrialPhase = Field(
        default=TrialPhase.NOT_APPLICABLE,
        description="Clinical trial phase of the primary supporting evidence.",
    )
    sample_size: int | None = Field(
        default=None,
        ge=0,
        description="Sample size (N) of the supporting study.",
    )
    source_type: SourceType = Field(description="Vetting category of the supporting source.")
    confidence: Confidence = Field(description="Deterministic methodological confidence rating.")
    extrapolation: bool = Field(
        default=False,
        description="True when the claim extrapolates beyond the studied population or model.",
    )
    note: str = Field(
        default="",
        max_length=1000,
        description="Mandatory explanation of confidence grade and speculative extrapolation label.",
    )


class ConsultantDraft(StrictModel):
    """Structured scientific literacy synthesis produced by the Consultant agent."""

    short_answer: str = Field(
        min_length=10,
        max_length=1500,
        description="Concise executive synthesis of recent (< 3 years) findings.",
    )
    claims: list[EvidenceGrade] = Field(
        min_length=1,
        description="Graded evidence claims underpinning the synthesis.",
    )
    whats_new: list[str] = Field(
        default_factory=list,
        description="Key experimental or recent discoveries from the last 3 years.",
    )
    established_context: list[str] = Field(
        default_factory=list,
        description="Brief context connecting recent findings to prior knowledge.",
    )
    limitations: list[str] = Field(
        default_factory=list,
        description="Methodological caveats, small sample sizes, or preprint disclaimers.",
    )
    references: list[str] = Field(
        default_factory=list,
        description="Formatted citations with record IDs and HTTPS URLs.",
    )


class CriticVerdict(StrictModel):
    """Evaluation output from the Critic agent governing loop termination and HITL review."""

    approved: bool = Field(description="Whether the draft satisfies all methodological and recency rules.")
    iteration: int = Field(default=1, ge=1, description="Critique loop iteration counter.")
    issues: list[str] = Field(
        default_factory=list,
        description="Specific evidentiary or recency defects identified in the draft.",
    )
    required_changes: list[str] = Field(
        default_factory=list,
        description="Actionable edits required from the Consultant before approval.",
    )
    speculative_extrapolations: list[str] = Field(
        default_factory=list,
        description="Claims permitted as optimistic extrapolations that must carry speculative labels.",
    )
    newest_findings_emphasised: bool = Field(
        default=True,
        description="Whether the draft prioritizes the most recent and experimental findings.",
    )
    requires_human_review: bool = Field(
        default=False,
        description="True if any part of the response touches upon clinical treatment decisions.",
    )


class QuestionScope(StrictModel):
    """Scope, stakes, and temporal classification of an incoming user question."""

    in_scope: bool = Field(description="True if the query pertains to Alzheimer's or related neurodegeneration.")
    topic_tags: list[str] = Field(
        default_factory=list,
        description="Detected domain tags (e.g., amyloid, tau, biomarkers, clinical_trials).",
    )
    stakes: Literal["informational", "treatment_related", "emergency"] = Field(
        description="Risk classification governing human-in-the-loop confirmation or emergency triage.",
    )
    requested_since_date: date | None = Field(
        default=None,
        description="Clamped lower-bound date extracted from user query, if any.",
    )
    reason: str = Field(
        min_length=3,
        max_length=500,
        description="Explanation of the scope and stakes classification.",
    )


class PubMedSearchInput(StrictModel):
    """Validated input schema for ``search_pubmed_alzheimers_studies_since``."""

    query_terms: str = Field(
        min_length=3,
        max_length=300,
        description="Biomedical search keywords (automatically scoped to Alzheimer's disease).",
    )
    since_date: date = Field(
        description="ISO date (YYYY-MM-DD) within the last 3 years.",
    )
    max_results: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of PubMed records to return (1-100).",
    )

    @field_validator("since_date")
    @classmethod
    def _check_since_date(cls, value: date) -> date:
        return _validate_since_date_against_floor(value)

    def window(self) -> RecencyWindow:
        """Constructs a validated ``RecencyWindow`` from ``since_date``."""
        return RecencyWindow(since_date=self.since_date)


class PubMedFetchInput(StrictModel):
    """Validated input schema for ``fetch_pubmed_abstracts_by_pmid``."""

    pmids: list[str] = Field(
        min_length=1,
        max_length=50,
        description="List of 1 to 50 numeric PubMed identifiers (PMIDs).",
    )

    @field_validator("pmids")
    @classmethod
    def _check_numeric_pmids(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in values:
            item = str(raw).strip()
            if not item.isdigit():
                raise ValueError(f"Invalid PMID '{raw}': all PMIDs must be numeric strings (digits only).")
            cleaned.append(item)
        return cleaned


class ClinicalTrialsSearchInput(StrictModel):
    """Validated input schema for ``search_clinicaltrials_alzheimers_trials_since``."""

    condition: str = Field(
        default="Alzheimer Disease",
        min_length=3,
        max_length=200,
        description="Disease or condition query passed to ClinicalTrials.gov query.cond.",
    )
    intervention_terms: str | None = Field(
        default=None,
        max_length=200,
        description="Optional drug, biologic, device, or behavioral intervention terms.",
    )
    since_date: date = Field(
        description="Earliest trial start date (YYYY-MM-DD) within the 3-year recency window.",
    )
    phases: list[TrialPhase] = Field(
        default_factory=list,
        description="Optional clinical trial phases to filter via AREA[Phase].",
    )
    max_results: int = Field(
        default=25,
        ge=1,
        le=100,
        description="Maximum number of trial records to return (1-100).",
    )

    @field_validator("since_date")
    @classmethod
    def _check_since_date(cls, value: date) -> date:
        return _validate_since_date_against_floor(value)

    def window(self) -> RecencyWindow:
        """Constructs a validated ``RecencyWindow`` from ``since_date``."""
        return RecencyWindow(since_date=self.since_date)


class EuropePmcSearchInput(StrictModel):
    """Validated input schema for ``search_europepmc_alzheimers_preprints_since``."""

    query_terms: str = Field(
        min_length=3,
        max_length=300,
        description="Keywords searched across Europe PMC peer-reviewed papers and preprints.",
    )
    since_date: date = Field(
        description="Earliest first-publication date (YYYY-MM-DD) within the 3-year recency window.",
    )
    include_preprints: bool = Field(
        default=True,
        description="When True, includes bioRxiv/medRxiv preprints (SRC:PPR) alongside MED/PMC.",
    )
    max_results: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of Europe PMC records to return (1-100).",
    )

    @field_validator("since_date")
    @classmethod
    def _check_since_date(cls, value: date) -> date:
        return _validate_since_date_against_floor(value)

    def window(self) -> RecencyWindow:
        """Constructs a validated ``RecencyWindow`` from ``since_date``."""
        return RecencyWindow(since_date=self.since_date)


class EvidenceGradeInput(StrictModel):
    """Validated input schema for ``grade_evidence_claim``."""

    claim: str = Field(
        min_length=5,
        max_length=1000,
        description="Scientific claim to grade.",
    )
    supporting_record_ids: list[str] = Field(
        min_length=1,
        description="List of supporting record IDs (at least one required).",
    )
    study_type: StudyType = Field(description="Methodological study design.")
    studied_population: str = Field(
        min_length=2,
        max_length=300,
        description="Population or experimental model actually studied (e.g., '5xFAD mice', 'MCI patients').",
    )
    target_population: str = Field(
        min_length=2,
        max_length=300,
        description="Population to which the claim is applied (e.g., 'late-stage Alzheimer patients').",
    )
    trial_phase: TrialPhase = Field(
        default=TrialPhase.NOT_APPLICABLE,
        description="Trial phase if clinical trial.",
    )
    sample_size: int | None = Field(
        default=None,
        ge=0,
        description="Participant or sample size (N).",
    )
    source_type: SourceType = Field(
        default=SourceType.PEER_REVIEWED,
        description="Vetting category of the source.",
    )


class SearchResult(StrictModel):
    """Standardized successful search response envelope returned by literature tools."""

    status: Literal["success"] = "success"
    source: Source = Field(description="Literature source that produced these results.")
    query: str = Field(description="Executed query expression.")
    window: RecencyWindow = Field(description="Applied recency window.")
    records: list[StudyRecord] = Field(
        default_factory=list,
        description="Records sorted from newest to oldest.",
    )
    total_available: int = Field(
        default=0,
        ge=0,
        description="Total matching records reported by the upstream API.",
    )
    recency_violations_dropped: int = Field(
        default=0,
        ge=0,
        description="Records dropped during post-filtering for predating since_date.",
    )
    next_step_hint: str = Field(
        default="",
        description="Actionable guidance for the calling agent on what tool or step to run next.",
    )


EXPORTED_SCHEMA_MODELS: tuple[type[StrictModel], ...] = (
    RecencyWindow,
    StudyRecord,
    ResearchDossier,
    EvidenceGrade,
    ConsultantDraft,
    CriticVerdict,
    QuestionScope,
    PubMedSearchInput,
    PubMedFetchInput,
    ClinicalTrialsSearchInput,
    EuropePmcSearchInput,
    EvidenceGradeInput,
    SearchResult,
)


def export_all_schemas(output_dir: str | Path) -> list[Path]:
    """Writes ``<ModelName>.schema.json`` for every model in ``EXPORTED_SCHEMA_MODELS``.

    Args:
        output_dir: Destination directory path.

    Returns:
        List of written ``Path`` objects.
    """
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for model_cls in EXPORTED_SCHEMA_MODELS:
        schema_dict: dict[str, Any] = model_cls.model_json_schema()
        schema_path = target_dir / f"{model_cls.__name__}.schema.json"
        schema_path.write_text(
            json.dumps(schema_dict, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written.append(schema_path)
    return written


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint supporting ``--export DIR`` to write JSON Schema files."""
    parser = argparse.ArgumentParser(description="Export Pydantic v2 JSON Schemas for alz_research_agent.")
    parser.add_argument(
        "--export",
        dest="export_dir",
        required=True,
        help="Directory where <ModelName>.schema.json files will be written.",
    )
    args = parser.parse_args(argv)
    export_all_schemas(args.export_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
