"""API keys: random, shown once, stored as SHA-256 hashes, compared in constant time.

Key format: ``js_<prefix>_<secret>``; ``prefix`` (8 chars) is stored in clear to find
the row, the hash covers the whole key. SHA-256 (not bcrypt) is appropriate because
keys are 190+ bits of randomness, not human passwords.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import select

from jobscout.db.engine import session_scope
from jobscout.db.models import ApiKey

DEMO_TTL = dt.timedelta(hours=24)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_key() -> tuple[str, str]:
    prefix = secrets.token_hex(4)
    return f"js_{prefix}_{secrets.token_urlsafe(24)}", prefix


def parse_prefix(key: str) -> str | None:
    parts = key.split("_")
    if len(parts) < 3 or parts[0] != "js" or len(parts[1]) != 8:
        return None
    return parts[1]


@dataclass(frozen=True)
class Principal:
    key_id: int
    name: str
    is_demo: bool


async def create_api_key(name: str, is_demo: bool = False) -> str:
    key, prefix = new_key()
    async with session_scope() as session:
        session.add(
            ApiKey(
                name=name[:128],
                prefix=prefix,
                key_hash=hash_key(key),
                is_demo=is_demo,
                expires_at=dt.datetime.now(dt.UTC) + DEMO_TTL if is_demo else None,
            )
        )
        await session.commit()
    return key


async def verify_key(key: str | None) -> Principal | None:
    if not key:
        return None
    prefix = parse_prefix(key)
    if prefix is None:
        hmac.compare_digest(hash_key(key), hash_key("js_xxxxxxxx_dummy"))  # even timing
        return None
    async with session_scope() as session:
        row = (
            await session.execute(select(ApiKey).where(ApiKey.prefix == prefix))
        ).scalar_one_or_none()
    expected = row.key_hash if row else hash_key("js_xxxxxxxx_dummy")
    if not hmac.compare_digest(hash_key(key), expected) or row is None:
        return None
    now = dt.datetime.now(dt.UTC)
    if row.revoked_at is not None or (row.expires_at is not None and row.expires_at < now):
        return None
    return Principal(key_id=row.id, name=row.name, is_demo=row.is_demo)


def key_from_request(request: Request) -> str | None:
    key = request.headers.get("x-api-key")
    if key:
        return key.strip()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None
