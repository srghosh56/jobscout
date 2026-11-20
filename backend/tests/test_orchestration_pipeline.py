"""Materialise the Dagster assets in-process against the test Postgres.

Ingestion runs in ``fixtures`` mode (fictional payloads served in-process: no network)
with the fake LLM/embedding providers, so no model is needed. Good fixtures must pass
every check; each bad fixture must fail its blocking check and stop the assets below it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

dg = pytest.importorskip("dagster")

from sqlalchemy import text  # noqa: E402

from jobscout.db.engine import dispose_engines, session_scope  # noqa: E402
from jobscout.orchestration.definitions import ASSETS, CHECKS  # noqa: E402
from jobscout.orchestration.resources import JobScoutDb, JobScoutSettings  # noqa: E402
from jobscout.orchestration.schedules import JOBS, all_sources_tags  # noqa: E402

pytestmark = pytest.mark.db

RUN_CONFIG = {"ops": {"snapshot_insights": {"config": {"min_cell_size": 1}}}}


@pytest.fixture
async def empty_db(clean_db: None) -> AsyncIterator[None]:
    # The steps run their own event loops; release the test loop's connections first.
    await dispose_engines()
    yield


@pytest.fixture
def defs(tmp_path: Path) -> Iterator[Any]:
    yield dg.Definitions(
        assets=ASSETS,
        asset_checks=CHECKS,
        jobs=JOBS,
        resources={
            "db": JobScoutDb(),
            "jobscout": JobScoutSettings(ingest_mode="fixtures", snapshot_dir=str(tmp_path)),
        },
    )


def run_pipeline(defs: Any, instance: Any = None) -> Any:
    job = defs.resolve_job_def("weekly_pipeline")
    return job.execute_in_process(
        instance=instance or dg.DagsterInstance.ephemeral(),
        tags=all_sources_tags(),
        run_config=RUN_CONFIG,
        raise_on_error=False,
    )


def materialized(result: Any) -> dict[str, dict[str, Any]]:
    out = {}
    for e in result.get_asset_materialization_events():
        m = e.event_specific_data.materialization
        out[m.asset_key.to_user_string()] = {k: v.value for k, v in m.metadata.items()}
    return out


def checks(result: Any) -> dict[str, bool]:
    return {
        e.event_specific_data.check_name: e.event_specific_data.passed
        for e in result.all_events
        if e.event_type_value == "ASSET_CHECK_EVALUATION"
    }


def sql(statement: str) -> None:
    from jobscout.orchestration.resources import run_async

    async def go() -> None:
        async with session_scope() as s:
            await s.execute(text(statement))
            await s.commit()

    run_async(go)


def test_pipeline_on_good_fixtures_passes_every_check(empty_db: None, defs: Any) -> None:
    result = run_pipeline(defs)
    assert result.success
    mats = materialized(result)
    assert mats["raw_postings"]["fetched"] == 6
    assert mats["raw_postings"]["inserted"] == 6
    assert mats["deduplicated_postings"]["duplicates_marked"] == 1  # the same ML job on both
    assert mats["in_scope_postings"]["active_postings"] == 5  # duplicates excluded
    assert mats["extracted_fields"]["rules_extracted"] == 5  # duplicates are not extracted
    assert mats["extracted_fields"]["by_method"] == {"rules": 5}
    assert mats["salary_bluecard"]["checked"] == 5
    assert mats["posting_embeddings"]["embedded"] == 5
    assert mats["postings_snapshot"]["rows"] == 5
    assert mats["snapshot_insights"]["salary_transparency_by_source_rows"] == 2
    assert mats["postings_iceberg"]["rows_now"] == 5
    assert len(checks(result)) == 10 and all(checks(result).values())

    again = run_pipeline(defs)  # idempotent: nothing new, nothing changed
    assert again.success
    mats = materialized(again)
    assert (mats["raw_postings"]["inserted"], mats["raw_postings"]["unchanged"]) == (0, 6)
    assert mats["posting_embeddings"]["embedded"] == 0
    assert mats["postings_snapshot"]["duckdb_rows_all_snapshots"] == 5  # same date rewritten
    iceberg = mats["postings_iceberg"]
    assert (iceberg["rows_before_this_run"], iceberg["rows_now"]) == (5, 5)  # time travel
    assert iceberg["iceberg_snapshots"] > 1


def test_single_source_partition_backfill(empty_db: None, defs: Any) -> None:
    job = defs.resolve_job_def("weekly_pipeline")
    result = job.execute_in_process(
        partition_key="arbeitnow", run_config=RUN_CONFIG, raise_on_error=False
    )
    assert result.success
    assert materialized(result)["raw_postings"]["by_source"] == {
        "arbeitnow": {"fetched": 3, "inserted": 3, "updated": 0, "unchanged": 0}
    }


def test_expiry_job_in_fixture_mode(empty_db: None, defs: Any) -> None:
    assert run_pipeline(defs).success
    result = defs.resolve_job_def("posting_expiry_job").execute_in_process(raise_on_error=False)
    assert result.success
    stats = materialized(result)["posting_expiry"]
    assert (stats["checked"], stats["alive"], stats["deactivated"]) == (5, 5, 0)


def test_malformed_posting_id_blocks_everything_downstream(empty_db: None, defs: Any) -> None:
    sql(
        "INSERT INTO postings (id, source, source_id, url, title, raw_text, content_hash) "
        "VALUES ('BAD ID', 'arbeitnow', 'bad', 'https://example.org', 'T', 'x', 'h')"
    )
    result = run_pipeline(defs)
    assert not result.success
    assert checks(result)["posting_ids_unique"] is False
    assert "deduplicated_postings" not in materialized(result)


def test_invalid_extraction_blocks_salary_and_snapshot(empty_db: None, defs: Any) -> None:
    assert run_pipeline(defs).success
    sql(
        "UPDATE postings SET seniority = 'wizard' WHERE id = (SELECT min(id) FROM postings WHERE duplicate_of IS NULL)"
    )
    result = run_pipeline(defs)
    assert not result.success
    assert checks(result)["extractions_match_schema"] is False
    mats = materialized(result)
    assert "extracted_fields" in mats
    assert not {"salary_bluecard", "insights_views", "postings_snapshot"} & set(mats)


def test_duplicate_ids_in_snapshot_block_insights(
    empty_db: None, defs: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jobscout.orchestration.assets import snapshot

    real = snapshot.load_snapshot_rows
    monkeypatch.setattr(snapshot, "load_snapshot_rows", lambda db: real(db) * 2)
    result = run_pipeline(defs)
    assert not result.success
    assert checks(result)["snapshot_ids_unique"] is False
    assert "snapshot_insights" not in materialized(result)


def test_unmasked_email_in_snapshot_blocks_insights(
    empty_db: None, defs: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jobscout.orchestration import masking

    monkeypatch.setattr(masking, "mask_contacts", lambda text: text)
    result = run_pipeline(defs)
    assert not result.success
    assert checks(result)["snapshot_contacts_masked"] is False
    assert "snapshot_insights" not in materialized(result)


def test_cv_text_in_the_database_blocks_the_pipeline(empty_db: None, defs: Any) -> None:
    sql("""INSERT INTO cv_profiles (profile) VALUES ('{"headline": "x", "cv_text": "..."}')""")
    result = run_pipeline(defs)
    assert not result.success
    assert checks(result)["no_cv_text_stored"] is False
    assert "deduplicated_postings" not in materialized(result)


def test_ingest_failure_raises_a_retryable_failure(
    empty_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jobscout.ingest import run as ingest_run
    from jobscout.orchestration.assets.pipeline import NETWORK_RETRY, raw_postings

    async def broken(name: str, stream: Any, client: Any = None) -> dict[str, Any]:
        return {"run_id": 0, "source": name, "status": "failed", "fetched": 0}

    monkeypatch.setattr(ingest_run, "ingest_source", broken)
    context = dg.build_asset_context(partition_key="arbeitnow")
    with pytest.raises(dg.Failure, match="ingestion failed for arbeitnow") as exc:
        raw_postings(context, db=JobScoutDb(), jobscout=JobScoutSettings(ingest_mode="fixtures"))
    assert exc.value.allow_retries
    assert raw_postings.op.retry_policy == NETWORK_RETRY
