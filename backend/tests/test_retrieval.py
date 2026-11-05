from __future__ import annotations

import pytest

from jobscout.db.engine import session_scope
from jobscout.retrieval.filters import SearchFilters
from jobscout.retrieval.search import fulltext_search, hybrid_search

pytestmark = pytest.mark.db


async def ids(query: str, mode: str = "hybrid", **f: object) -> list[str]:
    hits = await hybrid_search(query, SearchFilters(**f) if f else None, top_k=10, mode=mode)  # type: ignore[arg-type]
    return [h.id for h in hits]


async def test_fts_english_and_german_configs(corpus: None) -> None:
    async with session_scope() as s:
        en = [h.id for h in await fulltext_search(s, "retrieval augmented generation")]
        de = [h.id for h in await fulltext_search(s, "Softwareentwickler Deutschkenntnisse")]
    assert en[0] == "an-0000000001"
    assert de == ["ba-0000000004"]


async def test_fts_or_semantics_and_empty_query(corpus: None) -> None:
    async with session_scope() as s:
        hits = await fulltext_search(s, "kubernetes unicorn-nonexistent-term")
        assert [h.id for h in hits] == ["an-0000000006"]
        assert await fulltext_search(s, "?!") == []


async def test_inactive_and_duplicates_excluded(corpus: None) -> None:
    got = await ids("RAG LangChain Python machine learning", mode="fts")
    assert "an-0000000009" not in got and "ba-0000000010" not in got
    got_all = await ids("RAG LangChain Python", mode="fts", active_only=False)
    assert "an-0000000009" in got_all


async def test_dense_and_hybrid_modes(corpus: None) -> None:
    dense = await ids("machine learning engineer RAG pipelines python", mode="dense")
    assert dense[0] == "an-0000000001"
    hybrid = await hybrid_search("machine learning RAG", top_k=3, mode="hybrid")
    assert hybrid[0].id == "an-0000000001"
    assert hybrid[0].ranks.get("fts") and hybrid[0].ranks.get("dense")
    assert "«" in hybrid[0].snippet or hybrid[0].snippet


async def test_filters_city_variants_years_skills(corpus: None) -> None:
    assert set(await ids("machine learning", city="Munich")) == {"an-0000000001", "an-0000000002"}
    got = await ids("machine learning", city="München", max_years=3)
    assert got == ["an-0000000001"]
    assert await ids("engineer", mode="fts", skills=["rag", "langchain"]) == ["an-0000000001"]


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"english_sufficient": False}, {"ba-0000000004"}),
        ({"visa_sponsorship": True}, {"an-0000000001"}),
        ({"relocation_support": True}, {"an-0000000002", "an-0000000008"}),
        ({"salary_stated": True}, {"an-0000000001", "an-0000000003", "ba-0000000005"}),
        (
            {"bluecard_status": ["meets_standard", "meets_reduced_only"]},
            {"an-0000000001", "an-0000000003"},
        ),
        ({"remote_policy": ["remote"]}, {"an-0000000006"}),
        ({"seniority": ["senior"]}, {"an-0000000002", "ba-0000000004"}),
        ({"posting_language": "de"}, {"ba-0000000004", "ba-0000000005"}),
        ({"job_family": "devops_cloud"}, {"an-0000000006"}),
    ],
)
async def test_each_filter(corpus: None, filters: dict[str, object], expected: set[str]) -> None:
    hits = await hybrid_search(
        "engineer developer data python java kubernetes react",
        SearchFilters(**filters),
        top_k=20,
        mode="dense",
    )  # type: ignore[arg-type]
    assert {h.id for h in hits} == expected


def test_filters_is_empty() -> None:
    assert SearchFilters().is_empty()
    assert not SearchFilters(city="Berlin").is_empty()


async def test_public_sources_restriction(corpus: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from jobscout.settings import get_settings

    monkeypatch.setattr(get_settings(), "public_sources", ["arbeitsagentur"])
    got = await ids("engineer developer data", mode="dense")
    assert got and all(i.startswith("ba-") for i in got)
