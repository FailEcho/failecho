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

This is a dict with timestamps, not a cache server. One process, one dict.

The key space is not as small as it looks, which is why there is a cap on it:
`/v1/services` takes a caller-supplied `service` filter, and every distinct
value became a key that nothing ever removed. A TTL is not a bound -- an
expired entry is only dropped when that exact key is read again -- so an
unauthenticated reader could grow the dict indefinitely on a box with a 400M
memory ceiling. Entries are capped, expired ones are swept before anything is
evicted, and the oldest goes first.
"""

from __future__ import annotations

import time
from typing import Any


class TTLCache:
    """Single-process, time-boxed memoisation."""

    #: Enough for every real combination of dashboard parameters several
    #: times over, small enough that the dict cannot become the memory
    #: problem the cache exists to avoid.
    MAX_ENTRIES = 512

    def __init__(self, ttl_seconds: float, max_entries: int | None = None) -> None:
        self.ttl = ttl_seconds
        self.max_entries = max_entries or self.MAX_ENTRIES
        self._entries: dict[str, tuple[float, Any]] = {}
        self.hits = 0
        self.misses = 0
        self.evictions = 0

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
        if not self.enabled:
            return value
        if key not in self._entries and len(self._entries) >= self.max_entries:
            self._evict()
        self._entries[key] = (time.monotonic(), value)
        return value

    def _evict(self) -> None:
        """Make room: expired entries first, then the oldest live one.

        Python dicts keep insertion order and `set` always writes a fresh key
        last, so the first key is the oldest.
        """
        now = time.monotonic()
        stale = [k for k, (at, _) in self._entries.items() if now - at >= self.ttl]
        for key in stale:
            del self._entries[key]
            self.evictions += 1
        while len(self._entries) >= self.max_entries:
            del self._entries[next(iter(self._entries))]
            self.evictions += 1

    def clear(self) -> None:
        """Used by tests, and by anything that must observe a write instantly."""
        self._entries.clear()


#: Matches the Cache-Control max-age advertised for the same endpoints, so the
#: browser, any edge cache and the origin all agree on the staleness budget.
#: Configurable via FIN_DASHBOARD_CACHE_SECONDS; the test suite runs it at 0.
from app.core.config import settings  # noqa: E402  (avoids a circular import)

dashboard_cache = TTLCache(settings.dashboard_cache_seconds)
