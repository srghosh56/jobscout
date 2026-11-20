"""Warning checks (severity WARN, not blocking): they report in the UI and the metrics
but never stop the pipeline, because stale or thin data is still better than none."""

import datetime as dt
from typing import Any

import dagster as dg

from jobscout.orchestration.assets.pipeline import raw_postings
from jobscout.orchestration.resources import JobScoutDb

# The pipeline runs weekly; one missed run plus a day of slack is tolerated.
MAX_AGE = dt.timedelta(days=8)
# Warn when a source returns less than half of its previous successful run. Weekly
# volumes move with season and holidays; a halving points to an API change, a blocked
# client or an empty cache instead. The value is a judgement, not fitted to data.
MIN_VOLUME_RATIO = 0.5

PREVIOUS_RUNS_SQL = """
SELECT source, fetched, started_at FROM (
  SELECT source, fetched, started_at,
         row_number() OVER (PARTITION BY source ORDER BY id DESC) AS rn
  FROM ingest_runs WHERE kind = 'ingest' AND status = 'ok' AND finished_at IS NOT NULL
) t WHERE rn <= 2 ORDER BY source, rn"""


def is_fresh(
    last_seen: dt.datetime | None, now: dt.datetime, max_age: dt.timedelta = MAX_AGE
) -> bool:
    return last_seen is not None and now - last_seen <= max_age


def volume_drops(
    runs: list[dict[str, Any]], ratio: float = MIN_VOLUME_RATIO
) -> dict[str, tuple[int, int]]:
    """{source: (latest, previous)} for sources whose latest run fell below ``ratio``.
    ``runs`` holds up to two rows per source, newest first."""
    by_source: dict[str, list[int]] = {}
    for r in runs:
        by_source.setdefault(r["source"], []).append(int(r["fetched"]))
    return {
        s: (vals[0], vals[1])
        for s, vals in by_source.items()
        if len(vals) == 2 and vals[1] > 0 and vals[0] < ratio * vals[1]
    }


@dg.asset_check(
    asset=raw_postings,
    description=f"The newest active posting was seen less than {MAX_AGE.days} days ago.",
)
def raw_postings_fresh(db: JobScoutDb) -> dg.AssetCheckResult:
    last_seen = db.scalar(
        "SELECT max(last_seen_at) FROM postings WHERE is_active AND duplicate_of IS NULL"
    )
    now = dt.datetime.now(dt.UTC)
    age_h = round((now - last_seen).total_seconds() / 3600, 1) if last_seen else None
    return dg.AssetCheckResult(
        passed=is_fresh(last_seen, now),
        severity=dg.AssetCheckSeverity.WARN,
        metadata={"last_seen_at": str(last_seen), "age_hours": age_h, "max_age_days": MAX_AGE.days},
    )


@dg.asset_check(
    asset=raw_postings,
    description="No source fetched less than half of its previous successful run.",
)
def ingest_volume_stable(db: JobScoutDb) -> dg.AssetCheckResult:
    runs = db.fetch_all(PREVIOUS_RUNS_SQL)
    drops = volume_drops(runs)
    return dg.AssetCheckResult(
        passed=not drops,
        severity=dg.AssetCheckSeverity.WARN,
        metadata={
            "drops": str(drops),
            "min_ratio": MIN_VOLUME_RATIO,
            "latest_fetched": {r["source"]: int(r["fetched"]) for r in runs[::-1]},
        },
    )


QUALITY_CHECKS = [raw_postings_fresh, ingest_volume_stable]
