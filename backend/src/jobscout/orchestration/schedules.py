"""Jobs and schedules: the full pipeline weekly, the expiry checks daily."""

import dagster as dg

from jobscout.orchestration.assets.iceberg import SNAPSHOT_ASSETS
from jobscout.orchestration.assets.pipeline import (
    PIPELINE_ASSETS,
    SOURCE_PARTITIONS,
    posting_expiry,
)

TIMEZONE = "Europe/Berlin"
# Partition-range tags: one run materialises every source partition in a single step
# (raw_postings has a single-run backfill policy), then the downstream assets run once.
RANGE_START = "dagster/asset_partition_range_start"
RANGE_END = "dagster/asset_partition_range_end"

weekly_pipeline_job = dg.define_asset_job(
    "weekly_pipeline",
    selection=dg.AssetSelection.assets(*PIPELINE_ASSETS, *SNAPSHOT_ASSETS),
    description="ingest (both sources) → dedup → scope → extract → salary/Blue Card → "
    "embeddings → insights views → snapshot, with all checks.",
)
expiry_job = dg.define_asset_job(
    "posting_expiry_job",
    selection=dg.AssetSelection.assets(posting_expiry),
    description="Check posting URLs (oldest-checked first).",
)


def all_sources_tags() -> dict[str, str]:
    keys = SOURCE_PARTITIONS.get_partition_keys()
    return {RANGE_START: keys[0], RANGE_END: keys[-1]}


@dg.schedule(
    job=weekly_pipeline_job,
    cron_schedule="0 3 * * 1",  # Mondays 03:00, Berlin time
    execution_timezone=TIMEZONE,
    default_status=dg.DefaultScheduleStatus.RUNNING,
)
def weekly_pipeline_schedule(context: dg.ScheduleEvaluationContext) -> dg.RunRequest:
    week = context.scheduled_execution_time.strftime("%G-W%V")
    return dg.RunRequest(
        run_key=f"weekly-{week}", tags={**all_sources_tags(), "jobscout/week": week}
    )


daily_expiry_schedule = dg.ScheduleDefinition(
    name="daily_expiry_schedule",
    job=expiry_job,
    cron_schedule="30 4 * * *",  # daily 04:30, after the weekly run on Mondays
    execution_timezone=TIMEZONE,
    default_status=dg.DefaultScheduleStatus.RUNNING,
)

SCHEDULES = [weekly_pipeline_schedule, daily_expiry_schedule]
JOBS = [weekly_pipeline_job, expiry_job]
