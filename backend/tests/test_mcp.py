from __future__ import annotations

import json

import pytest
from mcp import Client

from jobscout.mcp_server import server

pytestmark = pytest.mark.db


def payload(result: object) -> dict:
    sc = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if sc:
        return sc.get("result", sc) if "note" in sc else sc
    text = result.content[0].text  # type: ignore[attr-defined]
    return json.loads(text)


async def test_tools_resources_prompts(corpus: None) -> None:
    async with Client(server) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert names == {
            "search_postings",
            "sql_query",
            "get_posting",
            "bluecard_check",
            "gap_analysis",
        }
        r = payload(
            await c.call_tool(
                "search_postings",
                {"query": "RAG machine learning", "filters": {"city": "Munich"}, "top_k": 3},
            )
        )
        data = r.get("result", r)
        assert data["results"][0]["id"] == "an-0000000001"
        bad = payload(await c.call_tool("sql_query", {"sql": "DROP TABLE postings"}))
        assert "rejected by SQL guard" in bad.get("result", bad)["error"]
        ok = payload(
            await c.call_tool(
                "sql_query", {"sql": "SELECT count(*) AS n FROM v_postings WHERE is_active LIMIT 1"}
            )
        )
        assert ok.get("result", ok)["rows"] == [[9]]
        det = payload(await c.call_tool("get_posting", {"posting_id": "an-0000000001"}))
        assert "excerpt" not in det.get("result", det)
        bc = payload(
            await c.call_tool(
                "bluecard_check", {"salary_min": 4300, "period": "month", "year": 2026}
            )
        )
        assert bc.get("result", bc)["status"] == "meets_standard"
        gap = payload(
            await c.call_tool(
                "gap_analysis",
                {
                    "profile": {
                        "skills": ["Java"],
                        "languages": [{"language": "German", "level": "basic"}],
                    },
                    "posting_id": "ba-0000000004",
                },
            )
        )
        assert any(g["kind"] == "german" for g in gap.get("result", gap)["gaps"])
        res = await c.read_resource("jobscout://schema")
        assert "v_postings" in res.contents[0].text  # type: ignore[union-attr]
        thr = json.loads((await c.read_resource("jobscout://bluecard-thresholds")).contents[0].text)  # type: ignore[union-attr]
        assert thr["thresholds"][0]["standard_eur"] == 50700.0
        p = await c.get_prompt("find_jobs", {"role": "data engineer", "city": "Berlin"})
        assert "Berlin" in p.messages[0].content.text  # type: ignore[union-attr]
