"""SQLAlchemy 2 ORM models. The Alembic migrations are the source of truth for DDL;
``tests/test_migrations.py`` checks that these models and the migrated schema don't drift."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBED_DIM = 1024

TSV_DE_EXPR = (
    "CASE WHEN posting_language = 'de' THEN "
    "setweight(to_tsvector('german'::regconfig, coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('german'::regconfig, coalesce(raw_text, '')), 'B') END"
)
TSV_EN_EXPR = (
    "CASE WHEN posting_language = 'en' THEN "
    "setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('english'::regconfig, coalesce(raw_text, '')), 'B') END"
)


class Base(DeclarativeBase):
    pass


def _now() -> Any:
    return func.now()


class Posting(Base):
    __tablename__ = "postings"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_postings_source_source_id"),
        Index("ix_postings_tsv_de", "tsv_de", postgresql_using="gin"),
        Index("ix_postings_tsv_en", "tsv_en", postgresql_using="gin"),
        Index("ix_postings_required_skills", "required_skills", postgresql_using="gin"),
        Index(
            "ix_postings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_postings_city_active", "city", "is_active"),
        Index("ix_postings_content_hash", "content_hash"),
    )

    # identity
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(256))
    url: Mapped[str] = mapped_column(Text)
    # core text
    title: Mapped[str] = mapped_column(Text)
    company: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str] = mapped_column(Text, server_default="")
    posting_language: Mapped[str] = mapped_column(String(2), server_default="en")
    # location
    city: Mapped[str | None] = mapped_column(Text)
    cities: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    country: Mapped[str | None] = mapped_column(Text)
    locations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    job_family: Mapped[str | None] = mapped_column(String(64))
    source_remote_flag: Mapped[bool | None] = mapped_column(Boolean)
    # JobLens schema fields
    remote_policy: Mapped[str] = mapped_column(String(16), server_default="unknown")
    seniority: Mapped[str] = mapped_column(String(16), server_default="unknown")
    min_years_experience: Mapped[int | None] = mapped_column(SmallInteger)
    required_skills: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    nice_to_have_skills: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    languages_required: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    german_level: Mapped[str] = mapped_column(String(16), server_default="none")
    english_sufficient: Mapped[str] = mapped_column(String(8), server_default="unclear")
    contract_type: Mapped[str] = mapped_column(String(16), server_default="unknown")
    contract_duration_months: Mapped[int | None] = mapped_column(SmallInteger)
    salary_min: Mapped[float | None] = mapped_column(Float)
    salary_max: Mapped[float | None] = mapped_column(Float)
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    salary_period: Mapped[str | None] = mapped_column(String(8))
    annual_salary_min_eur: Mapped[float | None] = mapped_column(Float)
    annual_salary_max_eur: Mapped[float | None] = mapped_column(Float)
    salary_stated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    bluecard_status: Mapped[str] = mapped_column(String(24), server_default="salary_not_stated")
    visa_sponsorship: Mapped[str] = mapped_column(String(16), server_default="not_mentioned")
    relocation_support: Mapped[str] = mapped_column(String(16), server_default="not_mentioned")
    application_deadline: Mapped[dt.date | None] = mapped_column(Date)
    # dates & freshness
    published_at: Mapped[dt.date | None] = mapped_column(Date)
    first_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=_now()
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=_now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    last_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    consecutive_failures: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    inactive_since: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # provenance
    content_hash: Mapped[str] = mapped_column(String(64))
    duplicate_of: Mapped[str | None] = mapped_column(String(32))
    extraction_status: Mapped[str] = mapped_column(String(16), server_default="pending")
    extraction_error: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str | None] = mapped_column(String(128))
    schema_version: Mapped[str | None] = mapped_column(String(32))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    extracted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # search
    tsv_de: Mapped[Any] = mapped_column(
        TSVECTOR, Computed(TSV_DE_EXPR, persisted=True), nullable=True
    )
    tsv_en: Mapped[Any] = mapped_column(
        TSVECTOR, Computed(TSV_EN_EXPR, persisted=True), nullable=True
    )
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(EMBED_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    embedded_hash: Mapped[str | None] = mapped_column(String(64))


class IngestRun(Base):
    __tablename__ = "ingest_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), server_default="ingest")
    source: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), server_default="running")
    fetched: Mapped[int] = mapped_column(Integer, server_default="0")
    inserted: Mapped[int] = mapped_column(Integer, server_default="0")
    updated: Mapped[int] = mapped_column(Integer, server_default="0")
    unchanged: Mapped[int] = mapped_column(Integer, server_default="0")
    duplicates: Mapped[int] = mapped_column(Integer, server_default="0")
    failed: Mapped[int] = mapped_column(Integer, server_default="0")
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    is_demo: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class SavedSearch(Base):
    __tablename__ = "saved_searches"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(200))
    query: Mapped[str | None] = mapped_column(Text)
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    api_key_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("api_keys.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    trace_id: Mapped[str | None] = mapped_column(String(64))
    session_id: Mapped[str | None] = mapped_column(String(64))
    message_id: Mapped[str | None] = mapped_column(String(64))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    rating: Mapped[int] = mapped_column(SmallInteger)
    comment: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(String(16), server_default="new")
    api_key_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("api_keys.id", ondelete="SET NULL")
    )


class CvProfile(Base):
    """Only written when the user explicitly opts in. Stores the structured profile,
    never the raw CV text."""

    __tablename__ = "cv_profiles"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB)
    api_key_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("api_keys.id", ondelete="CASCADE")
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class LlmCache(Base):
    """Cache of LLM outputs keyed by sha256(kind | model | prompt_version | input)."""

    __tablename__ = "llm_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(128))
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    tokens_in: Mapped[int] = mapped_column(Integer, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class EmbeddingCache(Base):
    __tablename__ = "embedding_cache"
    text_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(128), primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(VECTOR(EMBED_DIM))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
