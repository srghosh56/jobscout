from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx
import pytest

from jobscout.insights import MIN_N, get_insights, refresh_insights
from tests.test_api import H, app, client, key  # noqa: F401

pytestmark = pytest.mark.db


async def test_refresh_and_payload_shape(corpus: None) -> None:
    r = await refresh_insights()
    assert r["active_postings"] == 9 and "mv_top_skills" in r["refreshed"]
    ins = await get_insights()
    assert ins["coverage"]["active_postings"] == 9 and ins["coverage"]["llm_extracted"] == 9
    assert ins["min_cell_size"] == MIN_N and "Cells with fewer than" in ins["methodology"]
    # synthetic corpus is tiny: every city cell has < 5 postings → suppressed
    assert ins["by_city"] == []
    fam = {f["job_family"]: f for f in ins["by_job_family"]}
    assert fam["data_ml"]["n"] == 5
    assert fam["data_ml"]["share_salary_stated"] == pytest.approx(3 / 5)
    assert fam["data_ml"]["share_visa_or_relocation"] == pytest.approx(3 / 5)
    assert ins["top_skills"]["data_ml"][0] == {"skill": "python", "n": 5}


async def test_sql_tool_can_read_matviews(corpus: None) -> None:
    from jobscout.tools.impl import run_tool

    await refresh_insights()
    out, _ = await run_tool(
        "sql_query",
        {
            "sql": "SELECT job_family, n_active FROM mv_insights_city_family ORDER BY n_active DESC, job_family LIMIT 3"
        },
    )
    assert out["error"] is None and out["rows"]


async def test_public_insights_and_feeds(client: httpx.AsyncClient, key: str) -> None:  # noqa: F811
    await refresh_insights()
    r = await client.get("/api/insights")
    assert r.status_code == 200 and "methodology" in r.json()
    created = (
        await client.post(
            "/api/saved-searches",
            json={"name": "RAG", "query": "RAG machine learning", "filters": {"city": "München"}},
            headers=H(key),
        )
    ).json()
    rss = await client.get(f"/api/feeds/{created['id']}.xml")
    assert rss.status_code == 200 and rss.headers["content-type"].startswith("application/rss+xml")
    root = ET.fromstring(rss.content)
    items = root.findall("./channel/item")
    assert items and items[0].findtext("link") == "https://example.org/jobs/an-0000000001"
    desc = items[0].findtext("description") or ""
    assert "Blue Card (indicative)" in desc and "retrieval augmented" not in desc  # no posting text
    atom = await client.get(f"/api/feeds/{created['id']}.atom")
    assert atom.status_code == 200 and b"<feed" in atom.content
    browse = (
        await client.post(
            "/api/saved-searches",
            json={"name": "All Berlin", "filters": {"city": "Berlin"}},
            headers=H(key),
        )
    ).json()
    r = await client.get(f"/api/feeds/{browse['id']}.xml")
    assert len(ET.fromstring(r.content).findall("./channel/item")) == 3
    assert (
        await client.get("/api/feeds/00000000-0000-0000-0000-000000000000.xml")
    ).status_code == 404
    assert (await client.get("/api/feeds/not-a-uuid.xml")).status_code == 422
