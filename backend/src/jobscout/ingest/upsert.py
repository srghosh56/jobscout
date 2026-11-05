"""Idempotent upsert keyed by (source, source_id) with content-hash change detection.

- new posting                → INSERT, extraction_status='pending'
- same key, same content_hash → only last_seen_at / is_active refreshed ("unchanged")
- same key, new content_hash  → content replaced, extraction + embedding reset ("updated")

Running the same batch twice yields 0 inserts and 0 updates the second time.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from jobscout.db.models import Posting
from jobscout.ingest.cleaning import CleanPosting


@dataclass
class UpsertResult:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0


def _row(p: CleanPosting) -> dict[str, object]:
    return {
        "id": p.id,
        "source": p.source,
        "source_id": p.source_id,
        "url": p.url,
        "title": p.title,
        "company": p.company,
        "raw_text": p.raw_text,
        "posting_language": p.posting_language,
        "city": p.city,
        "cities": p.cities,
        "country": p.country,
        "locations": [{"city": c, "country": p.country} for c in p.cities],
        "job_family": p.job_family,
        "source_remote_flag": p.source_remote_flag,
        "published_at": p.published_at,
        "content_hash": p.content_hash,
    }


async def upsert_postings(session: AsyncSession, batch: Sequence[CleanPosting]) -> UpsertResult:
    result = UpsertResult()
    if not batch:
        return result
    # Deduplicate within the batch (last one wins).
    by_key = {(p.source, p.source_id): p for p in batch}
    keys = list(by_key)
    existing = {
        (r.source, r.source_id): r.content_hash
        for r in (
            await session.execute(
                select(Posting.source, Posting.source_id, Posting.content_hash).where(
                    tuple_(Posting.source, Posting.source_id).in_(keys)
                )
            )
        ).all()
    }
    new_rows, changed_rows, unchanged_keys = [], [], []
    for key, p in by_key.items():
        if key not in existing:
            new_rows.append(_row(p))
        elif existing[key] != p.content_hash:
            changed_rows.append(_row(p))
        else:
            unchanged_keys.append(key)

    if new_rows:
        stmt = insert(Posting).values(new_rows)
        # ON CONFLICT DO NOTHING keeps this safe under a concurrent ingest.
        stmt = stmt.on_conflict_do_nothing(index_elements=["source", "source_id"])
        res = await session.execute(stmt)
        result.inserted = int(getattr(res, "rowcount", 0) or 0)

    if changed_rows:
        stmt = insert(Posting).values(changed_rows)
        ex = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            index_elements=["source", "source_id"],
            set_={
                "url": ex.url,
                "title": ex.title,
                "company": ex.company,
                "raw_text": ex.raw_text,
                "posting_language": ex.posting_language,
                "city": ex.city,
                "cities": ex.cities,
                "locations": ex.locations,
                "job_family": ex.job_family,
                "source_remote_flag": ex.source_remote_flag,
                "published_at": ex.published_at,
                "content_hash": ex.content_hash,
                "extraction_status": "pending",
                "embedding": None,
                "embedded_hash": None,
                "updated_at": func.now(),
                "last_seen_at": func.now(),
                "is_active": True,
                "consecutive_failures": 0,
                "inactive_since": None,
            },
            where=Posting.content_hash != ex.content_hash,
        )
        res = await session.execute(stmt)
        result.updated = int(getattr(res, "rowcount", 0) or 0)

    if unchanged_keys:
        await session.execute(
            update(Posting)
            .where(tuple_(Posting.source, Posting.source_id).in_(unchanged_keys))
            .values(last_seen_at=func.now(), is_active=True, inactive_since=None)
        )
        result.unchanged = len(unchanged_keys)
    await session.commit()
    return result
