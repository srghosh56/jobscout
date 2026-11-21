"""Software-defined assets for the weekly pipeline and the expiry checks.

Each asset is a thin wrapper around the function the CLI already calls (``make ingest``,
``make extract`` …), so both paths share the same idempotent code. Assets return counts
and durations as materialisation metadata; Dagster keeps them per run for comparison.
"""

import time
from typing import Any

import dagster as dg

from jobscout.ingest.run import SOURCES
from jobscout.orchestration.resources import JobScoutDb, JobScoutSettings

GROUP = "postings"

# Static partitions: one per job board, so one source can be re-run or backfilled alone.
SOURCE_PARTITIONS = dg.StaticPartitionsDefinition(list(SOURCES))

# Network steps (job APIs, LLM, posting URLs). The HTTP client already retries single
# requests; this retries the whole step after an outage: about 1, 3 and 7 min (+/- up to 60 s).
NETWORK_RETRY = dg.RetryPolicy(
    max_retries=3, delay=60, backoff=dg.Backoff.EXPONENTIAL, jitter=dg.Jitter.PLUS_MINUS
)
# Local model calls (embeddings) only need a short retry.
MODEL_RETRY = dg.RetryPolicy(max_retries=2, delay=15, backoff=dg.Backoff.EXPONENTIAL)

ACTIVE = "is_active AND duplicate_of IS NULL"
EXTRACTION_METHOD_SQL = """
SELECT CASE WHEN extraction_status <> 'ok' THEN extraction_status
            WHEN model_version LIKE 'rules:%' THEN 'rules' ELSE 'llm' END AS method,
       count(*) AS n
FROM postings WHERE is_active AND duplicate_of IS NULL GROUP BY 1 ORDER BY 1"""


def _seconds(t0: float) -> float:
    return round(time.monotonic() - t0, 2)


@dg.asset(
    group_name=GROUP,
    partitions_def=SOURCE_PARTITIONS,
    backfill_policy=dg.BackfillPolicy.single_run(),
    retry_policy=NETWORK_RETRY,
    kinds={"python", "postgres"},
    description="Postings fetched from one job board (polite, cached client) and upserted "
    "idempotently by (source, source_id).",
)
def raw_postings(
    context: dg.AssetExecutionContext, db: JobScoutDb, jobscout: JobScoutSettings
) -> dg.MaterializeResult[Any]:
    from jobscout.ingest.run import ingest_source, source_stream

    async def ingest() -> list[dict[str, Any]]:
        results = []
        for name in context.partition_keys:
            async with jobscout.make_client() as client:
                stream = source_stream(name, client, jobscout.limit)
                results.append(await ingest_source(name, stream, client))
        return results

    t0 = time.monotonic()
    results = db.run(ingest)
    failed = [r["source"] for r in results if r["status"] != "ok"]
    if failed:
        # Rows fetched before the error are already upserted, so a retry is cheap.
        raise dg.Failure(f"ingestion failed for {', '.join(failed)}", allow_retries=True)
    totals = {k: sum(int(r[k]) for r in results) for k in ("fetched", "inserted", "updated")}
    return dg.MaterializeResult(
        metadata={
            "sources": ", ".join(context.partition_keys),
            "mode": jobscout.mode,
            "fetched": totals["fetched"],
            "inserted": totals["inserted"],
            "updated": totals["updated"],
            "unchanged": sum(int(r["unchanged"]) for r in results),
            "by_source": {
                r["source"]: {k: r[k] for k in ("fetched", "inserted", "updated", "unchanged")}
                for r in results
            },
            "ingest_run_ids": [int(r["run_id"]) for r in results],
            "duration_s": _seconds(t0),
        }
    )


@dg.asset(
    group_name=GROUP,
    deps=[raw_postings],
    kinds={"python", "postgres"},
    description="Cross-source near-duplicates marked with MinHash + LSH (Jaccard >= 0.85).",
)
def deduplicated_postings(db: JobScoutDb) -> dg.MaterializeResult[Any]:
    from jobscout.ingest.dedup import DEFAULT_THRESHOLD
    from jobscout.ingest.run import run_dedup

    t0 = time.monotonic()
    duplicates = db.run(lambda: run_dedup(threshold=DEFAULT_THRESHOLD))
    return dg.MaterializeResult(
        metadata={
            "duplicates_marked": duplicates,
            "threshold": DEFAULT_THRESHOLD,
            "duration_s": _seconds(t0),
        }
    )


@dg.asset(
    group_name=GROUP,
    deps=[deduplicated_postings],
    kinds={"python", "postgres"},
    description="Out-of-scope postings (outside Germany) pruned and Arbeitnow's foreign "
    "sites retired.",
)
def in_scope_postings(db: JobScoutDb) -> dg.MaterializeResult[Any]:
    from jobscout.ingest.run import prune_out_of_scope, retire_foreign_sites

    async def scope() -> tuple[int, int]:
        return await prune_out_of_scope(), await retire_foreign_sites()

    t0 = time.monotonic()
    pruned, retired = db.run(scope)
    active = db.scalar("SELECT count(*) FROM postings WHERE is_active AND duplicate_of IS NULL")
    return dg.MaterializeResult(
        metadata={
            "pruned_out_of_scope": pruned,
            "retired_foreign_sites": retired,
            "active_postings": int(active),
            "duration_s": _seconds(t0),
        }
    )


