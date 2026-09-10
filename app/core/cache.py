"""A tiny in-process TTL cache for the read-only dashboard endpoints.

Why this exists: `/v1/stats`, `/v1/services` and `/v1/recovery-intelligence`
return network-wide numbers that are identical for every caller. The homepage
polls all three every 30 seconds, so a hundred readers means a hundred
identical sets of SQL aggregates per cycle. Under a launch spike that was the
measured bottleneck -- at 200 concurrent visitors throughput fell from 179 to
77 req/s, purely on repeated identical work.

Ten seconds of staleness on a dashboard that refreshes every thirty is
invisible; recomputing it three hundred times is not.

Deliberately not cached: `/v1/query`, `/v1/observe`, `/v1/outcome` and
`/health`. Those answers belong to one caller and one failure.

This is a dict with timestamps, not a cache server. One process, bounded key
space (a handful of query-parameter combinations), no eviction policy needed.
"""

from __future__ import annotations

import time
from typing import Any


class TTLCache:
    """Single-process, time-boxed memoisation."""

    def __init__(self, ttl_seconds: float) -> None:
        self.ttl = ttl_seconds
        self._entries: dict[str, tuple[float, Any]] = {}
        self.hits = 0
        self.misses = 0

    @property
    def enabled(self) -> bool:
        return self.ttl > 0

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        stored_at, value = entry
        if time.monotonic() - stored_at >= self.ttl:
            del self._entries[key]
            self.misses += 1
            return None
        self.hits += 1
        return value

    def set(self, key: str, value: Any) -> Any:
        if self.enabled:
            self._entries[key] = (time.monotonic(), value)
        return value

    def clear(self) -> None:
        """Used by tests, and by anything that must observe a write instantly."""
        self._entries.clear()


#: Matches the Cache-Control max-age advertised for the same endpoints, so the
#: browser, any edge cache and the origin all agree on the staleness budget.
#: Configurable via FIN_DASHBOARD_CACHE_SECONDS; the test suite runs it at 0.
from app.core.config import settings  # noqa: E402  (avoids a circular import)

dashboard_cache = TTLCache(settings.dashboard_cache_seconds)
