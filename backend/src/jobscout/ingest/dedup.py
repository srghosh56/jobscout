"""Near-duplicate detection across sources with MinHash + LSH (datasketch).

The same job is often posted on both boards with small edits. We shingle the
normalised title + text into word 5-grams, build 128-permutation MinHash signatures
and use LSH to find candidate pairs with estimated Jaccard similarity >= threshold.
The earliest-seen posting is canonical; the others get ``duplicate_of`` set and are
hidden from search and insights.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from datasketch import MinHash, MinHashLSH

NUM_PERM = 128
SHINGLE = 5
DEFAULT_THRESHOLD = 0.85


def shingles(text: str, k: int = SHINGLE) -> set[str]:
    words = re.findall(r"\w+", text.lower())
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def signature(text: str, num_perm: int = NUM_PERM) -> MinHash:
    m = MinHash(num_perm=num_perm, seed=1)
    for s in shingles(text):
        m.update(s.encode("utf-8"))
    return m


@dataclass(frozen=True)
class DocForDedup:
    id: str
    text: str
    order_key: str  # earlier sorts first → canonical


def find_duplicates(
    docs: Sequence[DocForDedup], threshold: float = DEFAULT_THRESHOLD
) -> dict[str, str]:
    """Return {duplicate_id: canonical_id}."""
    ordered = sorted(docs, key=lambda d: (d.order_key, d.id))
    lsh = MinHashLSH(threshold=threshold, num_perm=NUM_PERM)
    sigs: dict[str, MinHash] = {}
    canonical_of: dict[str, str] = {}
    for d in ordered:
        sig = signature(d.text)
        candidates: Iterable[str] = lsh.query(sig)
        best: str | None = None
        for cid in candidates:
            # Confirm with the estimated Jaccard (LSH has false positives).
            if sig.jaccard(sigs[cid]) >= threshold:
                best = canonical_of.get(cid, cid)
                break
        if best is not None:
            canonical_of[d.id] = best
        else:
            lsh.insert(d.id, sig)
        sigs[d.id] = sig
    return canonical_of
