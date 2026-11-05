"""Test configuration.

Tests use a dedicated database ``jobscout_test`` on the Compose Postgres (port 55433)
or the CI service container. DB tests are skipped automatically if Postgres is down.
No test calls a real LLM or job API: LLM_PROVIDER=fake and HTTP is mocked with respx.
"""

from __future__ import annotations

import os

TEST_DB = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://jobscout:jobscout@localhost:55433/jobscout_test"
)
os.environ["DATABASE_URL"] = TEST_DB
os.environ["READONLY_DATABASE_URL"] = TEST_DB.replace(
    "jobscout:jobscout@", "jobscout_ro:jobscout_ro@"
)
os.environ["LLM_PROVIDER"] = "fake"
os.environ["EMBED_PROVIDER"] = "fake"
os.environ["RERANKER_ENABLED"] = "false"
os.environ["OTEL_ENABLED"] = "false"
os.environ["LOG_JSON"] = "true"
os.environ["MISTRAL_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""

from collections.abc import AsyncIterator, Iterator  # noqa: E402

import psycopg  # noqa: E402
import pytest  # noqa: E402

from jobscout.settings import get_settings  # noqa: E402

get_settings.cache_clear()


def _pg_available() -> bool:
    from jobscout.db.admin import _psycopg_dsn

    try:
        dsn = _psycopg_dsn(TEST_DB.replace("/jobscout_test", "/postgres"))
        with psycopg.connect(dsn, connect_timeout=2):
            return True
    except Exception:
        return False


PG_AVAILABLE = _pg_available()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if PG_AVAILABLE:
        return
    skip = pytest.mark.skip(reason="Postgres not available on TEST_DATABASE_URL")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def migrated_db() -> Iterator[str]:
    from jobscout.db import admin

    admin.ensure_database(TEST_DB)
    admin.upgrade(TEST_DB)
    yield TEST_DB


@pytest.fixture
async def db_session(migrated_db: str) -> AsyncIterator[object]:
    from jobscout.db.engine import get_sessionmaker

    async with get_sessionmaker()() as session:
        yield session


@pytest.fixture
async def clean_db(migrated_db: str) -> AsyncIterator[None]:
    """Truncate data tables before a test that needs an empty database."""
    from sqlalchemy import text

    from jobscout.db.engine import get_engine

    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE postings, ingest_runs, feedback, saved_searches, cv_profiles, "
                "api_keys, llm_cache, embedding_cache RESTART IDENTITY CASCADE"
            )
        )
    yield


@pytest.fixture
async def corpus(clean_db: None) -> AsyncIterator[None]:
    """Synthetic corpus loaded + embedded with the fake hashing embeddings."""
    from jobscout.db.engine import session_scope
    from jobscout.embed import run_embedding
    from tests.fixtures.corpus import load

    async with session_scope() as s:
        await load(s)
    await run_embedding()
    yield
