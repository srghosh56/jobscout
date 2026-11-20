"""The same snapshot as an Apache Iceberg table (PyIceberg, local SQL catalog on SQLite).

Iceberg adds what plain Parquet folders lack: atomic commits, schema evolution and time
travel over table snapshots. Everything stays local: the catalog is a SQLite file and the
warehouse is a folder next to the Parquet snapshots.

Each run replaces the rows of today's snapshot date (overwrite with a filter = one delete
and one append commit), so a rerun is idempotent. The time-travel query reads the table as
it was before this run's commits.
"""

import datetime as dt
import time
from pathlib import Path
from typing import Any

import dagster as dg

from jobscout.orchestration.assets.snapshot import (
    postings_snapshot,
    snapshot_file,
    snapshot_insights,
)
from jobscout.orchestration.resources import JobScoutSettings

NAMESPACE = "jobscout"
TABLE_ID = f"{NAMESPACE}.postings_snapshot"


def iceberg_catalog(base: Path) -> Any:
    from pyiceberg.catalog.sql import SqlCatalog

    root = base / "iceberg"
    (root / "warehouse").mkdir(parents=True, exist_ok=True)
    return SqlCatalog(
        "jobscout",
        uri=f"sqlite:///{root / 'catalog.db'}",
        warehouse=(root / "warehouse").as_uri(),
    )


def write_iceberg(base: Path, day: dt.date) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pq.read_table(snapshot_file(base, day))
    # Iceberg has no 16-bit integer type.
    table = table.cast(
        pa.schema([f.with_type(pa.int32()) if f.type == pa.int16() else f for f in table.schema])
    )
    catalog = iceberg_catalog(base)
    catalog.create_namespace_if_not_exists(NAMESPACE)
    ice = catalog.create_table_if_not_exists(TABLE_ID, schema=table.schema)
    before = ice.current_snapshot()
    # Row filter in PyIceberg's expression syntax; ``day`` is a date, never user input.
    ice.overwrite(table, overwrite_filter=f"snapshot_date = '{day.isoformat()}'")
    ice = catalog.load_table(TABLE_ID)
    rows_before = (
        ice.scan(snapshot_id=before.snapshot_id).to_arrow().num_rows if before is not None else 0
    )
    return {
        "rows_written": table.num_rows,
        "rows_now": ice.scan().to_arrow().num_rows,
        "rows_before_this_run": rows_before,  # time travel to the pre-run snapshot
        "iceberg_snapshots": len(ice.snapshots()),
    }


@dg.asset(
    group_name="snapshot",
    deps=[postings_snapshot],
    kinds={"iceberg"},
    description="Today's Parquet snapshot written to a local Iceberg table (SQLite catalog), "
    "with a time-travel read of the table before this run.",
)
def postings_iceberg(jobscout: JobScoutSettings) -> dg.MaterializeResult[Any]:
    t0 = time.monotonic()
    day = dt.datetime.now(dt.UTC).date()
    stats = write_iceberg(jobscout.snapshot_path, day)
    return dg.MaterializeResult(
        metadata={**stats, "table": TABLE_ID, "duration_s": round(time.monotonic() - t0, 2)}
    )


SNAPSHOT_ASSETS = [postings_snapshot, snapshot_insights, postings_iceberg]
