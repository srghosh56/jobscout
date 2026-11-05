"""Demo seed for `make up`: works without fresh API calls.

1. Database already has postings → nothing to do.
2. The recorded HTTP cache (.cache/http, git-ignored) exists → offline ingest replay,
   rules extraction, embeddings, insights.
3. Otherwise → the synthetic demo corpus (fictional employers), clearly marked.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from jobscout.db.engine import session_scope
from jobscout.db.models import Posting
from jobscout.logging import get_logger
from jobscout.settings import get_settings

log = get_logger(__name__)


async def seed(skip_llm: bool = True) -> dict[str, Any]:
    from jobscout.embed import run_embedding
    from jobscout.extract.runner import run_extraction, run_rules_extraction
    from jobscout.insights import refresh_insights

    async with session_scope() as s:
        n = (await s.execute(select(func.count()).select_from(Posting))).scalar_one()
    if n:
        return {"seeded": False, "reason": f"database already has {n} postings"}
    cache = get_settings().http_cache_dir
    out: dict[str, Any] = {"seeded": True}
    if cache.exists() and any(cache.rglob("*.json")):
        from jobscout.ingest.run import run_ingest

        out["source"] = "recorded HTTP cache (offline replay)"
        out["ingest"] = await run_ingest(offline=True)
        out["extract"] = await run_rules_extraction()
        if not skip_llm:
            out["llm_extract"] = await run_extraction()
    else:
        from jobscout.evaluation.synthetic_corpus import load

        out["source"] = "synthetic demo corpus (fictional employers)"
        async with session_scope() as s:
            await load(s)
    try:
        out["embed"] = await run_embedding()
    except Exception as exc:  # embedding model unreachable: search falls back to full text
        log.warning("seed_embed_failed", error=type(exc).__name__)
        out["embed"] = f"skipped ({type(exc).__name__}); full-text search still works"
    out["insights"] = await refresh_insights()
    return out
