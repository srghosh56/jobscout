"""Read the latest Parquet snapshot for the snapshot checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jobscout.orchestration.assets.snapshot import PARQUET_FILE, parquet_root


def latest_snapshot(base: Path, columns: list[str]) -> tuple[Path, list[dict[str, Any]]]:
    import pyarrow.parquet as pq

    files = sorted(parquet_root(base).glob(f"snapshot_date=*/{PARQUET_FILE}"))
    if not files:
        raise FileNotFoundError(f"no snapshot under {parquet_root(base)}")
    path = files[-1]  # folder names sort by ISO date
    return path, pq.read_table(path, columns=columns).to_pylist()
