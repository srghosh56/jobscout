from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from jobscout.agent.graph import Agent
from jobscout.api.app import create_app
from jobscout.api.ratelimit import RateLimiter, TokenBucket
from jobscout.llm import FakeChatModel
from jobscout.settings import get_settings
from tests.fixtures.pdf import make_pdf
from tests.test_agent import Script, call

pytestmark = pytest.mark.db


@pytest.fixture
async def app(corpus: None) -> AsyncIterator[FastAPI]:
    a = create_app()
    a.state.agent = Agent(
        model=FakeChatModel(
            responder=Script(
                [[call("search_postings", {"query": "RAG", "filters": {"city": "Munich"}})]],
                ["The ML Engineer role fits [an-0000000001]."],
            )
        )
    )
    yield a


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
async def key(client: httpx.AsyncClient) -> str:
    from jobscout.api.auth import create_api_key

    return await create_api_key("tests")


def H(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


async def test_health_ready_metrics_and_request_id(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz", headers={"X-Request-ID": "req-12345678"})
    assert r.json()["status"] == "ok" and r.headers["x-request-id"] == "req-12345678"
    r = await client.get("/healthz", headers={"X-Request-ID": "bad id with spaces"})
    assert r.headers["x-request-id"] != "bad id with spaces"
    r = await client.get("/readyz")
    assert r.status_code == 200 and r.json()["checks"]["database"] == "ok"
    assert r.json()["checks"]["active_postings"] == 9
    r = await client.get("/metrics")
    assert "jobscout_http_request_duration_seconds" in r.text


async def test_auth_required_and_bearer_works(client: httpx.AsyncClient, key: str) -> None:
    assert (await client.post("/api/search", json={"query": "x"})).status_code == 401
    assert (
        await client.post(
            "/api/search", json={"query": "x"}, headers={"X-API-Key": "js_deadbeef_nope"}
        )
    ).status_code == 401
    assert (
        await client.post("/api/search", json={"query": "x"}, headers={"X-API-Key": "garbage"})
    ).status_code == 401
    r = await client.post(
        "/api/search", json={"query": "rag"}, headers={"Authorization": f"Bearer {key}"}
    )
    assert r.status_code == 200


async def test_demo_key_flow_and_rate_limit(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "demo_rate_limit_capacity", 2)
    monkeypatch.setattr(s, "demo_rate_limit_refill_per_s", 0.01)
    r = await client.post("/api/demo-key")
    assert r.status_code == 200
    demo = r.json()["api_key"]
    codes = [
        (await client.post("/api/search", json={"query": "rag"}, headers=H(demo))).status_code
        for _ in range(3)
    ]
    assert codes == [200, 200, 429]
    last = await client.post("/api/search", json={"query": "rag"}, headers=H(demo))
    assert int(last.headers["Retry-After"]) >= 1
    for _ in range(2):
        await client.post("/api/demo-key")
    assert (await client.post("/api/demo-key")).status_code == 429  # 3 per IP per hour


async def test_search_and_browse(client: httpx.AsyncClient, key: str) -> None:
    r = await client.post(
        "/api/search",
        json={
            "query": "machine learning RAG",
            "filters": {"city": "München", "max_years": 3},
            "mode": "hybrid",
        },
        headers=H(key),
    )
    body = r.json()
    assert body["results"][0]["id"] == "an-0000000001" and body["mode"] == "hybrid"
    r = await client.post(
        "/api/search", json={"query": "", "filters": {"english_sufficient": False}}, headers=H(key)
    )
    assert r.json()["mode"] == "browse" and [x["id"] for x in r.json()["results"]] == [
        "ba-0000000004"
    ]
    r = await client.post(
        "/api/search", json={"query": "x", "filters": {"bogus": 1}}, headers=H(key)
    )
    assert r.status_code == 422


async def test_posting_detail_has_no_full_text(client: httpx.AsyncClient, key: str) -> None:
    r = await client.get("/api/postings/an-0000000001", headers=H(key))
    d = r.json()
    assert d["excerpt"] == "" and d["bluecard"]["status"] == "meets_standard"
    assert "not legal advice" in d["bluecard"]["disclaimer"].lower()
    assert (await client.get("/api/postings/an-ffffffffff", headers=H(key))).status_code == 404


async def test_chat_streams_sse_events(client: httpx.AsyncClient, key: str) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    async with client.stream(
        "POST", "/api/chat", json={"message": "RAG roles in Munich?"}, headers=H(key)
    ) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        ev = None
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                ev = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and ev:
                events.append((ev, json.loads(line.split(":", 1)[1])))
    names = [e for e, _ in events]
    assert (
        names[0] == "tool_call"
        and "tool_result" in names
        and "token" in names
        and "citation" in names
    )
    done = events[-1]
    assert done[0] == "done" and done[1]["citations"] == ["an-0000000001"]
    assert done[1]["trace_id"] and done[1]["message_id"] and "tool_results" not in done[1]


async def test_chat_validation(client: httpx.AsyncClient, key: str) -> None:
    assert (await client.post("/api/chat", json={"message": ""}, headers=H(key))).status_code == 422
    assert (
        await client.post(
            "/api/chat", json={"message": "x", "session_id": "../etc"}, headers=H(key)
        )
    ).status_code == 422


async def test_cv_match_upload_store_delete_and_no_cv_in_logs(
    client: httpx.AsyncClient, key: str, capsys: pytest.CaptureFixture[str]
) -> None:
    marker = "PLATYPUS-4412"
    pdf = make_pdf(
        [
            "Data Scientist",
            "3 years of experience with Python, RAG and PyTorch.",
            "English fluent, German A2 (basic).",
            "Based in Munich.",
            f"Codename {marker}",
        ]
    )
    r = await client.post(
        "/api/cv/match",
        files={"file": ("cv.pdf", pdf, "application/pdf")},
        data={"top_k": "3"},
        headers=H(key),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stored_profile_id"] is None and "not stored" in body["privacy"]
    assert body["matches"][0]["posting"]["id"] == "an-0000000001"
    r = await client.post(
        "/api/cv/match",
        data={
            "cv_text": "Python developer with 2 years of experience in Berlin, English fluent. "
            * 2,
            "store_profile": "true",
        },
        headers=H(key),
    )
    pid = r.json()["stored_profile_id"]
    assert pid
    assert (await client.delete(f"/api/cv/profiles/{pid}", headers=H(key))).status_code == 204
    assert (await client.delete(f"/api/cv/profiles/{pid}", headers=H(key))).status_code == 404
    out = capsys.readouterr()
    assert marker not in out.out + out.err
    r = await client.post("/api/cv/match", data={"cv_text": "too short"}, headers=H(key))
    assert r.status_code == 422
    assert (await client.post("/api/cv/match", headers=H(key))).status_code == 422


async def test_saved_searches_and_feedback(client: httpx.AsyncClient, key: str) -> None:
    r = await client.post(
        "/api/saved-searches",
        json={"name": "ML Munich", "query": "machine learning", "filters": {"city": "München"}},
        headers=H(key),
    )
    assert r.status_code == 201
    sid = r.json()["id"]
    assert r.json()["feed_url"].endswith(f"/api/feeds/{sid}.xml")
    lst = (await client.get("/api/saved-searches", headers=H(key))).json()
    assert [x["id"] for x in lst] == [sid]
    other = await __import__("jobscout.api.auth", fromlist=["x"]).create_api_key("other")
    assert (await client.get("/api/saved-searches", headers=H(other))).json() == []
    assert (await client.delete(f"/api/saved-searches/{sid}", headers=H(other))).status_code == 404
    assert (await client.delete(f"/api/saved-searches/{sid}", headers=H(key))).status_code == 204
    r = await client.post(
        "/api/feedback",
        json={
            "trace_id": "t1",
            "question": "q",
            "answer": "a",
            "rating": -1,
            "comment": "wrong city",
        },
        headers=H(key),
    )
    assert r.status_code == 201 and r.json()["id"] >= 1
    assert (
        await client.post(
            "/api/feedback", json={"question": "q", "answer": "a", "rating": 5}, headers=H(key)
        )
    ).status_code == 422


async def test_meta_and_openapi(client: httpx.AsyncClient) -> None:
    m = (await client.get("/api/meta")).json()
    assert m["bluecard"]["standard"] == 50700.0 and m["attribution"][0]["name"] == "Arbeitnow"
    spec = (await client.get("/openapi.json")).json()
    for path in (
        "/api/search",
        "/api/chat",
        "/api/cv/match",
        "/api/saved-searches",
        "/api/feedback",
        "/api/postings/{posting_id}",
    ):
        assert path in spec["paths"]


def test_token_bucket_maths() -> None:
    b = TokenBucket(capacity=2, refill_per_s=1.0, updated=0.0)
    assert b.take(now=0.0) == (True, 0.0) and b.take(now=0.0)[0]
    ok, retry = b.take(now=0.0)
    assert not ok and retry == pytest.approx(1.0)
    assert b.take(now=1.0)[0]  # refilled one token
    t = [0.0]
    rl = RateLimiter(clock=lambda: t[0])
    assert rl.check("k", 1, 0.5) == (True, 0)
    assert rl.check("k", 1, 0.5) == (False, 2)
    t[0] = 2.0
    assert rl.check("k", 1, 0.5)[0]
