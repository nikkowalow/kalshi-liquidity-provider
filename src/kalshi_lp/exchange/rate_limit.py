"""Client-side token buckets mirroring Kalshi's read/write rate limits.

Kalshi meters requests in tokens: most calls cost 10, cancels cost 2, and
batch calls cost per item. Each account tier refills a read bucket and a write
bucket at a fixed tokens-per-second budget (Basic: 200 read / 100 write).
Throttling ourselves keeps us from burning retries on 429s.
"""

from __future__ import annotations

import asyncio
import time
from enum import StrEnum


class Bucket(StrEnum):
    READ = "read"
    WRITE = "write"


class TokenBucket:
    def __init__(self, rate_per_sec: float, capacity: float | None = None):
        if rate_per_sec <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate_per_sec
        self.capacity = capacity if capacity is not None else rate_per_sec
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    async def acquire(self, tokens: float) -> None:
        # A request larger than the bucket (e.g. a big batch) waits for a full
        # bucket and then drives the balance negative, which throttles followers.
        async with self._lock:
            self._refill()
            need = min(tokens, self.capacity)
            if self._tokens < need:
                await asyncio.sleep((need - self._tokens) / self.rate)
                self._refill()
            self._tokens -= tokens


class RateLimiter:
    def __init__(self, read_per_sec: float, write_per_sec: float):
        self._buckets = {
            Bucket.READ: TokenBucket(read_per_sec),
            Bucket.WRITE: TokenBucket(write_per_sec),
        }

    async def acquire(self, bucket: Bucket, tokens: float) -> None:
        await self._buckets[bucket].acquire(tokens)
