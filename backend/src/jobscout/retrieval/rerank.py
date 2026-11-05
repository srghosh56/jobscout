"""Cross-encoder reranking on CPU (multilingual MiniLM trained on mMARCO).

A bi-encoder embeds query and document separately (fast, indexable); a cross-encoder
reads the (query, document) pair together (slower, more accurate), so we only apply
it to the top-50 fused candidates.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

from jobscout.logging import get_logger
from jobscout.settings import get_settings

log = get_logger(__name__)


@lru_cache(maxsize=1)
def _model() -> Any:
    from sentence_transformers import CrossEncoder

    s = get_settings()
    return CrossEncoder(s.reranker_model, device="cpu", max_length=512)


def rerank_sync(query: str, docs: list[tuple[str, str]], top_k: int) -> list[tuple[str, float]]:
    """docs: [(id, text)] → [(id, score)] best first."""
    if not docs:
        return []
    scores = _model().predict([(query, text) for _, text in docs], batch_size=16)
    ranked = sorted(
        zip([d for d, _ in docs], [float(s) for s in scores], strict=True),
        key=lambda x: -x[1],
    )
    return ranked[:top_k]


async def rerank(query: str, docs: list[tuple[str, str]], top_k: int) -> list[tuple[str, float]]:
    return await asyncio.to_thread(rerank_sync, query, docs, top_k)


def warmup() -> None:
    if get_settings().reranker_enabled:
        rerank_sync("warmup", [("x", "warmup")], 1)
