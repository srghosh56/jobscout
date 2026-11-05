"""Database administration: migrations and read-only role setup."""

from __future__ import annotations

from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import make_url

from jobscout.db.engine import sync_url
from jobscout.settings import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[3]


def alembic_config(database_url: str | None = None) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    url = sync_url(database_url or get_settings().database_url)
    # ConfigParser interpolation: escape % in passwords.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def _psycopg_dsn(url: str) -> str:
    u = make_url(url)
    return u.set(drivername="postgresql").render_as_string(hide_password=False)


def configure_readonly_role(database_url: str | None = None, ro_url: str | None = None) -> None:
    """Give the read-only role LOGIN and the password embedded in READONLY_DATABASE_URL."""
    s = get_settings()
    ro = make_url(ro_url or s.readonly_database_url)
    if not ro.username or ro.password is None:
        raise ValueError("READONLY_DATABASE_URL must include a username and password")
    with psycopg.connect(_psycopg_dsn(database_url or s.database_url), autocommit=True) as conn:
        conn.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(ro.username), sql.Literal(str(ro.password))
            )
        )


def upgrade(database_url: str | None = None, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)
    configure_readonly_role(database_url)


def downgrade(database_url: str | None = None, revision: str = "base") -> None:
    command.downgrade(alembic_config(database_url), revision)


def ensure_database(database_url: str) -> None:
    """Create the database in ``database_url`` if it doesn't exist (tests, first run)."""
    u = make_url(database_url)
    admin = u.set(database="postgres")
    with psycopg.connect(_psycopg_dsn(admin.render_as_string(False)), autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (u.database,)
        ).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(str(u.database))))
