"""Ranking and agreement metrics (pure functions, unit-tested)."""

from __future__ import annotations

import math
from collections.abc import Hashable, Sequence


def recall_at_k(ranked: Sequence[Hashable], relevant: set[Hashable], k: int) -> float:
    """|relevant ∩ top-k| / |relevant| (0 if there are no relevant items)."""
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def reciprocal_rank(ranked: Sequence[Hashable], relevant: set[Hashable]) -> float:
    for i, doc in enumerate(ranked, start=1):
        if doc in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(
    ranked: Sequence[Hashable], relevance: dict[Hashable, float] | set[Hashable], k: int
) -> float:
    """nDCG@k with gains rel/log2(rank+1). Binary relevance if a set is given."""
    gains = dict.fromkeys(relevance, 1.0) if isinstance(relevance, set) else dict(relevance)
    dcg = sum(gains.get(d, 0.0) / math.log2(i + 1) for i, d in enumerate(ranked[:k], start=1))
    ideal = sorted(gains.values(), reverse=True)[:k]
    idcg = sum(g / math.log2(i + 1) for i, g in enumerate(ideal, start=1))
    return dcg / idcg if idcg > 0 else 0.0


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def percentile(xs: Sequence[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    idx = min(len(s) - 1, max(0, math.ceil(p / 100 * len(s)) - 1))
    return s[idx]


def cohens_kappa(a: Sequence[Hashable], b: Sequence[Hashable]) -> float:
    """Cohen's kappa for two raters over the same items."""
    if len(a) != len(b) or not a:
        raise ValueError("need two equal-length, non-empty label lists")
    n = len(a)
    labels = set(a) | set(b)
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pe = sum((list(a).count(lbl) / n) * (list(b).count(lbl) / n) for lbl in labels)
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def result_set_match(expected: list[list[object]], got: list[list[object]]) -> bool:
    """Execution accuracy: same multiset of rows, order-insensitive, values normalised."""

    def norm(v: object) -> object:
        if isinstance(v, float):
            return round(v, 2)
        if isinstance(v, str):
            return v.strip().lower()
        return v

    def canon(rows: list[list[object]]) -> list[tuple[object, ...]]:
        return sorted((tuple(norm(v) for v in r) for r in rows), key=repr)

    return canon(expected) == canon(got)
