"""Per-key token-bucket rate limiting (in-process).

Each key gets a bucket of ``capacity`` tokens refilled at ``refill_per_s``. A request
costs one token (chat costs more). Empty bucket → HTTP 429 with Retry-After = seconds
until the next token. Multi-replica deployments need a shared store (e.g. Redis).
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    capacity: float
    refill_per_s: float
    tokens: float = -1.0
    updated: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        if self.tokens < 0:
            self.tokens = self.capacity

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self.updated)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_s)
        self.updated = now

    def take(self, cost: float = 1.0, now: float | None = None) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        now = time.monotonic() if now is None else now
        self._refill(now)
        if self.tokens >= cost:
            self.tokens -= cost
            return True, 0.0
        missing = cost - self.tokens
        return False, missing / self.refill_per_s if self.refill_per_s > 0 else math.inf


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic, max_keys: int = 50_000) -> None:
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()
        self._clock = clock
        self._max_keys = max_keys

    def check(
        self, key: str, capacity: float, refill_per_s: float, cost: float = 1.0
    ) -> tuple[bool, int]:
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self._max_keys:  # bound memory under abuse
                    self._buckets.clear()
                bucket = self._buckets[key] = TokenBucket(capacity, refill_per_s, updated=now)
            ok, retry = bucket.take(cost, now)
        return ok, (0 if ok else max(1, math.ceil(retry)))
