from __future__ import annotations

import psycopg
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from jobscout.db import admin
from jobscout.db.engine import sync_url
from jobscout.db.models import Base

pytestmark = pytest.mark.db


def _include(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    return not (type_ == "table" and name is not None and (name.startswith(("v_", "mv_"))))


def test_upgrade_downgrade_upgrade(migrated_db: str) -> None:
    admin.downgrade(migrated_db, "base")
    with psycopg.connect(admin._psycopg_dsn(migrated_db)) as conn:
        tables = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"
        ).fetchall()
    assert tables == []
    admin.upgrade(migrated_db)
    with psycopg.connect(admin._psycopg_dsn(migrated_db)) as conn:
        names = {
            r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        }
    assert {"postings", "ingest_runs", "feedback", "saved_searches", "api_keys"} <= names


def test_no_drift_between_models_and_migrations(migrated_db: str) -> None:
    engine = create_engine(sync_url(migrated_db))
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn, opts={"compare_type": True, "include_object": _include}
        )
        diff = compare_metadata(ctx, Base.metadata)
    engine.dispose()
    assert diff == [], f"models and migrations drifted: {diff}"


def test_readonly_role_cannot_read_base_tables_or_write(migrated_db: str) -> None:
    ro = migrated_db.replace("jobscout:jobscout@", "jobscout_ro:jobscout_ro@")
    with psycopg.connect(admin._psycopg_dsn(ro), autocommit=True) as conn:
        conn.execute("SELECT count(*) FROM v_postings").fetchone()
        for stmt in (
            "SELECT * FROM postings LIMIT 1",
            "SELECT * FROM api_keys LIMIT 1",
            "SELECT * FROM feedback LIMIT 1",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(stmt)
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("CREATE TABLE evil(x int)")
