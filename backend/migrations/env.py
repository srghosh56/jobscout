"""Alembic environment. Migrations run with the synchronous psycopg driver."""

from __future__ import annotations

from typing import Any

from alembic import context
from sqlalchemy import create_engine, pool

from jobscout.db.engine import sync_url
from jobscout.db.models import Base
from jobscout.settings import get_settings

config = context.config
target_metadata = Base.metadata

# Objects managed by raw SQL in migrations (views, matviews), not by the ORM.
IGNORED_TABLES = {"v_postings", "v_posting_skills", "v_posting_languages"}


def include_object(obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any) -> bool:
    if type_ == "table" and name is not None and (name in IGNORED_TABLES or name.startswith("mv_")):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url") or sync_url(get_settings().database_url),
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url") or sync_url(get_settings().database_url)
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
            compare_server_default=False,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
