"""Single source of truth for "now".

All timestamps are stored as *naive UTC* datetimes. Naive-UTC is the lowest
common denominator between SQLite (which has no native tz-aware type) and
PostgreSQL, which keeps the migration path boring.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utcnow() -> datetime:
    """Current UTC time as a naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ago(seconds: int) -> datetime:
    """Naive-UTC timestamp ``seconds`` in the past."""
    return utcnow() - timedelta(seconds=seconds)


def hour_bucket(value: datetime) -> datetime:
    """Truncate a timestamp to the start of its UTC hour.

    Hour buckets are the unit of long-term retention *and* the unit of the
    per-reporter evidence cap, which keeps both stories in one grain.
    """
    return value.replace(minute=0, second=0, microsecond=0)


def isoformat_z(value: datetime | None) -> str | None:
    """Render a stored naive-UTC datetime as an ISO-8601 ``...Z`` string."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat(timespec="seconds") + "Z"
