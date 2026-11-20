"""Dagster resources: the database and the pipeline settings.

Both read the existing pydantic settings (``jobscout.settings``), so nothing in the
orchestration package reads environment variables itself. Fields left as ``None`` fall
back to the settings, and can be overridden per run in the Dagster launchpad.
"""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal, TypeVar, cast, get_args

import dagster as dg
import httpx
from sqlalchemy import text
from sqlalchemy.engine import make_url

from jobscout.db.engine import dispose_engines, session_scope
from jobscout.ingest.http import PoliteClient
from jobscout.settings import Settings, get_settings

T = TypeVar("T")
IngestMode = Literal["live", "offline", "fixtures"]
INGEST_MODES: tuple[str, ...] = get_args(IngestMode)


def run_async[T](fn: Callable[[], Awaitable[T]]) -> T:
    """Run async pipeline code from a (synchronous) Dagster step.

    Each call gets a fresh event loop, so the cached async engine is disposed before the
    loop closes; otherwise the next step would reuse connections bound to a dead loop."""

    async def main() -> T:
        try:
            return await fn()
        finally:
            await dispose_engines()

    return asyncio.run(main())


class JobScoutDb(dg.ConfigurableResource):  # type: ignore[type-arg]
    """The Postgres database the pipeline reads and writes (``DATABASE_URL``)."""

    def url(self) -> str:
        """Database URL with the password hidden (safe for metadata and logs)."""
        return make_url(get_settings().database_url).render_as_string(hide_password=True)

    def run(self, fn: Callable[[], Awaitable[T]]) -> T:
        return run_async(fn)

    def fetch_all(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        async def query() -> list[dict[str, Any]]:
            async with session_scope() as session:
                rows = (await session.execute(text(sql), params or {})).all()
                return [dict(r._mapping) for r in rows]

        return run_async(query)

    def scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        rows = self.fetch_all(sql, params)
        return next(iter(rows[0].values())) if rows else None


class JobScoutSettings(dg.ConfigurableResource):  # type: ignore[type-arg]
    """Pipeline knobs on top of the application settings."""

    ingest_mode: str | None = None  # live | offline | fixtures (Dagster config has no Literal)
    ingest_limit: int | None = None
    llm_extraction: bool | None = None
    llm_limit: int | None = None
    expiry_limit: int | None = None
    snapshot_dir: str | None = None

    @property
    def app(self) -> Settings:
        return get_settings()

    @property
    def mode(self) -> IngestMode:
        mode = self.ingest_mode or self.app.pipeline_ingest_mode
        if mode not in INGEST_MODES:
            raise ValueError(f"ingest_mode must be one of {INGEST_MODES}, not {mode!r}")
        return cast(IngestMode, mode)

    @property
    def limit(self) -> int | None:
        return (
            self.ingest_limit if self.ingest_limit is not None else self.app.pipeline_ingest_limit
        )

    @property
    def llm_enabled(self) -> bool:
        if self.llm_extraction is not None:
            return self.llm_extraction
        return self.app.pipeline_llm_extraction

    @property
    def llm_max(self) -> int | None:
        return self.llm_limit if self.llm_limit is not None else self.app.pipeline_llm_limit

    @property
    def expiry_max(self) -> int:
        return self.expiry_limit or self.app.pipeline_expiry_limit

    @property
    def snapshot_path(self) -> Path:
        return Path(self.snapshot_dir) if self.snapshot_dir else self.app.snapshot_dir

    def fixture_transport(self) -> httpx.MockTransport:
        from jobscout.orchestration.fixtures import fixture_transport

        return fixture_transport()

    def make_client(self) -> PoliteClient:
        """The polite, cached client from ingestion, or an in-process fixture server."""
        from jobscout.ingest.run import make_client

        if self.mode == "fixtures":
            return PoliteClient(
                user_agent=self.app.http_user_agent,
                min_interval_s=0.0,
                cache_dir=None,
                transport=self.fixture_transport(),
            )
        return make_client(offline=self.mode == "offline")
