"""Reciprocal Rank Fusion (Cormack, Clarke & Büttcher, SIGIR 2009).

score(d) = Σ_r 1 / (k + rank_r(d)), with 1-based ranks. Documents missing from a
ranking contribute nothing for it. k (default 60) damps the influence of the very top
ranks so one retriever can't dominate; RRF needs no score normalisation, which is
why it works for combining BM25-like and cosine scores on different scales.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence


def rrf[T: Hashable](rankings: Sequence[Sequence[T]], k: int = 60) -> list[tuple[T, float]]:
    if k <= 0:
        raise ValueError("k must be positive")
    scores: dict[T, float] = {}
    first_seen: dict[T, int] = {}
    order = 0
    for ranking in rankings:
        seen_here: set[T] = set()
        for rank, doc in enumerate(ranking, start=1):
            if doc in seen_here:  # ignore duplicates within one ranking
                continue
            seen_here.add(doc)
            scores[doc] = scores.get(doc, 0.0) + 1.0 / (k + rank)
            if doc not in first_seen:
                first_seen[doc] = order
                order += 1
    # Ties broken by first appearance → deterministic output.
    return sorted(scores.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))
