"""Ablations (make ablations):

1. Retrieval: dense vs full-text vs hybrid (RRF) vs hybrid + cross-encoder rerank, on the
   golden semantic questions (recall@5, MRR, nDCG@10, latency).
2. Plain single-shot RAG (retrieve top-8 → one composer call) vs the agent, on the
   semantic + multi-step questions (judge faithfulness/relevance, citation precision,
   tool-free latency). Agent numbers come from the latest full run (same questions).
3. Agent architecture/model (single vs supervisor+critic; Mistral vs Gemini): only when
   GEMINI_API_KEY is set; otherwise recorded as "not measured".
Results: eval/results/ablations.json.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import HumanMessage, SystemMessage

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from retrieval_bench import bench, load_questions  # noqa: E402

from jobscout.agent import prompts  # noqa: E402
from jobscout.agent.citations import check_citations  # noqa: E402
from jobscout.evaluation.judges import judge  # noqa: E402
from jobscout.evaluation.metrics import mean, percentile, recall_at_k, reciprocal_rank  # noqa: E402
from jobscout.llm import get_chat_model, usage_of  # noqa: E402
from jobscout.retrieval.filters import SearchFilters  # noqa: E402
from jobscout.retrieval.search import hybrid_search  # noqa: E402
from jobscout.tools.impl import summary_from_hit, wrap_untrusted  # noqa: E402


async def single_shot_rag(q: dict[str, Any], chat: Any, judge_model: Any) -> dict[str, Any]:
    t0 = time.perf_counter()
    filters = SearchFilters(**q["retrieval_filters"]) if q.get("retrieval_filters") else None
    hits = await hybrid_search(q["question"], filters, top_k=8)
    result = {
        "query": q["question"],
        "results": [summary_from_hit(h).model_dump(mode="json") for h in hits],
    }
    lang = prompts.LANG_NAMES.get(prompts.detect_user_language(q["question"]), "English")
    ai = await chat.ainvoke(
        [
            SystemMessage(prompts.COMPOSER_SYSTEM.format(language=lang, extra="")),
            HumanMessage(
                f"Question: {q['question']}\n\nTool results:\n{wrap_untrusted('search_postings', result)}"
            ),
        ]
    )
    answer = str(ai.content)
    latency = time.perf_counter() - t0
    check = check_citations(answer, [h.id for h in hits])
    verdict = await judge(
        judge_model, q["question"], json.dumps(result, ensure_ascii=False)[:8000], answer
    )
    ti, to = usage_of(ai)
    out: dict[str, Any] = {"id": q["id"], "latency_s": latency, "judge": verdict, "tokens": ti + to}
    if check.cited:
        out["citation_precision"] = len(check.valid) / len(check.cited)
    if q.get("relevant_ids"):
        ranked = [h.id for h in hits]
        out["recall@5"] = recall_at_k(ranked, set(q["relevant_ids"]), 5)
        out["mrr"] = reciprocal_rank(ranked, set(q["relevant_ids"]))
    return out


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def avg(key: str) -> float | None:
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(mean(vals), 3) if vals else None

    def javg(key: str) -> float | None:
        vals = [r["judge"][key] for r in rows if r.get("judge") and r["judge"].get(key) is not None]
        return round(mean(vals), 2) if vals else None

    lat = [r["latency_s"] for r in rows]
    return {
        "n": len(rows),
        "judge_faithfulness": javg("faithfulness"),
        "judge_relevance": javg("relevance"),
        "citation_precision": avg("citation_precision"),
        "recall@5": avg("recall@5"),
        "mrr": avg("mrr"),
        "latency_p50_s": round(percentile(lat, 50), 1) if lat else None,
        "latency_p95_s": round(percentile(lat, 95), 1) if lat else None,
    }


def latest_agent_rows(ids: set[str]) -> list[dict[str, Any]]:
    runs = sorted((ROOT / "runs").glob("full_v*.jsonl"))
    runs = [r for r in runs if "_" not in r.stem.split("full_v", 1)[1].split("_", 1)[-1] or True]
    if not runs:
        return []
    rows = [json.loads(x) for x in runs[-1].read_text().splitlines() if x.strip()]
    return [r for r in rows if r["id"] in ids]


async def main() -> None:
    out: dict[str, Any] = {}
    sem = load_questions()
    out["retrieval"] = await bench(sem)

    golden = yaml.safe_load((ROOT / "golden.yaml").read_text())
    subset = [q for q in golden["questions"] if q["category"] in ("semantic", "multi_step")]
    chat, judge_model = get_chat_model("chat"), get_chat_model("judge")
    rag = []
    for i, q in enumerate(subset, 1):
        rag.append(await single_shot_rag(q, chat, judge_model))
        print(f"[rag {i}/{len(subset)}] {q['id']} {rag[-1]['latency_s']:.1f}s", file=sys.stderr)
    agent_rows = latest_agent_rows({q["id"] for q in subset})
    out["rag_vs_agent"] = {
        "questions": "golden semantic + multi_step",
        "single_shot_rag": summarise(rag),
        "agent": summarise(agent_rows) if agent_rows else "not measured (run make eval-full first)",
    }
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY_FILE"):
        out["architecture_model"] = (
            "run: eval/run_eval.py --full --provider gemini [--agent-mode supervisor]"
        )
    else:
        out["architecture_model"] = (
            "not measured: GEMINI_API_KEY not set (single vs supervisor+critic and Mistral vs Gemini need it)"
        )
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "ablations.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
