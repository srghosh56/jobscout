"""Async engines: a read-write engine for the app and a read-only engine (dedicated
Postgres role) used *only* by the guarded text-to-SQL tool."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from jobscout.settings import get_settings


def sync_url(url: str) -> str:
    """postgresql+asyncpg://... -> postgresql+psycopg://... (for Alembic / scripts).
    asyncpg spells TLS as ?ssl=require, libpq/psycopg as ?sslmode=require (Neon needs TLS)."""
    u = make_url(url)
    q = dict(u.query)
    if "ssl" in q:
        q["sslmode"] = q.pop("ssl")
    return u.set(drivername="postgresql+psycopg", query=q).render_as_string(hide_password=False)


def async_url(url: str) -> str:
    u = make_url(url)
    if u.drivername in ("postgresql", "postgres", "postgresql+psycopg"):
        u = u.set(drivername="postgresql+asyncpg")
    q = dict(u.query)
    if "sslmode" in q:
        q["ssl"] = q.pop("sslmode")
    q.pop("channel_binding", None)  # libpq-only option Neon puts in its URLs
    return u.set(query=q).render_as_string(hide_password=False)


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    s = get_settings()
    return create_async_engine(async_url(s.database_url), pool_pre_ping=True, pool_size=10)


@lru_cache(maxsize=1)
def get_readonly_engine() -> AsyncEngine:
    s = get_settings()
    return create_async_engine(
        async_url(s.readonly_database_url),
        pool_pre_ping=True,
        pool_size=5,
        connect_args={
            "server_settings": {
                "statement_timeout": str(s.sql_statement_timeout_ms),
                "default_transaction_read_only": "on",
                "application_name": "jobscout-sql-tool",
            }
        },
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session


async def dispose_engines() -> None:
    for factory in (get_engine, get_readonly_engine):
        if factory.cache_info().currsize:
            await factory().dispose()
            factory.cache_clear()
    get_sessionmaker.cache_clear()
