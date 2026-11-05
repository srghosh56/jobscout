"""A polite HTTP client for third-party APIs.

- identifies itself with a User-Agent,
- enforces a minimum interval between requests *per host* (rate limit),
- retries 429/5xx/timeouts with exponential backoff + jitter and honours Retry-After,
- caches successful GET responses on disk (also our "recorded fixtures" for offline
  replay). The cache lives in ``.cache/http`` and is git-ignored, because responses
  contain posting texts that must never be committed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from jobscout.logging import get_logger

log = get_logger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class OfflineCacheMiss(RuntimeError):
    """Raised in offline mode when a request isn't in the recorded cache."""


@dataclass
class PoliteClient:
    user_agent: str
    min_interval_s: float = 1.0
    cache_dir: Path | None = None
    cache_ttl_s: float = 6 * 24 * 3600
    max_retries: int = 5
    base_backoff_s: float = 1.0
    max_backoff_s: float = 60.0
    offline: bool = False
    timeout_s: float = 30.0
    transport: httpx.AsyncBaseTransport | None = None
    sleep: Any = asyncio.sleep
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)
    _last_request: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict, init=False, repr=False)
    stats: dict[str, int] = field(
        default_factory=lambda: {"requests": 0, "cache_hits": 0, "retries": 0, "errors": 0},
        init=False,
    )

    async def __aenter__(self) -> PoliteClient:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            timeout=self.timeout_s,
            follow_redirects=True,
            transport=self.transport,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()

    # -- cache ---------------------------------------------------------------
    def _cache_path(self, url: str, params: dict[str, Any] | None) -> Path | None:
        if self.cache_dir is None:
            return None
        full = url + ("?" + urlencode(sorted((params or {}).items())) if params else "")
        digest = hashlib.sha256(full.encode()).hexdigest()
        host = urlsplit(url).netloc.replace(":", "_")
        return self.cache_dir / host / f"{digest}.json"

    def _cache_get(self, path: Path | None) -> Any | None:
        if path is None or not path.exists():
            return None
        if not self.offline and time.time() - path.stat().st_mtime > self.cache_ttl_s:
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _cache_put(path: Path | None, data: Any) -> None:
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # -- rate limit ------------------------------------------------------------
    async def _wait_turn(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._last_request.get(host)
            now = time.monotonic()
            if last is not None and now - last < self.min_interval_s:
                await self.sleep(self.min_interval_s - (now - last))
            self._last_request[host] = time.monotonic()

    def backoff_delay(self, attempt: int, retry_after: str | None = None) -> float:
        """Exponential backoff with full jitter; Retry-After (seconds) wins if present."""
        if retry_after:
            try:
                return min(float(retry_after), self.max_backoff_s)
            except ValueError:
                pass
        cap = min(self.max_backoff_s, self.base_backoff_s * (2**attempt))
        return random.uniform(0, cap)  # noqa: S311 - jitter, not crypto

    async def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        use_cache: bool = True,
    ) -> Any:
        path = self._cache_path(url, params) if use_cache else None
        cached = self._cache_get(path)
        if cached is not None:
            self.stats["cache_hits"] += 1
            return cached
        if self.offline:
            raise OfflineCacheMiss(url)
        assert self._client is not None, "use 'async with PoliteClient(...)'"
        host = urlsplit(url).netloc
        for attempt in range(self.max_retries + 1):
            await self._wait_turn(host)
            self.stats["requests"] += 1
            try:
                resp = await self._client.get(url, params=params, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= self.max_retries:
                    self.stats["errors"] += 1
                    raise
                self.stats["retries"] += 1
                delay = self.backoff_delay(attempt)
                log.warning("http_retry", host=host, error=type(exc).__name__, delay=delay)
                await self.sleep(delay)
                continue
            if resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                self.stats["retries"] += 1
                delay = self.backoff_delay(attempt, resp.headers.get("Retry-After"))
                log.warning("http_retry", host=host, status=resp.status_code, delay=delay)
                await self.sleep(delay)
                continue
            if resp.status_code >= 400:
                self.stats["errors"] += 1
            resp.raise_for_status()
            data = resp.json()
            self._cache_put(path, data)
            return data
        raise RuntimeError("unreachable")  # pragma: no cover
