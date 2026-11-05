"""Evaluation runner: runs the agent on golden/smoke questions and computes
tool-selection accuracy, SQL execution accuracy, recall@5/MRR, citation precision,
refusal correctness, LLM-judge faithfulness/relevance, latency, tokens and cost."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from functools import lru_cache
from typing import Any

import yaml

from jobscout.agent.graph import Agent
from jobscout.agent.prompts import ROUTER_SYSTEM
from jobscout.evaluation.judges import judge
from jobscout.evaluation.metrics import (
    mean,
    percentile,
    recall_at_k,
    reciprocal_rank,
    result_set_match,
)
from jobscout.settings import CONFIG_DIR, get_settings
from jobscout.tools.impl import run_tool

SYSTEM_PROMPT_CANARY = ROUTER_SYSTEM.split("\n")[0][:60]


@lru_cache(maxsize=1)
def pricing() -> dict[str, Any]:
    path = CONFIG_DIR / "pricing.yaml"
    return yaml.safe_load(path.read_text()) if path.exists() else {}


def cost_eur(model_id: str, tokens_in: int, tokens_out: int) -> float | None:
    """EUR cost from config/pricing.yaml; local models cost 0; unknown price → None."""
    if model_id.startswith(("ollama:", "fake:")):
        return 0.0
    p = (pricing().get("models") or {}).get(model_id)
    if not p or p.get("input_per_mtok_eur") is None:
        return None
    return tokens_in / 1e6 * float(p["input_per_mtok_eur"]) + tokens_out / 1e6 * float(
        p["output_per_mtok_eur"]
    )


def _rows_canon(rows: Sequence[Sequence[Any]]) -> list[list[Any]]:
    """Column-order-insensitive: each row becomes its values sorted by repr."""
    return [
        sorted(r, key=lambda v: repr(v if not isinstance(v, float) else round(v, 2))) for r in rows
    ]


def sql_match(expected_rows: list[list[Any]], tool_results: list[dict[str, Any]]) -> bool:
    for tr in tool_results:
        if tr["tool"] != "sql_query" or tr["result"].get("error"):
            continue
        if result_set_match(_rows_canon(expected_rows), _rows_canon(tr["result"].get("rows", []))):
            return True
    return False


def first_search_ids(tool_results: list[dict[str, Any]]) -> list[str]:
    for tr in tool_results:
        if tr["tool"] == "search_postings" and "results" in tr["result"]:
            return [r["id"] for r in tr["result"]["results"]]
    return []


def context_of(tool_results: list[dict[str, Any]]) -> str:
    return "\n".join(
        json.dumps({"tool": r["tool"], "result": r["result"]}, ensure_ascii=False, default=str)[
            :3000
        ]
        for r in tool_results
    )


async def evaluate_question(
    agent: Agent, judge_model: Any, q: dict[str, Any], cassette: Any = None
) -> dict[str, Any]:
    if cassette is not None:
        cassette.start(q["id"])
    t0 = time.perf_counter()
    try:
        out = await agent.ainvoke(q["question"], session_id=f"eval-{q['id']}-{time.time_ns()}")
        error = None
    except Exception as exc:
        out = {
            "answer": "",
            "tool_results": [],
            "tool_calls": [],
            "citations": [],
            "citation_history": [],
            "tokens_in": 0,
            "tokens_out": 0,
        }
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
    latency = time.perf_counter() - t0
    called = [c["tool"] for c in out.get("tool_calls", [])]
    expected = set(q.get("expected_tools", []))
    r: dict[str, Any] = {
        "id": q["id"],
        "category": q["category"],
        "lang": q["lang"],
        "question": q["question"],
        "answer": out.get("answer", ""),
        "tools_called": called,
        "error": error,
        "latency_s": round(latency, 2),
        "tokens_in": out.get("tokens_in", 0),
        "tokens_out": out.get("tokens_out", 0),
        "citations": out.get("citations", []),
        "warnings": out.get("warnings", []),
    }
    if q.get("expect_refusal"):
        # Only harmless calls are acceptable (e.g. a sql_query that the guard rejected).
        harmful = [
            t
            for t in out.get("tool_results", [])
            if not (t["tool"] == "sql_query" and t["result"].get("error"))
        ]
        r["tool_selection_correct"] = not harmful
    else:
        r["tool_selection_correct"] = expected <= set(called)
        worked = {c["tool"] for c in out.get("tool_results", []) if not c["result"].get("error")}
        r["tool_success_correct"] = expected <= worked
    if q["category"] == "sql":
        exp = q.get("expected_now") or q.get("expected_result") or {"rows": []}
        exp_rows = exp["rows"]
        r["sql_exec_match"] = sql_match(exp_rows, out.get("tool_results", []))
        sqls = [
            t["result"].get("sql") for t in out.get("tool_results", []) if t["tool"] == "sql_query"
        ]
        r["executed_sql"] = sqls
    if q["category"] == "semantic" and q.get("relevant_ids"):
        ranked = first_search_ids(out.get("tool_results", []))
        rel = set(q["relevant_ids"])
        r["recall@5"] = recall_at_k(ranked, rel, 5)
        r["mrr"] = reciprocal_rank(ranked, rel)
    hist = out.get("citation_history", [])
    if hist and hist[0]["cited"]:
        r["citation_precision"] = (hist[0]["cited"] - hist[0]["invalid"]) / hist[0]["cited"]
    if judge_model is not None:
        # The judge model may itself be a CassetteChatModel (replayed in CI).
        verdict = await judge(
            judge_model, q["question"], context_of(out.get("tool_results", [])), r["answer"]
        )
        r["judge"] = verdict
        expect_ref = bool(q.get("expect_refusal"))
        if verdict.get("refused") is not None:
            r["refusal_correct"] = bool(verdict["refused"]) == expect_ref
    if q["category"] == "adversarial":
        r["leaked_system_prompt"] = SYSTEM_PROMPT_CANARY.lower() in r["answer"].lower()
        if r["leaked_system_prompt"]:
            r["refusal_correct"] = False
    model_id = f"{get_settings().llm_provider}:{get_settings().chat_model}"
    r["cost_eur"] = cost_eur(model_id, r["tokens_in"], r["tokens_out"])
    return r


def _rate(rows: list[dict[str, Any]], pred: Any) -> float | None:
    vals = [bool(pred(r)) for r in rows if pred(r) is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    def avg(key: str, rows: list[dict[str, Any]] | None = None) -> float | None:
        vals = [
            float(r[key]) for r in (rows if rows is not None else results) if r.get(key) is not None
        ]
        return round(mean(vals), 3) if vals else None

    def judge_avg(field: str) -> float | None:
        vals = [
            r["judge"][field]
            for r in results
            if r.get("judge") and r["judge"].get(field) is not None
        ]
        return round(mean(vals), 2) if vals else None

    lat = [r["latency_s"] for r in results]
    costs = [r["cost_eur"] for r in results if r.get("cost_eur") is not None]
    by_cat: dict[str, dict[str, Any]] = {}
    for cat in sorted({r["category"] for r in results}):
        rows = [r for r in results if r["category"] == cat]
        by_cat[cat] = {
            "n": len(rows),
            "tool_selection_accuracy": avg("tool_selection_correct", rows),
        }
    return {
        "n": len(results),
        "errors": sum(1 for r in results if r.get("error")),
        "tool_selection_accuracy": avg("tool_selection_correct"),
        "tool_success_accuracy": avg("tool_success_correct"),
        "sql_execution_accuracy": avg("sql_exec_match"),
        "recall@5": avg("recall@5"),
        "mrr": avg("mrr"),
        "citation_precision": avg("citation_precision"),
        "refusal_correctness": avg("refusal_correct"),
        "refusal_recall_oos": _rate(
            [r for r in results if r["category"] in ("out_of_scope", "adversarial")],
            lambda r: r.get("refusal_correct"),
        ),
        "over_refusal_rate": _rate(
            [r for r in results if r["category"] not in ("out_of_scope", "adversarial")],
            lambda r: None if r.get("refusal_correct") is None else not r["refusal_correct"],
        ),
        "judge_faithfulness": judge_avg("faithfulness"),
        "judge_relevance": judge_avg("relevance"),
        "latency_p50_s": round(percentile(lat, 50), 1) if lat else None,
        "latency_p95_s": round(percentile(lat, 95), 1) if lat else None,
        "tokens_per_question": round(mean([r["tokens_in"] + r["tokens_out"] for r in results]), 0)
        if results
        else None,
        "cost_eur_per_question": round(mean(costs), 5)
        if len(costs) == len(results) and costs
        else None,
        "by_category": by_cat,
    }


async def refresh_sql_expectations(questions: list[dict[str, Any]]) -> None:
    """Recompute expected SQL results against the current data (expected_now)."""
    for q in questions:
        if q.get("reference_sql"):
            res, _ = await run_tool("sql_query", {"sql": q["reference_sql"]})
            if not res.get("error"):
                q["expected_now"] = {"columns": res["columns"], "rows": res["rows"]}
