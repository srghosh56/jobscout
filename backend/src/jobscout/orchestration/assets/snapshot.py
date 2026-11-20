"""Columnar snapshot: Postgres (row store, serves the app) → Parquet → DuckDB (analytics).

Each run writes the active, de-duplicated postings with their extracted fields to
``<snapshot_dir>/parquet/snapshot_date=YYYY-MM-DD/postings.parquet`` (rewriting the same
date is idempotent) and reloads every snapshot into ``<snapshot_dir>/jobscout.duckdb``.
Posting text is masked (e-mail addresses, phone numbers) before it is written.
"""

import datetime as dt
import time
from pathlib import Path
from typing import Any

import dagster as dg

from jobscout.orchestration import masking
from jobscout.orchestration.assets.pipeline import salary_bluecard
from jobscout.orchestration.resources import JobScoutDb, JobScoutSettings

GROUP = "snapshot"
PARQUET_FILE = "postings.parquet"
DUCKDB_FILE = "jobscout.duckdb"
TABLE = "postings_snapshot"

SNAPSHOT_SQL = """
SELECT id, source, title, company, city, job_family, posting_language, remote_policy, seniority,
       min_years_experience, required_skills, german_level, english_sufficient, contract_type,
       salary_min, salary_max, salary_currency, salary_period, annual_salary_min_eur,
       annual_salary_max_eur, salary_stated, bluecard_status, visa_sponsorship, relocation_support,
       CASE WHEN model_version LIKE 'rules:%' THEN 'rules' ELSE 'llm' END AS extraction_method,
       published_at, first_seen_at, raw_text
FROM postings
WHERE is_active AND duplicate_of IS NULL AND extraction_status = 'ok'
ORDER BY id"""

# Example analytical queries over all snapshots. Cells below the minimum size are hidden,
# as in the public insights (jobscout.insights.MIN_N).
QUERIES: dict[str, str] = {
    "english_friendly_by_city": """
SELECT snapshot_date, city, count(*) AS n,
       round(avg(CASE WHEN english_sufficient = 'yes' THEN 1 ELSE 0 END), 3) AS share_english_friendly
FROM postings_snapshot WHERE city IS NOT NULL
GROUP BY snapshot_date, city HAVING count(*) >= $min_n
ORDER BY snapshot_date, n DESC, city""",
    "salary_transparency_by_source": """
SELECT snapshot_date, source, count(*) AS n,
       round(avg(CASE WHEN salary_stated THEN 1 ELSE 0 END), 3) AS share_salary_stated,
       median(annual_salary_min_eur) FILTER (WHERE salary_stated) AS median_annual_min_eur
FROM postings_snapshot
GROUP BY snapshot_date, source HAVING count(*) >= $min_n
ORDER BY snapshot_date, source""",
    "top_skills_english_friendly": """
SELECT skill, count(*) AS n
FROM (SELECT unnest(required_skills) AS skill FROM postings_snapshot
      WHERE english_sufficient = 'yes'
        AND snapshot_date = (SELECT max(snapshot_date) FROM postings_snapshot))
GROUP BY skill HAVING count(*) >= $min_n
ORDER BY n DESC, skill LIMIT 10""",
}


def parquet_root(base: Path) -> Path:
    return base / "parquet"


def snapshot_file(base: Path, day: dt.date) -> Path:
    return parquet_root(base) / f"snapshot_date={day.isoformat()}" / PARQUET_FILE


def load_snapshot_rows(db: JobScoutDb) -> list[dict[str, Any]]:
    """Rows for the snapshot, straight from Postgres (text not yet masked)."""
    return db.fetch_all(SNAPSHOT_SQL)


def to_records(rows: list[dict[str, Any]], day: dt.date) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        rec = {k: v for k, v in r.items() if k != "raw_text"}
        rec["text_masked"] = masking.mask_contacts(r.get("raw_text") or "")
        rec["snapshot_date"] = day
        out.append(rec)
    return out


