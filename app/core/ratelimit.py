"""In-process rate limiting for write endpoints.

Scope and honesty
-----------------
This is a fixed-window counter held in a dict inside one uvicorn process. It
is **not distributed**: run two workers and each gets its own budget. That is
an acceptable trade for an MVP whose whole point is to run on one small VPS
with no Redis, and the limits are deliberately generous.

What it protects: a single client flooding `/v1/observe` or `/v1/outcome` and
skewing the network's statistics. What it does not protect against: a
distributed flood from many IPs. The per-reporter evidence cap in
:mod:`app.core.intelligence` is the second, more important line of defence --
it limits *influence* rather than *requests*, so it survives an attacker who
simply changes IP.

Reads are not limited. Querying is the product; making it expensive to ask
would defeat the network.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.core.config import settings


@dataclass
class _Bucket:
    window_start: float
    count: int


class FixedWindowLimiter:
    """One counter per client key, reset every ``window_seconds``.

    Memory is bounded: expired buckets are swept on write, and if a spray of
    unique keys still pushes the map past ``max_clients`` it is cleared. A
    cleared map means everyone gets a fresh budget, which fails *open* -- the
    network would rather accept a burst of telemetry than reject honest agents.
    """

    def __init__(self, limit: int, window_seconds: float, max_clients: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_clients = max_clients
        self._buckets: dict[str, _Bucket] = {}

    def check(self, key: str) -> tuple[bool, int]:
        """Count one request. Returns ``(allowed, retry_after_seconds)``."""
        now = time.monotonic()
        bucket = self._buckets.get(key)

        if bucket is None or now - bucket.window_start >= self.window_seconds:
            if len(self._buckets) >= self.max_clients:
                self._sweep(now)
            self._buckets[key] = _Bucket(window_start=now, count=1)
            return True, 0

        if bucket.count >= self.limit:
            retry_after = int(self.window_seconds - (now - bucket.window_start)) + 1
            return False, max(1, retry_after)

        bucket.count += 1
        return True, 0

    def remaining(self, key: str) -> int:
        bucket = self._buckets.get(key)
        if bucket is None or time.monotonic() - bucket.window_start >= self.window_seconds:
            return self.limit
        return max(0, self.limit - bucket.count)

    def _sweep(self, now: float) -> None:
        expired = [
            key
            for key, bucket in self._buckets.items()
            if now - bucket.window_start >= self.window_seconds
        ]
        for key in expired:
            del self._buckets[key]
        if len(self._buckets) >= self.max_clients:
            self._buckets.clear()

    def reset(self) -> None:
        """Drop all state. Used by tests and by ``--reload`` restarts."""
        self._buckets.clear()


#: Shared limiter for every write path (REST and MCP alike), so an agent
#: cannot double its budget by switching transport.
write_limiter = FixedWindowLimiter(
    limit=settings.rate_limit_writes_per_minute,
    window_seconds=60.0,
    max_clients=settings.rate_limit_max_tracked_clients,
)

#: Header order used when the deployment sits behind a trusted proxy.
_FORWARDED_HEADERS = ("cf-connecting-ip", "x-real-ip", "x-forwarded-for")


def client_key(peer_ip: str | None, headers: dict | None = None, prefix: str = "") -> str:
    """Identify the caller for rate-limiting purposes.

    Behind Cloudflare or nginx the peer address is the proxy, so a forwarded
    header is used instead -- but only when ``FIN_TRUST_PROXY=1``. Trusting
    those headers on a directly exposed server would let any client forge its
    own identity and bypass the limit entirely.
    """
    ip = peer_ip or "unknown"
    if settings.trust_proxy_headers and headers:
        lowered = {str(k).lower(): v for k, v in headers.items()}
        for header in _FORWARDED_HEADERS:
            value = lowered.get(header)
            if value:
                ip = str(value).split(",")[0].strip()
                break
    return f"{prefix}{ip}"


def check_write_limit(key: str) -> tuple[bool, int]:
    """Returns ``(allowed, retry_after)``; always allows when disabled."""
    if not settings.rate_limit_enabled:
        return True, 0
    return write_limiter.check(key)
