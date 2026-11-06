"""Ingestion orchestrator: fetch → clean → upsert (batched) → cross-source dedup,
with one ``ingest_runs`` row per source holding the statistics."""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import func, select, update

from jobscout.db.engine import session_scope
from jobscout.db.models import IngestRun, Posting
from jobscout.ingest.cleaning import CleanPosting, arbeitnow_site_country
from jobscout.ingest.dedup import DocForDedup, find_duplicates
from jobscout.ingest.http import PoliteClient
from jobscout.ingest.sources import (
    DEFAULT_BA_CITIES,
    DEFAULT_BA_QUERIES,
    BAConfig,
    fetch_arbeitnow,
    fetch_arbeitsagentur,
)
from jobscout.ingest.upsert import upsert_postings
from jobscout.logging import get_logger
from jobscout.settings import get_settings

log = get_logger(__name__)
SOURCES = ("arbeitsagentur", "arbeitnow")
BATCH = 100


def make_client(offline: bool = False, **kw: Any) -> PoliteClient:
    s = get_settings()
    return PoliteClient(
        user_agent=s.http_user_agent,
        min_interval_s=s.http_min_interval_s,
        cache_dir=s.http_cache_dir,
        offline=offline,
        **kw,
    )


def source_stream(
    name: str, client: PoliteClient, limit: int | None
) -> AsyncIterator[CleanPosting]:
    if name == "arbeitsagentur":
        return fetch_arbeitsagentur(
            client, BAConfig(queries=DEFAULT_BA_QUERIES, cities=DEFAULT_BA_CITIES), limit=limit
        )
    if name == "arbeitnow":
        return fetch_arbeitnow(client, limit=limit)
    raise ValueError(f"unknown source {name}")


async def ingest_source(
    name: str, stream: AsyncIterator[CleanPosting], client: PoliteClient | None = None
) -> dict[str, Any]:
    async with session_scope() as session:
        run = IngestRun(source=name, kind="ingest")
        session.add(run)
        await session.commit()
        run_id = run.id
    totals = {"fetched": 0, "inserted": 0, "updated": 0, "unchanged": 0, "failed": 0}
    status = "ok"
    batch: list[CleanPosting] = []

    async def flush() -> None:
        async with session_scope() as session:
            r = await upsert_postings(session, batch)
        totals["inserted"] += r.inserted
        totals["updated"] += r.updated
        totals["unchanged"] += r.unchanged
        batch.clear()

    try:
        async for posting in stream:
            totals["fetched"] += 1
            batch.append(posting)
            if len(batch) >= BATCH:
                await flush()
        if batch:
            await flush()
    except Exception as exc:
        status = "failed"
        totals["failed"] += 1
        log.error("ingest_failed", source=name, error=repr(exc)[:300])
        if batch:
            await flush()
    async with session_scope() as session:
        await session.execute(
            update(IngestRun)
            .where(IngestRun.id == run_id)
            .values(
                finished_at=dt.datetime.now(dt.UTC),
                status=status,
                fetched=totals["fetched"],
                inserted=totals["inserted"],
                updated=totals["updated"],
                unchanged=totals["unchanged"],
                failed=totals["failed"],
                stats={"http": dict(client.stats) if client else {}},
            )
        )
        await session.commit()
    from jobscout.metrics import INGEST_POSTINGS

    INGEST_POSTINGS.labels(source=name, kind="fetched").set(totals["fetched"])
    INGEST_POSTINGS.labels(source=name, kind="inserted").set(totals["inserted"])
    return {"run_id": run_id, "source": name, "status": status, **totals}


async def run_dedup(threshold: float = 0.85) -> int:
    """Recompute cross-source duplicates over active postings. Returns #duplicates."""
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Posting.id, Posting.title, Posting.raw_text, Posting.first_seen_at).where(
                    Posting.is_active.is_(True)
                )
            )
        ).all()
        docs = [
            DocForDedup(
                id=r.id, text=f"{r.title}\n{r.raw_text}", order_key=r.first_seen_at.isoformat()
            )
            for r in rows
        ]
        dups = find_duplicates(docs, threshold=threshold)
        await session.execute(update(Posting).values(duplicate_of=None))
        for dup_id, canon in dups.items():
            await session.execute(
                update(Posting).where(Posting.id == dup_id).values(duplicate_of=canon)
            )
        await session.commit()
    return len(dups)


async def prune_out_of_scope() -> int:
    """Delete postings whose location is outside the product scope (Germany).
    Uses the same classifier as ingestion, so a rule change is applied retroactively."""
    from sqlalchemy import delete

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Posting.id, Posting.locations, Posting.city, Posting.country).where(
                    Posting.source == "arbeitnow"
                )
            )
        ).all()
        foreign = [r.id for r in rows if r.country not in ("Germany", None)]
        if foreign:
            await session.execute(
                update(Posting).where(Posting.duplicate_of.in_(foreign)).values(duplicate_of=None)
            )
            await session.execute(delete(Posting).where(Posting.id.in_(foreign)))
            await session.commit()
    return len(foreign)


async def retire_foreign_sites() -> int:
    """Take postings from Arbeitnow's foreign sites out of the product, keeping their rows.

    A German posting that was marked as a copy of a foreign one becomes the original again."""
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Posting.id, Posting.url).where(
                    Posting.source == "arbeitnow", Posting.is_active.is_(True)
                )
            )
        ).all()
        foreign = {r.id: c for r in rows if (c := arbeitnow_site_country(r.url))}
        for posting_id, country in foreign.items():
            await session.execute(
                update(Posting)
                .where(Posting.id == posting_id)
                .values(country=country, is_active=False, inactive_since=func.now())
            )
        await session.execute(
            update(Posting)
            .where(Posting.duplicate_of.in_(list(foreign)), Posting.id.not_in(list(foreign)))
            .values(duplicate_of=None)
        )
        await session.commit()
    return len(foreign)


async def run_ingest(
    sources: list[str] | None = None, limit: int | None = None, offline: bool = False
) -> dict[str, Any]:
    results = []
    for name in sources or list(SOURCES):
        async with make_client(offline=offline) as client:
            results.append(await ingest_source(name, source_stream(name, client, limit), client))
    pruned = await prune_out_of_scope()
    duplicates = await run_dedup()
    async with session_scope() as session:
        if results:
            await session.execute(
                update(IngestRun)
                .where(IngestRun.id == results[-1]["run_id"])
                .values(duplicates=duplicates)
            )
            await session.commit()
    return {"runs": results, "duplicates": duplicates, "pruned_out_of_scope": pruned}