def write_parquet(records: list[dict[str, Any]], path: Path) -> int:
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema(
        [
            ("id", pa.string()),
            ("source", pa.string()),
            ("title", pa.string()),
            ("company", pa.string()),
            ("city", pa.string()),
            ("job_family", pa.string()),
            ("posting_language", pa.string()),
            ("remote_policy", pa.string()),
            ("seniority", pa.string()),
            ("min_years_experience", pa.int16()),
            ("required_skills", pa.list_(pa.string())),
            ("german_level", pa.string()),
            ("english_sufficient", pa.string()),
            ("contract_type", pa.string()),
            ("salary_min", pa.float64()),
            ("salary_max", pa.float64()),
            ("salary_currency", pa.string()),
            ("salary_period", pa.string()),
            ("annual_salary_min_eur", pa.float64()),
            ("annual_salary_max_eur", pa.float64()),
            ("salary_stated", pa.bool_()),
            ("bluecard_status", pa.string()),
            ("visa_sponsorship", pa.string()),
            ("relocation_support", pa.string()),
            ("extraction_method", pa.string()),
            ("published_at", pa.date32()),
            ("first_seen_at", pa.timestamp("us", tz="UTC")),
            ("text_masked", pa.string()),
            ("snapshot_date", pa.date32()),
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(records, schema=schema)
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(path)  # atomic: readers never see a half-written file
    return path.stat().st_size


def load_duckdb(base: Path) -> int:
    """(Re)build the DuckDB table from every Parquet snapshot. Returns its row count."""
    import duckdb

    glob = str(parquet_root(base) / "snapshot_date=*" / PARQUET_FILE)
    with duckdb.connect(str(base / DUCKDB_FILE)) as con:
        con.execute(
            "CREATE OR REPLACE TABLE postings_snapshot AS "
            "SELECT * FROM read_parquet($glob, hive_partitioning = false)",
            {"glob": glob},
        )
        row = con.execute("SELECT count(*) FROM postings_snapshot").fetchone()
    return int(row[0]) if row else 0


@dg.asset(
    group_name=GROUP,
    deps=[salary_bluecard],
    kinds={"parquet", "duckdb"},
    description="Active postings plus extracted fields as Parquet (one folder per snapshot "
    "date, text masked), loaded into a DuckDB file for analytics.",
)
def postings_snapshot(
    context: dg.AssetExecutionContext, db: JobScoutDb, jobscout: JobScoutSettings
) -> dg.MaterializeResult[Any]:
    t0 = time.monotonic()
    base = jobscout.snapshot_path
    day = dt.datetime.now(dt.UTC).date()
    records = to_records(load_snapshot_rows(db), day)
    path = snapshot_file(base, day)
    size = write_parquet(records, path)
    total = load_duckdb(base)
    snapshots = sorted(p.name for p in parquet_root(base).glob("snapshot_date=*"))
    context.log.info(f"snapshot {day} written: {len(records)} rows")
    return dg.MaterializeResult(
        metadata={
            "snapshot_date": day.isoformat(),
            "rows": len(records),
            "parquet_path": dg.MetadataValue.path(str(path)),
            "parquet_bytes": size,
            "duckdb_rows_all_snapshots": total,
            "snapshots": len(snapshots),
            "duration_s": round(time.monotonic() - t0, 2),
        }
    )


class SnapshotInsightsConfig(dg.Config):
    min_cell_size: int = 5


def run_queries(base: Path, min_n: int) -> dict[str, list[dict[str, Any]]]:
    import duckdb

    out: dict[str, list[dict[str, Any]]] = {}
    with duckdb.connect(str(base / DUCKDB_FILE), read_only=True) as con:
        for name, sql in QUERIES.items():
            cur = con.execute(sql, {"min_n": min_n})
            cols = [d[0] for d in cur.description or []]
            out[name] = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    return out


def _markdown(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_no cell reaches the minimum size_"
    cols = list(rows[0])
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    return "\n".join(lines)


@dg.asset(
    group_name=GROUP,
    deps=[postings_snapshot],
    kinds={"duckdb"},
    description="Example analytical SQL in DuckDB over all snapshots; results are stored "
    "as materialisation metadata.",
)
def snapshot_insights(
    config: SnapshotInsightsConfig, jobscout: JobScoutSettings
) -> dg.MaterializeResult[Any]:
    results = run_queries(jobscout.snapshot_path, config.min_cell_size)
    metadata: dict[str, Any] = {"min_cell_size": config.min_cell_size}
    for name, rows in results.items():
        metadata[name] = dg.MetadataValue.md(_markdown(rows))
        metadata[f"{name}_rows"] = len(rows)
    return dg.MaterializeResult(metadata=metadata)
