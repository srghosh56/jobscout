"""Structured schema for a job posting.

Bump ``SCHEMA_VERSION`` on any change; every
stored row records the schema version it was extracted with.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "joblens-1.0"

RemotePolicy = Literal["onsite", "hybrid", "remote", "unknown"]
Seniority = Literal["intern", "junior", "mid", "senior", "staff_plus", "unknown"]
LanguageLevel = Literal["basic", "good", "fluent", "native", "unspecified"]
YesNoUnclear = Literal["yes", "no", "unclear"]
YesNoNotMentioned = Literal["yes", "no", "not_mentioned"]
ContractType = Literal[
    "permanent", "fixed_term", "freelance", "internship", "working_student", "unknown"
]
SalaryPeriod = Literal["year", "month", "hour"]
PostingLanguage = Literal["en", "de"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Location(_Strict):
    city: str | None = None
    country: str | None = None


class LanguageRequirement(_Strict):
    language: str
    level: LanguageLevel = "unspecified"


class Salary(_Strict):
    min: float | None = Field(default=None, ge=0)
    max: float | None = Field(default=None, ge=0)
    currency: str = "EUR"
    period: SalaryPeriod = "year"

    @model_validator(mode="after")
    def _min_le_max(self) -> Salary:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("salary.min must be <= salary.max")
        if self.min is None and self.max is None:
            raise ValueError("salary needs at least one of min/max; use null for no salary")
        return self


class JobPosting(_Strict):
    title: str
    company: str | None = None
    locations: list[Location] = Field(default_factory=list)
    remote_policy: RemotePolicy = "unknown"
    seniority: Seniority = "unknown"
    min_years_experience: int | None = Field(default=None, ge=0, le=40)
    required_skills: list[str] = Field(default_factory=list)
    nice_to_have_skills: list[str] = Field(default_factory=list)
    languages_required: list[LanguageRequirement] = Field(default_factory=list)
    english_sufficient: YesNoUnclear = "unclear"
    contract_type: ContractType = "unknown"
    contract_duration_months: int | None = Field(default=None, ge=0, le=120)
    salary: Salary | None = None
    visa_sponsorship: YesNoNotMentioned = "not_mentioned"
    relocation_support: YesNoNotMentioned = "not_mentioned"
    application_deadline: date | None = None
    posting_language: PostingLanguage = "en"


def _require_all(node: object) -> None:
    """Mark every property as required (nullable ones stay nullable). Constrained
    decoders skip optional properties, so without this the model omits fields."""
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            node["required"] = list(props)
            for prop in props.values():
                if isinstance(prop, dict):
                    prop.pop("default", None)
        for value in node.values():
            _require_all(value)
    elif isinstance(node, list):
        for item in node:
            _require_all(item)


def strict_schema(model: type[BaseModel]) -> dict[str, object]:
    schema = model.model_json_schema()
    _require_all(schema)
    return schema


def json_schema() -> dict[str, object]:
    """JSON schema handed to the LLM in JSON-schema (structured output) mode."""
    return strict_schema(JobPosting)
