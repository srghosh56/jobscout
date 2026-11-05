"""F6 expiry checks: is a posting still online?

- Bundesagentur postings: the v4 details API (404 → gone); Arbeitnow postings: HEAD on
  the job page, falling back to GET when HEAD isn't allowed.
- "Gone" = 404/410, or a redirect away from the job page (e.g. to the listing).
- A posting is marked inactive only after 2 consecutive "gone" results, so one bad
  response never removes a live job. 429/5xx/timeouts are retried with backoff and count
  as "unknown" (no state change except nothing).
- Politeness: one request at a time per host, ≥ ``min_interval_s`` apart, at most
  ``limit`` postings per run, oldest-checked first.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select, update

from jobscout.db.engine import session_scope
from jobscout.db.models import IngestRun, Posting
from jobscout.ingest.sources import BA_DETAILS, BA_HEADERS, encode_refnr
from jobscout.logging import get_logger
from jobscout.settings import get_settings

log = get_logger(__name__)
Status = Literal["alive", "gone", "unknown"]
FAILURES_TO_DEACTIVATE = 2


@dataclass
class HostLimiter:
    min_interval_s: float
    sleep: object = asyncio.sleep
    _last: dict[str, float] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=lambda: defaultdict(asyncio.Lock))

    async def wait(self, host: str) -> None:
        async with self._locks[host]:
            last = self._last.get(host)
            now = time.monotonic()
            if last is not None and now - last < self.min_interval_s:
                await self.sleep(self.min_interval_s - (now - last))  # type: ignore[operator]
            self._last[host] = time.monotonic()


def check_url(p: Posting) -> tuple[str, dict[str, str]]:
    if p.source == "arbeitsagentur":
        return BA_DETAILS.format(code=encode_refnr(p.source_id)), BA_HEADERS
    return p.url, {}


def classify(resp: httpx.Response, original_url: str) -> Status:
    if resp.status_code in (404, 410):
        return "gone"
    if resp.status_code in (429,) or resp.status_code >= 500:
        return "unknown"
    if 200 <= resp.status_code < 300:
        final = urlsplit(str(resp.url))
        orig = urlsplit(original_url)
        if final.netloc == orig.netloc and final.path.rstrip("/") != orig.path.rstrip("/"):
            return "gone"  # redirected to a listing/home page
        return "alive"
    return "unknown"


async def probe(
    client: httpx.AsyncClient,
    limiter: HostLimiter,
    url: str,
    headers: dict[str, str],
    retries: int = 2,
) -> Status:
    host = urlsplit(url).netloc
    for attempt in range(retries + 1):
        await limiter.wait(host)
        try:
            resp = await client.head(url, headers=headers)
            if resp.status_code in (405, 501, 403):  # HEAD not supported → GET
                await limiter.wait(host)
                resp = await client.get(url, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError):
            status: Status = "unknown"
        else:
            status = classify(resp, url)
            if status != "unknown":
                return status
            ra = resp.headers.get("Retry-After")
            if ra and ra.isdigit() and attempt < retries:
                await limiter.sleep(min(int(ra), 60))  # type: ignore[operator]
                continue
        if attempt < retries:
            await limiter.sleep(random.uniform(0, 2 ** (attempt + 1)))  # type: ignore[operator]  # noqa: S311
    return "unknown"


async def run_expiry(
    limit: int | None = 500,
    transport: httpx.AsyncBaseTransport | None = None,
    min_interval_s: float = 2.0,
    sleep: object = asyncio.sleep,
) -> dict[str, int]:
    s = get_settings()
    limit = limit or 500
    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(Posting)
                    .where(Posting.is_active.is_(True), Posting.duplicate_of.is_(None))
                    .order_by(Posting.last_checked_at.asc().nulls_first(), Posting.id)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        run = IngestRun(source="all", kind="expiry")
        session.add(run)
        await session.commit()
        run_id = run.id
    stats = {"checked": 0, "alive": 0, "gone": 0, "unknown": 0, "deactivated": 0}
    limiter = HostLimiter(min_interval_s, sleep=sleep)
    by_host: dict[str, list[Posting]] = defaultdict(list)
    for p in rows:
        by_host[urlsplit(check_url(p)[0]).netloc].append(p)

    async with httpx.AsyncClient(
        headers={"User-Agent": s.http_user_agent},
        timeout=20,
        follow_redirects=True,
        transport=transport,
    ) as client:

        async def host_worker(items: list[Posting]) -> None:  # one request at a time per host
            for p in items:
                url, headers = check_url(p)
                status = await probe(client, limiter, url, headers)
                stats["checked"] += 1
                stats[status] += 1
                now = dt.datetime.now(dt.UTC)
                async with session_scope() as session:
                    if status == "alive":
                        await session.execute(
                            update(Posting)
                            .where(Posting.id == p.id)
                            .values(last_checked_at=now, consecutive_failures=0)
                        )
                    elif status == "gone":
                        fails = p.consecutive_failures + 1
                        values: dict[str, object] = {
                            "last_checked_at": now,
                            "consecutive_failures": fails,
                        }
                        if fails >= FAILURES_TO_DEACTIVATE:
                            values.update(is_active=False, inactive_since=now)
                            stats["deactivated"] += 1
                        await session.execute(
                            update(Posting).where(Posting.id == p.id).values(**values)
                        )
                    await session.commit()

        await asyncio.gather(*(host_worker(items) for items in by_host.values()))

    async with session_scope() as session:
        await session.execute(
            update(IngestRun)
            .where(IngestRun.id == run_id)
            .values(
                finished_at=dt.datetime.now(dt.UTC),
                status="ok",
                fetched=stats["checked"],
                updated=stats["deactivated"],
                stats=stats,
            )
        )
        await session.commit()
    log.info("expiry_done", **stats)
    return stats
