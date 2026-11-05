"""Batched embeddings for postings, cached by sha256(text) + model.

The embedded text is the title + the first EMBED_CHARS characters of the cleaned
text (independent of LLM extraction, so a re-extraction never forces a re-embed).
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import numpy as np
from langchain_core.embeddings import Embeddings
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert

from jobscout.db.engine import session_scope
from jobscout.db.models import EmbeddingCache, Posting
from jobscout.llm import embed_model_version, get_embeddings
from jobscout.logging import get_logger

log = get_logger(__name__)
EMBED_CHARS = 2000


def embed_text(title: str, raw_text: str) -> str:
    return f"{title}\n\n{raw_text[:EMBED_CHARS]}"


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def embed_texts(
    texts: list[str], embedder: Embeddings | None = None, model_id: str | None = None
) -> list[list[float]]:
    """Embed with the cache: only cache misses go to the model."""
    embedder = embedder or get_embeddings()
    model_id = model_id or embed_model_version()
    hashes = [text_hash(t) for t in texts]
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(EmbeddingCache.text_hash, EmbeddingCache.embedding).where(
                    EmbeddingCache.model == model_id, EmbeddingCache.text_hash.in_(hashes)
                )
            )
        ).all()
        cached = {r.text_hash: np.asarray(r.embedding, dtype=np.float32).tolist() for r in rows}
        missing = [(h, t) for h, t in zip(hashes, texts, strict=True) if h not in cached]
        if missing:
            vectors = await embedder.aembed_documents([t for _, t in missing])
            for (h, _), v in zip(missing, vectors, strict=True):
                # pgvector stores float32: round now so cached == fresh vectors.
                cached[h] = np.asarray(v, dtype=np.float32).tolist()
            await session.execute(
                insert(EmbeddingCache)
                .values(
                    [
                        {"text_hash": h, "model": model_id, "embedding": cached[h]}
                        for h, _ in missing
                    ]
                )
                .on_conflict_do_nothing()
            )
            await session.commit()
    return [cached[h] for h in hashes]


async def embed_query(text: str) -> list[float]:
    return (await get_embeddings().aembed_documents([text]))[0]


async def run_embedding(limit: int | None = None, batch_size: int = 32) -> dict[str, Any]:
    model_id = embed_model_version()
    embedder = get_embeddings()
    async with session_scope() as session:
        q = select(Posting.id, Posting.title, Posting.raw_text).where(
            Posting.duplicate_of.is_(None),
            or_(Posting.embedding.is_(None), Posting.embedding_model != model_id),
        )
        if limit:
            q = q.limit(limit)
        rows = (await session.execute(q)).all()
    t0 = time.monotonic()
    done = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        texts = [embed_text(r.title, r.raw_text) for r in chunk]
        vectors = await embed_texts(texts, embedder, model_id)
        async with session_scope() as session:
            for r, t, v in zip(chunk, texts, vectors, strict=True):
                await session.execute(
                    update(Posting)
                    .where(Posting.id == r.id)
                    .values(embedding=v, embedding_model=model_id, embedded_hash=text_hash(t))
                )
            await session.commit()
        done += len(chunk)
        log.info("embed_progress", done=done, total=len(rows))
    return {"embedded": done, "model": model_id, "seconds": round(time.monotonic() - t0, 1)}
