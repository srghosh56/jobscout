"""Hybrid retrieval: Postgres full-text search (german/english configurations by
posting language) + pgvector cosine search, fused with RRF, reranked by a CPU
cross-encoder. Filters are applied in SQL inside each retriever."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from sqlalchemy import Float, Select, String, bindparam, cast, func, literal, or_, select, text
from sqlalchemy.dialects.postgresql import REGCONFIG, TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession

from jobscout.db.engine import session_scope
from jobscout.db.models import Posting
from jobscout.embed import embed_query
from jobscout.logging import get_logger
from jobscout.metrics import RETRIEVAL_LATENCY
from jobscout.retrieval.filters import SearchFilters, where
from jobscout.retrieval.fusion import rrf
from jobscout.settings import get_settings

log = get_logger(__name__)

Mode = Literal["fts", "dense", "hybrid", "hybrid_rerank"]
HEADLINE_OPTS = (
    "MaxFragments=2, MaxWords=22, MinWords=8, StartSel=«, StopSel=», FragmentDelimiter= … "
)


@dataclass
class SearchHit:
    id: str
    title: str
    company: str | None
    city: str | None
    url: str
    score: float
    snippet: str = ""
    remote_policy: str = "unknown"
    seniority: str = "unknown"
    min_years_experience: int | None = None
    english_sufficient: str = "unclear"
    german_level: str = "none"
    bluecard_status: str = "salary_not_stated"
    visa_sponsorship: str = "not_mentioned"
    relocation_support: str = "not_mentioned"
    salary_min: float | None = None
    salary_max: float | None = None
    salary_period: str | None = None
    required_skills: list[str] = field(default_factory=list)
    posting_language: str = "en"
    last_checked_at: str | None = None
    published_at: str | None = None
    source: str = ""
    extraction_status: str = "pending"
    ranks: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SUMMARY_COLS = (
    Posting.id,
    Posting.title,
    Posting.company,
    Posting.city,
    Posting.url,
    Posting.remote_policy,
    Posting.seniority,
    Posting.min_years_experience,
    Posting.english_sufficient,
    Posting.german_level,
    Posting.bluecard_status,
    Posting.visa_sponsorship,
    Posting.relocation_support,
    Posting.salary_min,
    Posting.salary_max,
    Posting.salary_period,
    Posting.required_skills,
    Posting.posting_language,
    Posting.last_checked_at,
    Posting.published_at,
    Posting.source,
    Posting.extraction_status,
)


def _hit(row: Any, score: float, snippet: str = "") -> SearchHit:
    return SearchHit(
        id=row.id,
        title=row.title,
        company=row.company,
        city=row.city,
        url=row.url,
        score=score,
        snippet=snippet,
        remote_policy=row.remote_policy,
        seniority=row.seniority,
        min_years_experience=row.min_years_experience,
        english_sufficient=row.english_sufficient,
        german_level=row.german_level,
        bluecard_status=row.bluecard_status,
        visa_sponsorship=row.visa_sponsorship,
        relocation_support=row.relocation_support,
        salary_min=row.salary_min,
        salary_max=row.salary_max,
        salary_period=row.salary_period,
        required_skills=list(row.required_skills or []),
        posting_language=row.posting_language,
        last_checked_at=row.last_checked_at.isoformat() if row.last_checked_at else None,
        published_at=row.published_at.isoformat() if row.published_at else None,
        source=row.source,
        extraction_status=row.extraction_status,
    )


def _or_query(cfg: str, param: str) -> Any:
    """plainto_tsquery joins terms with AND; we turn it into OR so long natural-language
    queries still match, and let ts_rank_cd reward documents matching more terms."""
    base = func.plainto_tsquery(cast(literal(cfg), REGCONFIG), bindparam(param, type_=String))
    return cast(func.replace(cast(base, String), "&", "|"), TSQUERY)


def clean_query(q: str) -> str:
    return re.sub(r"[^\w\s\-+#.]", " ", q).strip()


def fts_statement(filters: SearchFilters | None, limit: int) -> Select[Any]:
    q_de = _or_query("german", "q")
    q_en = _or_query("english", "q")
    rank = func.greatest(
        func.coalesce(func.ts_rank_cd(Posting.tsv_de, q_de, 32), 0.0),
        func.coalesce(func.ts_rank_cd(Posting.tsv_en, q_en, 32), 0.0),
    ).cast(Float)
    match = or_(Posting.tsv_de.bool_op("@@")(q_de), Posting.tsv_en.bool_op("@@")(q_en))
    return (
        select(*SUMMARY_COLS, rank.label("rank"))
        .where(where(filters), match)
        .order_by(rank.desc(), Posting.id)
        .limit(limit)
    )


async def fulltext_search(
    session: AsyncSession, query: str, filters: SearchFilters | None = None, limit: int = 50
) -> list[SearchHit]:
    q = clean_query(query)
    if not q:
        return []
    rows = (await session.execute(fts_statement(filters, limit), {"q": q})).all()
    return [_hit(r, float(r.rank)) for r in rows]


async def snippets(session: AsyncSession, ids: list[str], query: str) -> dict[str, str]:
    """Short highlighted fragments (never the full text) for display/citations."""
    if not ids:
        return {}
    stmt = text(
        """
        SELECT id, ts_headline(
                 CASE WHEN posting_language = 'de' THEN 'german' ELSE 'english' END::regconfig,
                 left(raw_text, 20000),
                 replace(plainto_tsquery(CASE WHEN posting_language = 'de' THEN 'german' ELSE 'english' END::regconfig, :q)::text, '&', '|')::tsquery,
                 :opts) AS snip,
               left(raw_text, 220) AS head
        FROM postings WHERE id = ANY(:ids)
        """
    )
    rows = (
        await session.execute(
            stmt, {"q": clean_query(query) or "x", "ids": ids, "opts": HEADLINE_OPTS}
        )
    ).all()
    out = {}
    for r in rows:
        snip = r.snip if r.snip and "«" in r.snip else (r.head + "…")
        out[r.id] = re.sub(r"\s+", " ", snip)
    return out


async def vector_search(
    session: AsyncSession,
    query_vec: list[float],
    filters: SearchFilters | None = None,
    limit: int = 50,
) -> list[SearchHit]:
    dist = Posting.embedding.cosine_distance(query_vec)
    # HNSW + WHERE filters: let pgvector keep scanning until enough rows pass the filter.
    await session.execute(text("SET LOCAL hnsw.ef_search = 100"))
    await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    stmt = (
        select(*SUMMARY_COLS, dist.label("dist"))
        .where(where(filters), Posting.embedding.is_not(None))
        .order_by(dist)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    hits = [_hit(r, 1.0 - float(r.dist)) for r in rows]
    # relaxed_order may return slightly out-of-order rows: re-sort exactly.
    return sorted(hits, key=lambda h: -h.score)


def rerank_text(h: SearchHit, snippet: str) -> str:
    skills = ", ".join(h.required_skills[:12])
    return f"{h.title} | {h.company or ''} | {h.city or ''} | skills: {skills} | {snippet}"


async def hybrid_search(
    query: str,
    filters: SearchFilters | None = None,
    top_k: int | None = None,
    mode: Mode = "hybrid_rerank",
    candidates: int | None = None,
    query_vec: list[float] | None = None,
) -> list[SearchHit]:
    s = get_settings()
    top_k = top_k or s.rerank_top_k
    candidates = candidates or s.retrieval_candidates
    t0 = time.perf_counter()
    fts_hits: list[SearchHit] = []
    vec_hits: list[SearchHit] = []

    async def run_fts() -> None:
        nonlocal fts_hits
        async with session_scope() as sess:
            fts_hits = await fulltext_search(sess, query, filters, candidates)

    async def run_vec() -> None:
        nonlocal vec_hits
        vec = query_vec if query_vec is not None else await embed_query(query)
        async with session_scope() as sess, sess.begin():
            vec_hits = await vector_search(sess, vec, filters, candidates)

    if mode == "fts":
        await run_fts()
    elif mode == "dense":
        await run_vec()
    else:
        await asyncio.gather(run_fts(), run_vec())

    by_id: dict[str, SearchHit] = {}
    for h in vec_hits + fts_hits:
        by_id.setdefault(h.id, h)
    fts_rank = {h.id: i for i, h in enumerate(fts_hits, start=1)}
    vec_rank = {h.id: i for i, h in enumerate(vec_hits, start=1)}

    if mode == "fts":
        ordered = [(h.id, h.score) for h in fts_hits]
    elif mode == "dense":
        ordered = [(h.id, h.score) for h in vec_hits]
    else:
        ordered = rrf([[h.id for h in fts_hits], [h.id for h in vec_hits]], k=s.rrf_k)[:candidates]

    async with session_scope() as sess:
        snips = await snippets(sess, [i for i, _ in ordered[: max(top_k, candidates)]], query)

    if mode == "hybrid_rerank" and s.reranker_enabled and ordered:
        docs = [(i, rerank_text(by_id[i], snips.get(i, ""))) for i, _ in ordered]
        ordered = await _rerank(query, docs, top_k)

    out = []
    for doc_id, score in ordered[:top_k]:
        h = by_id[doc_id]
        h.score = float(score)
        h.snippet = snips.get(doc_id, "")
        h.ranks = {
            k: v for k, v in (("fts", fts_rank.get(doc_id)), ("dense", vec_rank.get(doc_id))) if v
        }
        out.append(h)
    RETRIEVAL_LATENCY.labels(mode=mode).observe(time.perf_counter() - t0)
    return out


async def _rerank(query: str, docs: list[tuple[str, str]], top_k: int) -> list[tuple[str, float]]:
    from jobscout.retrieval.rerank import rerank

    try:
        return await rerank(query, docs, top_k)
    except Exception as exc:  # reranker failure must not break search
        log.warning("rerank_failed", error=type(exc).__name__)
        return [(d, 0.0) for d, _ in docs[:top_k]]


async def browse(
    filters: SearchFilters | None = None, limit: int = 20, offset: int = 0
) -> list[SearchHit]:
    """No query text: newest postings matching the filters."""
    async with session_scope() as sess:
        rows = (
            await sess.execute(
                select(*SUMMARY_COLS)
                .where(where(filters))
                .order_by(
                    Posting.published_at.desc().nulls_last(),
                    Posting.first_seen_at.desc(),
                    Posting.id,
                )
                .limit(limit)
                .offset(offset)
            )
        ).all()
    return [_hit(r, 0.0) for r in rows]
