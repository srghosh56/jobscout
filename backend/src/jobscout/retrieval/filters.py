"""F1 search filters, applied in SQL (never post-hoc in Python, so ranking and
LIMIT apply to the filtered set)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import ColumnElement, and_, exists, func, literal_column, or_, select, true

from jobscout.db.models import Posting

RemotePolicy = Literal["onsite", "hybrid", "remote", "unknown"]
Seniority = Literal["intern", "junior", "mid", "senior", "staff_plus", "unknown"]
BlueCard = Literal["meets_standard", "meets_reduced_only", "below", "salary_not_stated"]


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str | None = Field(
        None, description="City name, e.g. 'Berlin' or 'München'; several: 'Berlin, München'"
    )
    remote_policy: list[RemotePolicy] | None = None
    seniority: list[Seniority] | None = None
    max_years: int | None = Field(None, ge=0, le=40, description="Max required years of experience")
    skills: list[str] | None = Field(None, description="All of these skills must be listed")
    posting_language: Literal["en", "de"] | None = None
    english_sufficient: bool | None = Field(None, description="True: English is enough")
    visa_sponsorship: bool | None = Field(None, description="True: visa sponsorship mentioned")
    relocation_support: bool | None = Field(None, description="True: relocation support mentioned")
    salary_stated: bool | None = None
    bluecard_status: list[BlueCard] | None = None
    job_family: str | None = None
    active_only: bool = True

    @field_validator("city", mode="before")
    @classmethod
    def _cities_to_text(cls, value: Any) -> Any:
        """Models often send ["Berlin", "München"] where the field needs one string."""
        return ", ".join(str(v) for v in value) if isinstance(value, list) else value

    @field_validator("remote_policy", "seniority", "skills", "bluecard_status", mode="before")
    @classmethod
    def _single_value_to_list(cls, value: Any) -> Any:
        """Models often send "remote" where the field needs ["remote"]."""
        return [value] if isinstance(value, str) else value

    def is_empty(self) -> bool:
        return not any(
            v not in (None, [], True) for k, v in self.model_dump().items() if k != "active_only"
        )


def city_variants(city: str) -> list[str]:
    from jobscout.ingest.cleaning import normalize_city

    canon = normalize_city(city) or city
    return sorted({city.lower(), canon.lower()})


def filter_clauses(f: SearchFilters | None) -> list[ColumnElement[bool]]:
    f = f or SearchFilters()
    c: list[ColumnElement[bool]] = [Posting.duplicate_of.is_(None)]
    from jobscout.settings import get_settings

    sources = get_settings().public_sources
    if sources:
        c.append(Posting.source.in_(sources))
    if f.active_only:
        c.append(Posting.is_active.is_(True))
    if f.city:
        variants = sorted(
            {v for city in f.city.split(",") if city.strip() for v in city_variants(city.strip())}
        )
        c.append(
            or_(
                func.lower(Posting.city).in_(variants),
                exists(
                    select(literal_column("1"))
                    .select_from(func.unnest(Posting.cities).alias("cty"))
                    .where(func.lower(literal_column("cty")).in_(variants))
                ),
            )
        )
    if f.remote_policy:
        c.append(Posting.remote_policy.in_(f.remote_policy))
    if f.seniority:
        c.append(Posting.seniority.in_(f.seniority))
    if f.max_years is not None:
        c.append(Posting.min_years_experience <= f.max_years)
    if f.skills:
        for skill in f.skills:
            c.append(
                exists(
                    select(literal_column("1"))
                    .select_from(
                        func.unnest(
                            func.array_cat(Posting.required_skills, Posting.nice_to_have_skills)
                        ).alias("sk")
                    )
                    .where(func.lower(literal_column("sk")) == skill.strip().lower())
                )
            )
    if f.posting_language:
        c.append(Posting.posting_language == f.posting_language)
    if f.english_sufficient is not None:
        c.append(
            Posting.english_sufficient == "yes"
            if f.english_sufficient
            else Posting.english_sufficient != "yes"
        )
    if f.visa_sponsorship is not None:
        c.append(
            Posting.visa_sponsorship == "yes"
            if f.visa_sponsorship
            else Posting.visa_sponsorship != "yes"
        )
    if f.relocation_support is not None:
        c.append(
            Posting.relocation_support == "yes"
            if f.relocation_support
            else Posting.relocation_support != "yes"
        )
    if f.salary_stated is not None:
        c.append(Posting.salary_stated.is_(f.salary_stated))
    if f.bluecard_status:
        c.append(Posting.bluecard_status.in_(f.bluecard_status))
    if f.job_family:
        c.append(Posting.job_family == f.job_family)
    return c


def where(f: SearchFilters | None) -> ColumnElement[bool]:
    clauses = filter_clauses(f)
    return and_(true(), *clauses)
