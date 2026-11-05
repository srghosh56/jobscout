from __future__ import annotations

import pytest
from sqlalchemy import func, select

from jobscout.db.engine import session_scope
from jobscout.db.models import EmbeddingCache, Posting
from jobscout.embed import embed_texts, run_embedding
from jobscout.ingest.cleaning import CleanPosting
from jobscout.ingest.upsert import upsert_postings
from jobscout.llm import HashingEmbeddings
from tests.fixtures.synthetic import LOREM_DE, LOREM_EN


def test_hashing_embeddings_similarity() -> None:
    e = HashingEmbeddings(1024)
    a, b, c = e.embed_documents(
        [
            "python machine learning rag",
            "rag machine learning python berlin",
            "koch restaurant küche",
        ]
    )
    dot = lambda x, y: sum(i * j for i, j in zip(x, y, strict=True))  # noqa: E731
    assert dot(a, b) > dot(a, c)
    assert abs(dot(a, a) - 1) < 1e-9


@pytest.mark.db
async def test_embedding_job_uses_cache(clean_db: None) -> None:
    async with session_scope() as s:
        await upsert_postings(
            s,
            [
                CleanPosting(
                    source="arbeitnow",
                    source_id="m1",
                    url="u",
                    title="ML",
                    company=None,
                    raw_text=LOREM_EN,
                    city=None,
                ),
                CleanPosting(
                    source="arbeitnow",
                    source_id="m2",
                    url="u",
                    title="Dev",
                    company=None,
                    raw_text=LOREM_DE,
                    city=None,
                ),
            ],
        )
    r = await run_embedding(batch_size=1)
    assert r["embedded"] == 2 and r["model"] == "fake:hashing-1024"
    assert (await run_embedding())["embedded"] == 0  # idempotent
    async with session_scope() as s:
        assert (await s.execute(select(func.count()).select_from(EmbeddingCache))).scalar_one() == 2
        assert (
            await s.execute(select(func.count()).where(Posting.embedding.is_not(None)))
        ).scalar_one() == 2
    v1 = await embed_texts(["same text"])
    v2 = await embed_texts(["same text"])
    assert v1 == v2
