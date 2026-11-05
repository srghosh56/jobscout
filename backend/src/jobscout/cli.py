"""``jobscout`` command-line entry point (used by the Makefile and scheduled workflows)."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable, Coroutine
from typing import Any

from jobscout.logging import configure_logging
from jobscout.settings import get_settings


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def cmd_migrate(_: argparse.Namespace) -> None:
    from jobscout.db import admin

    admin.upgrade()
    print("migrations applied; read-only role configured")


def cmd_ingest(args: argparse.Namespace) -> None:
    from jobscout.ingest.run import run_ingest

    stats = _run(run_ingest(sources=args.source, limit=args.limit, offline=args.offline))
    print(json.dumps(stats, indent=2, default=str))


def cmd_extract(args: argparse.Namespace) -> None:
    from jobscout.extract.runner import run_extraction

    if args.rules_only:
        from jobscout.extract.runner import run_rules_extraction

        print(
            json.dumps(_run(run_rules_extraction(limit=args.limit, refresh=args.refresh)), indent=2)
        )
        return
    print(
        json.dumps(_run(run_extraction(limit=args.limit, concurrency=args.concurrency)), indent=2)
    )


def cmd_backfill(args: argparse.Namespace) -> None:
    from jobscout.extract.backfill import load_backfill_file

    print(json.dumps(_run(load_backfill_file(args.path)), indent=2))


def cmd_embed(args: argparse.Namespace) -> None:
    from jobscout.embed import run_embedding

    print(json.dumps(_run(run_embedding(limit=args.limit, batch_size=args.batch_size)), indent=2))


def cmd_expiry(args: argparse.Namespace) -> None:
    from jobscout.expiry import run_expiry

    print(json.dumps(_run(run_expiry(limit=args.limit)), indent=2, default=str))


def cmd_insights(_: argparse.Namespace) -> None:
    from jobscout.insights import refresh_insights

    print(json.dumps(_run(refresh_insights()), indent=2, default=str))


def cmd_seed(args: argparse.Namespace) -> None:
    from jobscout.seed import seed

    print(json.dumps(_run(seed(skip_llm=args.skip_llm)), indent=2, default=str))


def cmd_openapi(_: argparse.Namespace) -> None:
    from jobscout.api.app import create_app

    print(json.dumps(create_app().openapi(), indent=2))


def cmd_create_key(args: argparse.Namespace) -> None:
    from jobscout.api.auth import create_api_key

    key = _run(create_api_key(args.name, is_demo=args.demo))
    print(key)  # shown once; only the hash is stored


def cmd_feedback_review(args: argparse.Namespace) -> None:
    from jobscout.feedback_review import review

    _run(review(approve=args.approve, reject=args.reject))


def cmd_mcp(args: argparse.Namespace) -> None:
    from jobscout.mcp_server import run_server

    run_server(args.transport, port=args.port)


def main(argv: list[str] | None = None) -> None:
    s = get_settings()
    configure_logging(s.log_level, json=s.log_json)
    p = argparse.ArgumentParser(prog="jobscout")
    sub = p.add_subparsers(dest="cmd", required=True)
    commands: dict[str, Callable[[argparse.Namespace], None]] = {}

    def add(
        name: str, fn: Callable[[argparse.Namespace], None], **kw: Any
    ) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, **kw)
        commands[name] = fn
        return sp

    add("migrate", cmd_migrate, help="apply migrations")
    sp = add("ingest", cmd_ingest, help="fetch postings")
    sp.add_argument("--source", action="append", choices=["arbeitsagentur", "arbeitnow"])
    sp.add_argument("--limit", type=int, default=None, help="max postings per source")
    sp.add_argument("--offline", action="store_true", help="replay recorded fixtures only")
    sp = add("extract", cmd_extract, help="LLM extraction")
    sp.add_argument("--limit", type=int, default=None)
    sp.add_argument("--concurrency", type=int, default=2)
    sp.add_argument("--rules-only", action="store_true", help="fast provisional rule extraction")
    sp.add_argument("--refresh", action="store_true", help="with --rules-only: redo rule rows")
    sp = add("backfill", cmd_backfill, help="load a JobLens backfill JSONL file")
    sp.add_argument("path")
    sp = add("embed", cmd_embed, help="compute embeddings")
    sp.add_argument("--limit", type=int, default=None)
    sp.add_argument("--batch-size", type=int, default=32)
    sp = add("expiry", cmd_expiry, help="check posting URLs")
    sp.add_argument("--limit", type=int, default=None)
    add("insights", cmd_insights, help="refresh insights views")
    sp = add("seed", cmd_seed, help="load recorded fixtures for a demo")
    sp.add_argument("--skip-llm", action="store_true")
    add("openapi", cmd_openapi, help="print the OpenAPI schema")
    sp = add("create-key", cmd_create_key, help="create an API key")
    sp.add_argument("name")
    sp.add_argument("--demo", action="store_true")
    sp = add("feedback-review", cmd_feedback_review, help="review negative feedback")
    sp.add_argument("--approve", type=int, action="append", default=[])
    sp.add_argument("--reject", type=int, action="append", default=[])
    sp = add("mcp", cmd_mcp, help="run the MCP server")
    sp.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    sp.add_argument("--port", type=int, default=8765)

    args = p.parse_args(argv)
    commands[args.cmd](args)


if __name__ == "__main__":  # pragma: no cover
    main()
