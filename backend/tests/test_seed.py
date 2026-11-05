from __future__ import annotations

from pathlib import Path

import pytest

from jobscout.seed import seed
from jobscout.settings import get_settings

pytestmark = pytest.mark.db


async def test_seed_synthetic_then_noop(
    clean_db: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(get_settings(), "http_cache_dir", tmp_path / "nocache")
    r = await seed()
    assert r["seeded"] and r["source"].startswith("synthetic") and r["embed"]["embedded"] == 10
    r2 = await seed()
    assert r2["seeded"] is False
