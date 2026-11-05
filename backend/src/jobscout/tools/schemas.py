"""Pydantic input/output schemas for the eight agent tools (also used by the MCP server
and the HTTP API, so all three surfaces share one contract)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from jobscout.retrieval.filters import SearchFilters

POSTING_ID_PATTERN = r"^(ba|an)-[0-9a-f]{10}$"


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- inputs -------------------------------------------------------------------
class SqlQueryInput(_In):
    sql: str = Field(
        ...,
        description="One read-only PostgreSQL SELECT over the allowed views, ending with LIMIT <= 50.",
        max_length=4000,
    )


class SearchPostingsInput(_In):
    query: str = Field(
        ..., description="What the user is looking for, in their words.", max_length=500
    )
    filters: SearchFilters | None = Field(None, description="Optional structured filters.")
    top_k: int = Field(8, ge=1, le=20)


class GetPostingInput(_In):
    posting_id: str = Field(..., pattern=POSTING_ID_PATTERN, description="e.g. 'an-3f9a1c2d7e'")


class ComparePostingsInput(_In):
    posting_ids: list[str] = Field(..., min_length=2, max_length=5)


class LanguageSkill(_In):
    language: str
    level: Literal["basic", "good", "fluent", "native", "unspecified"] = "unspecified"


class CandidateProfile(_In):
    """Structured CV profile (no names, contact data or raw CV text)."""

    headline: str = Field("", max_length=200)
    skills: list[str] = Field(default_factory=list, max_length=60)
    years_experience: float | None = Field(None, ge=0, le=60)
    languages: list[LanguageSkill] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list, max_length=10)
    preferred_cities: list[str] = Field(default_factory=list, max_length=10)
    target_roles: list[str] = Field(default_factory=list, max_length=10)


class MatchCvInput(_In):
    profile: CandidateProfile
    filters: SearchFilters | None = None
    top_k: int = Field(8, ge=1, le=20)


class GapAnalysisInput(_In):
    profile: CandidateProfile
    posting_id: str = Field(..., pattern=POSTING_ID_PATTERN)


class BlueCardCheckInput(_In):
    posting_id: str | None = Field(None, pattern=POSTING_ID_PATTERN)
    salary_min: float | None = Field(None, ge=0)
    salary_max: float | None = Field(None, ge=0)
    currency: str | None = "EUR"
    period: Literal["year", "month", "hour"] | None = "year"
    year: int | None = Field(None, ge=2020, le=2035)


class AskClarificationInput(_In):
    question: str = Field(..., max_length=300, description="One short question for the user.")


# --- outputs ------------------------------------------------------------------
class SqlResult(BaseModel):
    sql: str
    columns: list[str] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    truncated: bool = False
    repaired: str | None = None
    error: str | None = None


class PostingSummary(BaseModel):
    id: str
    title: str
    company: str | None
    city: str | None
    url: str
    remote_policy: str
    seniority: str
    min_years_experience: int | None
    english_sufficient: str
    german_level: str
    bluecard_status: str
    visa_sponsorship: str
    relocation_support: str
    salary: str | None
    required_skills: list[str]
    snippet: str = ""
    last_checked_at: str | None = None
    score: float | None = None


class SearchResult(BaseModel):
    query: str
    results: list[PostingSummary]


class PostingDetail(PostingSummary):
    nice_to_have_skills: list[str] = []
    languages_required: list[dict[str, Any]] = []
    contract_type: str = "unknown"
    posting_language: str = "en"
    published_at: str | None = None
    is_active: bool = True
    source: str = ""
    bluecard: dict[str, Any] | None = None
    excerpt: str = ""  # bounded excerpt for the model; never the full text in the UI


class CompareResult(BaseModel):
    fields: list[str]
    postings: list[PostingDetail]


class Gap(BaseModel):
    kind: Literal["skill", "german", "years", "location", "bluecard"]
    detail: str
    severity: Literal["blocker", "major", "minor"]


class GapResult(BaseModel):
    posting_id: str
    matched_skills: list[str]
    missing_skills: list[str]
    gaps: list[Gap]
    summary: str


class CvMatch(BaseModel):
    posting: PostingSummary
    score: float
    explanation: str
    gaps: GapResult


class MatchResult(BaseModel):
    profile: CandidateProfile
    matches: list[CvMatch]


class BlueCardResultModel(BaseModel):
    status: str
    annual_min_eur: float | None
    annual_max_eur: float | None
    threshold_year: int
    standard_threshold: float
    reduced_threshold: float
    explanation: str
    disclaimer: str
    source_url: str
    posting_id: str | None = None


class Clarification(BaseModel):
    question: str