@dg.asset(
    group_name=GROUP,
    deps=[in_scope_postings],
    retry_policy=NETWORK_RETRY,
    kinds={"python", "postgres"},
    description="Rule-based extraction for every pending posting, then (only when enabled) "
    "LLM JSON-schema extraction that upgrades rule rows.",
)
def extracted_fields(db: JobScoutDb, jobscout: JobScoutSettings) -> dg.MaterializeResult[Any]:
    from jobscout.extract.runner import run_extraction, run_rules_extraction

    t0 = time.monotonic()
    rules = db.run(run_rules_extraction)
    metadata: dict[str, Any] = {"rules_extracted": int(rules["rules_extracted"])}
    if jobscout.llm_enabled:
        llm = db.run(lambda: run_extraction(limit=jobscout.llm_max))
        metadata.update(
            llm_ok=int(llm["ok"]),
            llm_failed=int(llm["failed"]),
            llm_model=str(llm["model_version"]),
        )
        if llm["total"] and llm["failed"] > 0.05 * llm["total"]:
            raise dg.Failure(
                f"LLM extraction failed for {llm['failed']} of {llm['total']}", allow_retries=True
            )
    else:
        metadata["llm"] = "disabled (PIPELINE_LLM_EXTRACTION=false)"
    by_method = {r["method"]: int(r["n"]) for r in db.fetch_all(EXTRACTION_METHOD_SQL)}
    metadata.update(by_method=by_method, duration_s=_seconds(t0))
    return dg.MaterializeResult(metadata=metadata)


@dg.asset(
    group_name=GROUP,
    deps=[extracted_fields],
    kinds={"python", "postgres"},
    description="Salary columns re-derived from the text and the indicative EU Blue Card status.",
)
def salary_bluecard(db: JobScoutDb) -> dg.MaterializeResult[Any]:
    from jobscout.extract.runner import refresh_salaries

    t0 = time.monotonic()
    stats = db.run(refresh_salaries)
    status = db.fetch_all(
        "SELECT bluecard_status, count(*) AS n FROM postings WHERE is_active AND duplicate_of IS NULL GROUP BY 1 ORDER BY 1"
    )
    return dg.MaterializeResult(
        metadata={
            "checked": int(stats["checked"]),
            "changed": int(stats["changed"]),
            "bluecard_status": {r["bluecard_status"]: int(r["n"]) for r in status},
            "duration_s": _seconds(t0),
        }
    )


@dg.asset(
    group_name=GROUP,
    deps=[in_scope_postings],
    retry_policy=MODEL_RETRY,
    kinds={"python", "pgvector"},
    description="Embeddings for postings without a vector (cached by text hash and model).",
)
def posting_embeddings(db: JobScoutDb) -> dg.MaterializeResult[Any]:
    from jobscout.embed import run_embedding

    stats = db.run(run_embedding)
    return dg.MaterializeResult(
        metadata={
            "embedded": int(stats["embedded"]),
            "model": str(stats["model"]),
            "duration_s": float(stats["seconds"]),
        }
    )


@dg.asset(
    group_name=GROUP,
    deps=[salary_bluecard, posting_embeddings],
    kinds={"python", "postgres"},
    description="Insights materialised views refreshed (concurrently, readers never block).",
)
def insights_views(db: JobScoutDb) -> dg.MaterializeResult[Any]:
    from jobscout.insights import refresh_insights

    t0 = time.monotonic()
    out = db.run(refresh_insights)
    return dg.MaterializeResult(
        metadata={
            "views": ", ".join(out["refreshed"]),
            "active_postings": int(out["active_postings"]),
            "duration_s": _seconds(t0),
        }
    )


@dg.asset(
    group_name="maintenance",
    retry_policy=NETWORK_RETRY,
    kinds={"python", "postgres"},
    description="Posting URLs checked; a posting becomes inactive after two consecutive "
    "'gone' results. Runs on its own daily schedule, oldest-checked first. Skipped in "
    "offline mode.",
)
def posting_expiry(db: JobScoutDb, jobscout: JobScoutSettings) -> dg.MaterializeResult[Any]:
    from jobscout.expiry import run_expiry

    if jobscout.mode == "offline":
        # Offline means no requests at all; checking URLs would contact the job sites.
        return dg.MaterializeResult(metadata={"skipped": "offline mode makes no requests"})
    t0 = time.monotonic()
    fixtures = jobscout.mode == "fixtures"
    stats = db.run(
        lambda: run_expiry(
            limit=jobscout.expiry_max,
            transport=jobscout.fixture_transport() if fixtures else None,
            min_interval_s=0.0 if fixtures else 2.0,
        )
    )
    return dg.MaterializeResult(
        metadata={**{k: int(v) for k, v in stats.items()}, "duration_s": _seconds(t0)}
    )


PIPELINE_ASSETS = [
    raw_postings,
    deduplicated_postings,
    in_scope_postings,
    extracted_fields,
    salary_bluecard,
    posting_embeddings,
    insights_views,
]
