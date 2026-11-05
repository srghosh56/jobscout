"""Retrieval benchmark: recall@5, MRR and nDCG@10 for each retrieval mode on the
golden set's retrieval (semantic) questions. Writes eval/results/retrieval_bench.json.

Usage: make retrieval-bench   (uses the configured embedding model + live DB)
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from jobscout.evaluation.metrics import mean, ndcg_at_k, percentile, recall_at_k, reciprocal_rank
from jobscout.retrieval.filters import SearchFilters
from jobscout.retrieval.search import hybrid_search

ROOT = Path(__file__).resolve().parent
MODES = ("fts", "dense", "hybrid", "hybrid_rerank")


def load_questions(path: Path = ROOT / "golden.yaml") -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        q for q in data["questions"] if q.get("category") == "semantic" and q.get("relevant_ids")
    ]


async def bench(questions: list[dict[str, Any]], modes: tuple[str, ...] = MODES) -> dict[str, Any]:
    out: dict[str, Any] = {"n_questions": len(questions), "modes": {}}
    for mode in modes:
        r5, rr, nd, lat = [], [], [], []
        for q in questions:
            filters = (
                SearchFilters(**q["retrieval_filters"]) if q.get("retrieval_filters") else None
            )
            t0 = time.perf_counter()
            hits = await hybrid_search(q["question"], filters, top_k=10, mode=mode)  # type: ignore[arg-type]
            lat.append(time.perf_counter() - t0)
            ranked = [h.id for h in hits]
            rel = set(q["relevant_ids"])
            r5.append(recall_at_k(ranked, rel, 5))
            rr.append(reciprocal_rank(ranked, rel))
            nd.append(ndcg_at_k(ranked, rel, 10))
        out["modes"][mode] = {
            "recall@5": round(mean(r5), 3),
            "mrr": round(mean(rr), 3),
            "ndcg@10": round(mean(nd), 3),
            "latency_p50_s": round(percentile(lat, 50), 3),
            "latency_p95_s": round(percentile(lat, 95), 3),
        }
    return out


def main() -> None:
    questions = load_questions()
    if not questions:
        print("no semantic questions with relevant_ids in eval/golden.yaml", file=sys.stderr)
        sys.exit(1)
    res = asyncio.run(bench(questions))
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "retrieval_bench.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
