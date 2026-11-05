"""Evaluation entry point.

  make eval-smoke   → 20 questions on the synthetic corpus, LLM calls replayed from
                      eval/cassettes/smoke.json (CI: no API keys), thresholds enforced.
  make eval-record  → re-record the smoke cassette with the configured real model.
  make eval-full    → 80 golden questions against the real database and real model.

Summaries go to eval/results/*.json (committed); per-question details with answers go to
eval/runs/*.jsonl (git-ignored).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARGS = argparse.ArgumentParser()
ARGS.add_argument("--smoke", action="store_true")
ARGS.add_argument("--full", action="store_true")
ARGS.add_argument("--record", action="store_true")
ARGS.add_argument("--replay", action="store_true")
ARGS.add_argument("--limit", type=int)
ARGS.add_argument("--ids", nargs="*")
ARGS.add_argument("--no-judge", action="store_true")
ARGS.add_argument("--tag", default="")
ARGS.add_argument("--agent-mode", choices=["single", "supervisor"])
ARGS.add_argument("--provider", choices=["ollama", "mistral", "gemini"])
args = ARGS.parse_args()

if args.smoke:
    # Isolated, deterministic environment: synthetic corpus, fake embeddings, no reranker.
    os.environ.setdefault(
        "SMOKE_DATABASE_URL",
        "postgresql+asyncpg://jobscout:jobscout@localhost:55433/jobscout_smoke",
    )
    db = os.environ.get("CI_SMOKE_DATABASE_URL") or os.environ["SMOKE_DATABASE_URL"]
    os.environ["DATABASE_URL"] = db
    os.environ["READONLY_DATABASE_URL"] = db.replace(
        "jobscout:jobscout@", "jobscout_ro:jobscout_ro@"
    )
    os.environ["EMBED_PROVIDER"] = "fake"
    os.environ["RERANKER_ENABLED"] = "false"
    if args.replay:
        os.environ["LLM_PROVIDER"] = "fake"
if args.agent_mode:
    os.environ["AGENT_MODE"] = args.agent_mode
if args.provider:
    os.environ["LLM_PROVIDER"] = args.provider

import asyncio  # noqa: E402
import datetime as dt  # noqa: E402
import json  # noqa: E402
from typing import Any  # noqa: E402

import yaml  # noqa: E402
from sqlalchemy import text  # noqa: E402

from jobscout.agent.graph import Agent  # noqa: E402
from jobscout.db import admin  # noqa: E402
from jobscout.db.engine import dispose_engines, session_scope  # noqa: E402
from jobscout.evaluation.cassette import Cassette, CassetteChatModel  # noqa: E402
from jobscout.evaluation.runner import aggregate, evaluate_question, refresh_sql_expectations  # noqa: E402
from jobscout.llm import get_chat_model, model_version  # noqa: E402
from jobscout.logging import configure_logging  # noqa: E402
from jobscout.settings import get_settings  # noqa: E402


async def prepare_smoke_db() -> None:
    from jobscout.embed import run_embedding
    from jobscout.evaluation.synthetic_corpus import load
    from jobscout.insights import refresh_insights

    url = os.environ["DATABASE_URL"]
    admin.ensure_database(url)
    admin.upgrade(url)
    async with session_scope() as s:
        await s.execute(text("TRUNCATE postings, embedding_cache RESTART IDENTITY CASCADE"))
        await s.commit()
        await load(s)
    await run_embedding()
    await refresh_insights()


def build_agent(cassette: Cassette | None) -> tuple[Agent, Any]:
    if cassette is None:
        return Agent(), (None if args.no_judge else get_chat_model("judge"))
    inner = None if cassette.mode == "replay" else get_chat_model("chat")
    judge_inner = None if cassette.mode == "replay" else get_chat_model("judge")
    model = CassetteChatModel(inner=inner, cassette=cassette)
    judge = None if args.no_judge else CassetteChatModel(inner=judge_inner, cassette=cassette)
    return Agent(model=model), judge


def build_supervisor_agent() -> Any:
    from jobscout.agent.supervisor import SupervisorAgent

    return SupervisorAgent()


async def main() -> int:
    configure_logging("WARNING", json=True)
    if args.smoke:
        spec = yaml.safe_load((ROOT / "smoke.yaml").read_text())
        await prepare_smoke_db()
        cassette = (
            Cassette(ROOT / "cassettes" / "smoke.json", "replay" if args.replay else "record")
            if (args.replay or args.record)
            else None
        )
        name = "smoke"
    else:
        spec = yaml.safe_load((ROOT / "golden.yaml").read_text())
        cassette = None
        name = f"full_v{spec['version']}" + (f"_{args.tag}" if args.tag else "")
    questions: list[dict[str, Any]] = spec["questions"]
    if args.ids:
        questions = [q for q in questions if q["id"] in set(args.ids)]
    if args.limit:
        questions = questions[: args.limit]
    await refresh_sql_expectations(questions)

    agent, judge_model = build_agent(cassette)
    if get_settings().agent_mode == "supervisor" and cassette is None:
        agent = build_supervisor_agent()
    results = []
    runs = ROOT / "runs"
    runs.mkdir(exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S")
    detail_path = runs / f"{name}_{stamp}.jsonl"
    with detail_path.open("w", encoding="utf-8") as fh:
        for i, q in enumerate(questions, 1):
            r = await evaluate_question(agent, judge_model, q, cassette)
            results.append(r)
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
            fh.flush()
            print(
                f"[{i}/{len(questions)}] {q['id']} tools={r['tools_called']} ok={r['tool_selection_correct']} {r['latency_s']}s",
                file=sys.stderr,
            )
    if cassette is not None:
        cassette.save()
    summary = {
        "name": name,
        "golden_version": spec.get("version"),
        "run_at": stamp,
        "model": model_version("chat") if not args.replay else "replayed-cassette",
        "judge_model": None
        if args.no_judge
        else (model_version("judge") if not args.replay else "replayed-cassette"),
        "agent_mode": get_settings().agent_mode,
        "embed_provider": get_settings().effective_embed_provider,
        "reranker": get_settings().reranker_enabled,
        "metrics": aggregate(results),
        "details_file": str(detail_path.relative_to(ROOT.parent)),
    }
    (ROOT / "results").mkdir(exist_ok=True)
    if not args.ids and not args.limit:
        (ROOT / "results" / f"{name}.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False)
        )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    await dispose_engines()
    if args.smoke and "thresholds" in spec:
        m, th, failed = summary["metrics"], spec["thresholds"], []
        for key, minimum in th.items():
            if key == "max_errors":
                if m["errors"] > minimum:
                    failed.append(f"errors {m['errors']} > {minimum}")
            elif m.get(key) is None or m[key] < minimum:
                failed.append(f"{key} {m.get(key)} < {minimum}")
        if failed:
            print("SMOKE EVAL FAILED: " + "; ".join(failed), file=sys.stderr)
            return 1
        print("smoke eval thresholds passed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
