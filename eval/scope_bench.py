"""Scope check accuracy on questions kept out of config/scope_examples.yaml.

On-topic set: every in-scope golden question plus eval/scope_holdout.yaml.
Off-topic set: the golden out-of-scope questions plus eval/scope_holdout.yaml.
Writes eval/results/scope_bench.json.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from pathlib import Path

import yaml

from jobscout.agent.scope import check_scope

ROOT = Path(__file__).resolve().parent


async def main() -> None:
    golden = yaml.safe_load((ROOT / "golden.yaml").read_text(encoding="utf-8"))["questions"]
    holdout = yaml.safe_load((ROOT / "scope_holdout.yaml").read_text(encoding="utf-8"))
    refused_groups = ("out_of_scope", "adversarial")
    on_topic = [q["question"] for q in golden if q["category"] not in refused_groups]
    off_topic = [q["question"] for q in golden if q["category"] in refused_groups]
    on_topic += holdout["on_topic"]
    off_topic += holdout["off_topic"]
    await check_scope("warm up")
    wrongly_refused, wrongly_allowed, latencies = [], [], []
    for expected, questions in ((True, on_topic), (False, off_topic)):
        for question in questions:
            start = time.perf_counter()
            decision = await check_scope(question)
            latencies.append(time.perf_counter() - start)
            if decision.in_scope != expected:
                (wrongly_refused if expected else wrongly_allowed).append(question)
    result = {
        "on_topic_total": len(on_topic),
        "on_topic_allowed": len(on_topic) - len(wrongly_refused),
        "off_topic_total": len(off_topic),
        "off_topic_refused": len(off_topic) - len(wrongly_allowed),
        "latency_ms_p50": round(statistics.median(latencies) * 1000, 1),
        "wrongly_refused": wrongly_refused,
        "wrongly_allowed": wrongly_allowed,
    }
    (ROOT / "results" / "scope_bench.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
