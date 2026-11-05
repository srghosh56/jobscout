"""Batch extraction runner: pending postings → extractor → typed columns."""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from typing import Any

from sqlalchemy import ColumnElement, and_, case, func, or_, select, update

from jobscout.db.engine import session_scope
from jobscout.db.models import IngestRun, Posting
from jobscout.extract.extractor import PROMPT_VERSION, derive_fields, extract_text
from jobscout.llm import get_chat_model, model_version
from jobscout.logging import get_logger
from jobscout.schema import SCHEMA_VERSION

log = get_logger(__name__)

# Families most relevant to the target users are extracted first.
FAMILY_PRIORITY = {"data_ml": 0, "software_engineering": 1, "devops_cloud": 2, "security": 3}


async def extract_one(posting_id: str, model: Any, model_id: str) -> dict[str, Any]:
    async with session_scope() as session:
        p = await session.get(Posting, posting_id)
        if p is None:
            return {"ok": False}
        text = f"{p.title}\n{p.company or ''}\n{p.raw_text}"
        outcome = await extract_text(model, model_id, text, session=session)
        year = (p.published_at or dt.date.today()).year
        values: dict[str, Any]
        if outcome.ok and outcome.data is not None:
            values = derive_fields(outcome.data, p.raw_text, year, p.source_remote_flag)
            values.update(extraction_status="ok", extraction_error=None)
        else:
            values = {
                "extraction_status": "failed",
                "extraction_error": (outcome.error or "")[:500],
            }
        values.update(
            model_version=model_id,
            schema_version=SCHEMA_VERSION,
            prompt_version=PROMPT_VERSION,
            extracted_at=dt.datetime.now(dt.UTC),
        )
        await session.execute(update(Posting).where(Posting.id == posting_id).values(**values))
        await session.commit()
    return {
        "ok": outcome.ok,
        "cached": outcome.cached,
        "tokens_in": outcome.tokens_in,
        "tokens_out": outcome.tokens_out,
    }


RULES_MODEL_VERSION = "rules:jobscout-rules-v2"


async def run_rules_extraction(limit: int | None = None, refresh: bool = False) -> dict[str, Any]:
    """Fast deterministic provisional extraction for pending postings (no LLM).
    Rows get model_version='rules:…' and are upgraded later by the LLM runner."""
    from jobscout.extract.rules import rule_based_extract
    from jobscout.schema import JobPosting

    async with session_scope() as session:
        todo = Posting.extraction_status == "pending"
        if refresh:  # re-run on rows produced by an older rules version
            todo = or_(todo, Posting.model_version.like("rules:%"))
        q = select(Posting).where(todo, Posting.duplicate_of.is_(None))
        if limit:
            q = q.limit(limit)
        rows = (await session.execute(q)).scalars().all()
        n = 0
        for p in rows:
            data = JobPosting.model_validate(
                rule_based_extract(f"{p.title}\n{p.raw_text}")
            ).model_dump(mode="json")
            values = derive_fields(
                data, p.raw_text, (p.published_at or dt.date.today()).year, p.source_remote_flag
            )
            values.update(
                extraction_status="ok",
                extraction_error=None,
                model_version=RULES_MODEL_VERSION,
                schema_version=SCHEMA_VERSION,
                prompt_version=RULES_MODEL_VERSION.split("-")[-1],
                extracted_at=dt.datetime.now(dt.UTC),
            )
            await session.execute(update(Posting).where(Posting.id == p.id).values(**values))
            n += 1
        await session.commit()
    return {"rules_extracted": n, "model_version": RULES_MODEL_VERSION}


async def run_extraction(
    limit: int | None = None,
    concurrency: int = 2,
    statuses: tuple[str, ...] = ("pending",),
    upgrade_rules: bool = True,
) -> dict[str, Any]:
    model_id = model_version("extract")
    model = get_chat_model("extract")
    async with session_scope() as session:
        order = case(FAMILY_PRIORITY, value=Posting.job_family, else_=9)
        todo: ColumnElement[bool] = Posting.extraction_status.in_(statuses)
        if upgrade_rules:
            todo = or_(
                todo,
                and_(
                    Posting.extraction_status == "ok",
                    Posting.model_version.like("rules:%"),
                ),
            )
        q = (
            select(Posting.id)
            .where(todo, Posting.duplicate_of.is_(None), Posting.is_active.is_(True))
            .order_by(order, Posting.source.asc(), func.random())
        )
        if limit:
            q = q.limit(limit)
        ids = [r[0] for r in (await session.execute(q)).all()]
        run = IngestRun(source="llm", kind="extract")
        session.add(run)
        await session.commit()
        run_id = run.id

    sem = asyncio.Semaphore(concurrency)
    stats: dict[str, Any] = {
        "total": len(ids),
        "ok": 0,
        "failed": 0,
        "cached": 0,
        "tokens_in": 0,
        "tokens_out": 0,
    }
    t0 = time.monotonic()

    async def worker(pid: str) -> None:
        async with sem:
            try:
                r = await extract_one(pid, model, model_id)
            except Exception as exc:
                log.error("extract_crash", posting_id=pid, error=type(exc).__name__)
                r = {"ok": False}
            stats["ok" if r.get("ok") else "failed"] += 1
            stats["cached"] += int(bool(r.get("cached")))
            stats["tokens_in"] += int(r.get("tokens_in", 0))
            stats["tokens_out"] += int(r.get("tokens_out", 0))
            done = stats["ok"] + stats["failed"]
            if done % 25 == 0:
                log.info("extract_progress", done=done, total=len(ids), ok=stats["ok"])

    await asyncio.gather(*(worker(pid) for pid in ids))
    stats["seconds"] = round(time.monotonic() - t0, 1)
    stats["model_version"] = model_id
    async with session_scope() as session:
        await session.execute(
            update(IngestRun)
            .where(IngestRun.id == run_id)
            .values(
                finished_at=dt.datetime.now(dt.UTC),
                status="ok" if stats["failed"] <= 0.05 * max(1, stats["total"]) else "degraded",
                fetched=stats["total"],
                updated=stats["ok"],
                failed=stats["failed"],
                stats=stats,
            )
        )
        await session.commit()
    return stats
