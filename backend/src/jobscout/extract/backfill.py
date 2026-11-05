"""Loader for JobLens Extract backfill files (JSONL).

Each line: {"source": ..., "source_id": ..., "model_version": ..., "schema_version": ...,
"prompt_version": ..., "extraction": {<JobPosting>}}. Rows whose schema_version does not
match ours, or that lack a model_version, are rejected (never silently mixed in).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, update

from jobscout.db.engine import session_scope
from jobscout.db.models import Posting
from jobscout.extract.extractor import derive_fields
from jobscout.schema import SCHEMA_VERSION, JobPosting


def validate_record(rec: dict[str, Any]) -> tuple[JobPosting | None, str | None]:
    if not rec.get("model_version"):
        return None, "missing model_version"
    if rec.get("schema_version") != SCHEMA_VERSION:
        return None, f"schema_version {rec.get('schema_version')!r} != {SCHEMA_VERSION!r}"
    if not rec.get("source") or not rec.get("source_id"):
        return None, "missing source/source_id"
    try:
        return JobPosting.model_validate(rec.get("extraction") or {}), None
    except ValidationError as exc:
        return None, f"invalid extraction: {exc.error_count()} errors"


async def load_backfill_file(path: str | Path) -> dict[str, Any]:
    stats: dict[str, Any] = {"lines": 0, "loaded": 0, "rejected": 0, "not_found": 0, "reasons": {}}
    content = await asyncio.to_thread(Path(path).read_text, encoding="utf-8")
    for line in content.splitlines():
        if not line.strip():
            continue
        stats["lines"] += 1
        rec = json.loads(line)
        jp, err = validate_record(rec)
        if jp is None:
            stats["rejected"] += 1
            stats["reasons"][err] = stats["reasons"].get(err, 0) + 1
            continue
        async with session_scope() as session:
            p = (
                await session.execute(
                    select(Posting).where(
                        Posting.source == rec["source"], Posting.source_id == str(rec["source_id"])
                    )
                )
            ).scalar_one_or_none()
            if p is None:
                stats["not_found"] += 1
                continue
            values = derive_fields(
                jp.model_dump(mode="json"),
                p.raw_text,
                (p.published_at or dt.date.today()).year,
                p.source_remote_flag,
            )
            values.update(
                extraction_status="ok",
                model_version=str(rec["model_version"]),
                schema_version=SCHEMA_VERSION,
                prompt_version=str(rec.get("prompt_version") or "joblens-backfill"),
                extracted_at=dt.datetime.now(dt.UTC),
            )
            await session.execute(update(Posting).where(Posting.id == p.id).values(**values))
            await session.commit()
            stats["loaded"] += 1
    return stats
