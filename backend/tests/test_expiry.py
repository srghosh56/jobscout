from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import select

from jobscout.db.engine import session_scope
from jobscout.db.models import Posting
from jobscout.expiry import HostLimiter, classify, run_expiry

pytestmark = pytest.mark.db


async def nosleep(_s: float) -> None:
    return None


def handler_factory(routes: dict[str, Any], calls: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url}")
        spec = routes.get(str(request.url).split("?")[0], 200)
        if callable(spec):
            return spec(request)
        return httpx.Response(spec)

    return httpx.MockTransport(handler)


async def test_two_consecutive_failures_deactivate(corpus: None) -> None:
    gone = "https://example.org/jobs/an-0000000001"
    calls: list[str] = []
    t = handler_factory({gone: 404}, calls)
    s1 = await run_expiry(limit=50, transport=t, min_interval_s=0, sleep=nosleep)
    assert s1["gone"] == 1 and s1["deactivated"] == 0
    async with session_scope() as s:
        p = await s.get(Posting, "an-0000000001")
        assert p is not None and p.is_active and p.consecutive_failures == 1 and p.last_checked_at
    s2 = await run_expiry(limit=50, transport=t, min_interval_s=0, sleep=nosleep)
    assert s2["deactivated"] == 1
    async with session_scope() as s:
        p = await s.get(Posting, "an-0000000001")
        assert p is not None and not p.is_active and p.inactive_since is not None
        others = (
            (
                await s.execute(
                    select(Posting).where(
                        Posting.consecutive_failures == 0,
                        Posting.is_active,
                        Posting.duplicate_of.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert all(o.last_checked_at for o in others)


async def test_transient_errors_do_not_count_and_success_resets(corpus: None) -> None:
    url = "https://example.org/jobs/an-0000000003"
    calls: list[str] = []
    async with session_scope() as s:
        await s.execute(
            Posting.__table__.update()
            .where(Posting.id == "an-0000000003")
            .values(consecutive_failures=1)
        )
        await s.commit()
    seq = iter(
        [
            httpx.Response(503),
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(503),
        ]
    )
    t = handler_factory({url: lambda r: next(seq)}, calls)
    st = await run_expiry(limit=50, transport=t, min_interval_s=0, sleep=nosleep)
    assert st["unknown"] == 1
    async with session_scope() as s:
        assert (await s.get(Posting, "an-0000000003")).consecutive_failures == 1  # type: ignore[union-attr]
    t2 = handler_factory({}, [])
    await run_expiry(limit=50, transport=t2, min_interval_s=0, sleep=nosleep)
    async with session_scope() as s:
        assert (await s.get(Posting, "an-0000000003")).consecutive_failures == 0  # type: ignore[union-attr]


async def test_ba_uses_details_api_and_head_falls_back_to_get(corpus: None) -> None:
    calls: list[str] = []

    def no_head(r: httpx.Request) -> httpx.Response:
        return httpx.Response(405 if r.method == "HEAD" else 200)

    t = handler_factory({"https://example.org/jobs/an-0000000006": no_head}, calls)
    await run_expiry(limit=50, transport=t, min_interval_s=0, sleep=nosleep)
    assert any("rest.arbeitsagentur.de" in c and "jobdetails" in c for c in calls)
    assert "GET https://example.org/jobs/an-0000000006" in calls


def test_classify_redirect_to_listing_is_gone() -> None:
    req = httpx.Request("GET", "https://www.arbeitnow.com/jobs")
    r = httpx.Response(200, request=req)
    assert classify(r, "https://www.arbeitnow.com/jobs/companies/x/y") == "gone"
    assert (
        classify(
            httpx.Response(200, request=httpx.Request("GET", "https://a.b/x")), "https://a.b/x"
        )
        == "alive"
    )
    assert classify(httpx.Response(302, request=req), "https://a.b/x") == "unknown"


async def test_host_limiter_spaces_requests() -> None:
    waits: list[float] = []

    async def rec(s: float) -> None:
        waits.append(s)

    lim = HostLimiter(5.0, sleep=rec)
    await lim.wait("h")
    await lim.wait("h")
    await lim.wait("other")
    assert len(waits) == 1 and waits[0] > 4
