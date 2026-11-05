from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from jobscout.ingest.http import OfflineCacheMiss, PoliteClient
from jobscout.ingest.sources import (
    ARBEITNOW_URL,
    BA_DETAILS,
    BA_SEARCH,
    BAConfig,
    encode_refnr,
    fetch_arbeitnow,
    fetch_arbeitsagentur,
)
from tests.fixtures.synthetic import arbeitnow_page, ba_detail, ba_search_page


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, s: float) -> None:
        self.calls.append(s)


def client(tmp_path: Path | None = None, **kw: object) -> PoliteClient:
    return PoliteClient(user_agent="test-agent", min_interval_s=0.0, cache_dir=tmp_path, **kw)  # type: ignore[arg-type]


@respx.mock
async def test_retries_on_429_with_retry_after_then_succeeds() -> None:
    route = respx.get("https://api.example.com/x").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(503),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    sleep = FakeSleep()
    async with client(sleep=sleep) as c:
        assert await c.get_json("https://api.example.com/x") == {"ok": True}
    assert route.call_count == 3
    assert sleep.calls[0] == 7.0  # Retry-After honoured
    assert c.stats["retries"] == 2
    assert route.calls[0].request.headers["User-Agent"] == "test-agent"


@respx.mock
async def test_gives_up_after_max_retries() -> None:
    respx.get("https://api.example.com/x").mock(return_value=httpx.Response(500))
    async with client(sleep=FakeSleep(), max_retries=2) as c:
        with pytest.raises(httpx.HTTPStatusError):
            await c.get_json("https://api.example.com/x")


@respx.mock
async def test_timeout_retry() -> None:
    respx.get("https://api.example.com/t").mock(
        side_effect=[httpx.ConnectTimeout("t"), httpx.Response(200, json=[1])]
    )
    async with client(sleep=FakeSleep()) as c:
        assert await c.get_json("https://api.example.com/t") == [1]


def test_backoff_is_bounded_with_jitter() -> None:
    c = client(base_backoff_s=1, max_backoff_s=10)
    for attempt in range(10):
        assert 0 <= c.backoff_delay(attempt) <= 10
    assert c.backoff_delay(0, "bogus") <= 1


@respx.mock
async def test_cache_and_offline_replay(tmp_path: Path) -> None:
    route = respx.get("https://api.example.com/c").mock(
        return_value=httpx.Response(200, json={"v": 1})
    )
    async with client(tmp_path) as c:
        await c.get_json("https://api.example.com/c", params={"a": 1})
        await c.get_json("https://api.example.com/c", params={"a": 1})
    assert route.call_count == 1
    async with client(tmp_path, offline=True) as c:
        assert await c.get_json("https://api.example.com/c", params={"a": 1}) == {"v": 1}
        with pytest.raises(OfflineCacheMiss):
            await c.get_json("https://api.example.com/c", params={"a": 2})


async def test_rate_limit_waits_between_requests_per_host() -> None:
    sleep = FakeSleep()
    c = PoliteClient(user_agent="t", min_interval_s=10.0, sleep=sleep)
    await c._wait_turn("a.example")
    await c._wait_turn("a.example")
    await c._wait_turn("b.example")
    assert len(sleep.calls) == 1 and 9 < sleep.calls[0] <= 10


@respx.mock
async def test_fetch_arbeitsagentur_parses_and_dedups_refnrs() -> None:
    refs = ["R-1", "R-2", "R-1"]
    respx.get(BA_SEARCH).mock(return_value=httpx.Response(200, json=ba_search_page(refs)))
    for r in set(refs):
        respx.get(BA_DETAILS.format(code=encode_refnr(r))).mock(
            return_value=httpx.Response(200, json=ba_detail(r))
        )
    cfg = BAConfig(queries=["Softwareentwickler"], cities=["Hamburg"], max_pages=1)
    async with client() as c:
        out = [p async for p in fetch_arbeitsagentur(c, cfg)]
    assert [p.source_id for p in out] == ["R-1", "R-2"]
    p = out[0]
    assert p.city == "Hamburg" and p.posting_language == "de"
    assert p.url.endswith("/jobdetail/R-1")
    assert p.job_family == "software_engineering"
    assert p.source_remote_flag is True


@respx.mock
async def test_fetch_arbeitsagentur_skips_short_texts_and_respects_limit() -> None:
    respx.get(BA_SEARCH).mock(
        return_value=httpx.Response(200, json=ba_search_page(["A", "B", "C"]))
    )
    respx.get(BA_DETAILS.format(code=encode_refnr("A"))).mock(
        return_value=httpx.Response(200, json=ba_detail("A", text="too short"))
    )
    for r in ("B", "C"):
        respx.get(BA_DETAILS.format(code=encode_refnr(r))).mock(
            return_value=httpx.Response(200, json=ba_detail(r))
        )
    cfg = BAConfig(queries=["x"], cities=["y"], max_pages=1)
    async with client() as c:
        out = [p async for p in fetch_arbeitsagentur(c, cfg, limit=1)]
    assert [p.source_id for p in out] == ["B"]


@respx.mock
async def test_fetch_arbeitnow_paginates_and_strips_boilerplate() -> None:
    respx.get(ARBEITNOW_URL, params={"page": "1"}).mock(
        return_value=httpx.Response(200, json=arbeitnow_page(2, next_page=True))
    )
    respx.get(ARBEITNOW_URL, params={"page": "2"}).mock(
        return_value=httpx.Response(200, json=arbeitnow_page(1, next_page=False))
    )
    async with client() as c:
        out = [p async for p in fetch_arbeitnow(c)]
    assert len(out) == 3
    assert "Arbeitnow" not in out[0].raw_text
    assert out[1].city == "München"
    assert out[0].published_at is not None
    assert out[0].posting_language == "en"
