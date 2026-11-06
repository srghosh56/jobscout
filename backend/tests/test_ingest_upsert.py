from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select, update

from jobscout.db.engine import session_scope
from jobscout.db.models import IngestRun, Posting
from jobscout.ingest.cleaning import CleanPosting
from jobscout.ingest.run import ingest_source, retire_foreign_sites, run_dedup
from jobscout.ingest.upsert import upsert_postings
from tests.fixtures.synthetic import LOREM_DE, LOREM_EN

pytestmark = pytest.mark.db


def make(sid: str, text: str = LOREM_EN, source: str = "arbeitnow") -> CleanPosting:
    return CleanPosting(
        source=source,
        source_id=sid,
        url=f"https://example.com/{sid}",
        title="ML Engineer",
        company="Fictional GmbH",
        raw_text=text,
        city="Berlin",
        cities=["Berlin"],
    )


async def test_retire_foreign_sites_keeps_rows_and_frees_german_copies(clean_db: None) -> None:
    german = make("german")
    copy = make("german-copy")
    foreign = make("foreign")
    foreign.url = "https://www.arbeitnow.ch/jobs/companies/fictional/engineer-1"
    async with session_scope() as s:
        await upsert_postings(s, [german, copy, foreign])
        foreign_id = (
            await s.execute(select(Posting.id).where(Posting.source_id == "foreign"))
        ).scalar_one()
        await s.execute(
            update(Posting)
            .where(Posting.source_id == "german-copy")
            .values(duplicate_of=foreign_id)
        )
        await s.commit()

    assert await retire_foreign_sites() == 1
    assert await retire_foreign_sites() == 0

    async with session_scope() as s:
        rows = {p.source_id: p for p in (await s.execute(select(Posting))).scalars()}
    assert len(rows) == 3
    assert rows["foreign"].is_active is False and rows["foreign"].country == "Switzerland"
    assert rows["foreign"].inactive_since is not None
    assert rows["german"].is_active is True
    assert rows["german-copy"].is_active is True and rows["german-copy"].duplicate_of is None


async def test_upsert_is_idempotent_and_detects_changes(clean_db: None) -> None:
    batch = [make("a"), make("b")]
    async with session_scope() as s:
        r1 = await upsert_postings(s, batch)
    async with session_scope() as s:
        r2 = await upsert_postings(s, batch)
    assert (r1.inserted, r1.updated, r1.unchanged) == (2, 0, 0)
    assert (r2.inserted, r2.updated, r2.unchanged) == (0, 0, 2)

    async with session_scope() as s:
        await s.execute(
            Posting.__table__.update()
            .where(Posting.source_id == "a")
            .values(extraction_status="ok")
        )
        await s.commit()
        r3 = await upsert_postings(s, [make("a", text=LOREM_EN + " Changed."), make("c")])
    assert (r3.inserted, r3.updated, r3.unchanged) == (1, 1, 0)
    async with session_scope() as s:
        row = (await s.execute(select(Posting).where(Posting.source_id == "a"))).scalar_one()
        assert row.extraction_status == "pending"  # re-extract after content change
        assert row.raw_text.endswith("Changed.")
        assert (await s.execute(select(func.count()).select_from(Posting))).scalar_one() == 3


async def test_tsvector_follows_posting_language(clean_db: None) -> None:
    async with session_scope() as s:
        await upsert_postings(s, [make("en1"), make("de1", text=LOREM_DE, source="arbeitsagentur")])
        rows = {r.source_id: r for r in (await s.execute(select(Posting))).scalars()}
    assert rows["en1"].posting_language == "en" and rows["en1"].tsv_en is not None
    assert rows["en1"].tsv_de is None
    assert rows["de1"].posting_language == "de" and rows["de1"].tsv_de is not None


async def test_ingest_source_records_run_stats_and_dedup(clean_db: None) -> None:
    async def stream() -> Any:
        yield make("x1")
        yield make("x2")  # identical text → near-duplicate
        yield make("x3", text=LOREM_DE, source="arbeitsagentur")

    stats = await ingest_source("arbeitnow", stream())
    assert stats["fetched"] == 3 and stats["inserted"] == 3 and stats["status"] == "ok"
    assert await run_dedup() == 1
    async with session_scope() as s:
        run = (await s.execute(select(IngestRun))).scalar_one()
        assert run.status == "ok" and run.inserted == 3 and run.finished_at is not None
        dups = (await s.execute(select(Posting.id).where(Posting.duplicate_of.is_not(None)))).all()
        assert len(dups) == 1


async def test_ingest_source_marks_failed_runs(clean_db: None) -> None:
    async def broken() -> Any:
        yield make("ok1")
        raise RuntimeError("API down")

    stats = await ingest_source("arbeitnow", broken())
    assert stats["status"] == "failed" and stats["inserted"] == 1
