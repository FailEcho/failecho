"""Generate synthetic demo telemetry so the network is legible immediately.

    python scripts/seed_demo.py            # add demo data
    python scripts/seed_demo.py --reset    # remove old demo data first
    python scripts/seed_demo.py --purge    # remove demo data and exit

Every row written here carries ``source='synthetic'``. The /v1/stats endpoint
counts those rows and the homepage shows a "Demo / synthetic data" banner while
any exist, so synthetic volume can never be mistaken for real adoption.

Four scenarios are produced:

    github-mcp / create_issue        MAJOR      schema drift, fixed by refresh
    search-api / search              DEGRADED   upstream timeouts, fallback works
    stripe-mcp / create_refund       HEALTHY    occasional rate limiting
    example-agent-tool / run         HEALTHY    rare crash, evidence too thin
                                                to recommend anything
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, insert, select  # noqa: E402

from app.core.clock import utcnow  # noqa: E402
from app.core.config import (  # noqa: E402
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    SOURCE_SYNTHETIC,
)
from app.core.fingerprint import compute_fingerprint  # noqa: E402
from app.core.normalize import normalize_error  # noqa: E402
from app.core.privacy import hash_reporter_id  # noqa: E402
from app.db.database import SessionLocal, init_db  # noqa: E402
from app.db.models import (  # noqa: E402
    Fingerprint,
    HourlyRecoveryStat,
    HourlyStat,
    Observation,
    RecoveryOutcome,
)

RNG = random.Random(1337)
NOW = utcnow()
MINUTES = 60  # how far back the demo history reaches


def reporters(prefix: str, count: int) -> list[str]:
    return [hash_reporter_id(f"demo-{prefix}-{i}") for i in range(count)]


def make_observation(
    *,
    minutes_ago: int,
    service: str,
    operation: str,
    version: str,
    schema_hash: str,
    outcome: str,
    reporter: str,
    error_type: str | None = None,
    error_code: str | None = None,
    raw_message: str | None = None,
    latency_ms: int | None = None,
) -> dict:
    normalized = normalize_error(raw_message) if outcome == OUTCOME_FAILURE else None
    fingerprint = (
        compute_fingerprint(
            service=service,
            operation=operation,
            version=version,
            schema_hash=schema_hash,
            error_type=error_type,
            error_code=error_code,
            normalized_error=normalized,
        )
        if outcome == OUTCOME_FAILURE
        else None
    )
    created = NOW - timedelta(minutes=minutes_ago, seconds=RNG.randint(0, 59))
    return {
        "created_at": created,
        "service": service,
        "operation": operation,
        "version": version,
        "schema_hash": schema_hash,
        "outcome": outcome,
        "fingerprint": fingerprint,
        "error_type": error_type,
        "error_code": error_code,
        "normalized_error": normalized,
        "latency_ms": latency_ms,
        "reporter_hash": reporter,
        "source": SOURCE_SYNTHETIC,
    }


def scenario(
    *,
    service: str,
    operation: str,
    version: str,
    schema_hash: str,
    error_type: str,
    error_code: str,
    message_factory,
    reporter_pool: list[str],
    per_minute: int,
    failure_rate,
    ok_latency: tuple[int, int],
    fail_latency: tuple[int, int],
) -> list[dict]:
    """Emit one minute-by-minute history.

    ``failure_rate`` is a callable ``minutes_ago -> rate`` so a scenario can
    ramp into an incident instead of being uniformly broken.
    """
    rows: list[dict] = []
    for minutes_ago in range(MINUTES, -1, -1):
        rate = failure_rate(minutes_ago)
        count = max(1, int(RNG.gauss(per_minute, per_minute * 0.25)))
        for _ in range(count):
            failed = RNG.random() < rate
            rows.append(
                make_observation(
                    minutes_ago=minutes_ago,
                    service=service,
                    operation=operation,
                    version=version,
                    schema_hash=schema_hash,
                    outcome=OUTCOME_FAILURE if failed else OUTCOME_SUCCESS,
                    reporter=RNG.choice(reporter_pool),
                    error_type=error_type if failed else None,
                    error_code=error_code if failed else None,
                    raw_message=message_factory() if failed else None,
                    latency_ms=RNG.randint(*(fail_latency if failed else ok_latency)),
                )
            )
    return rows


def recovery_rows(
    fingerprint: str, action: str, attempts: int, successes: int, pool: list[str]
) -> list[dict]:
    outcomes = [True] * successes + [False] * (attempts - successes)
    RNG.shuffle(outcomes)
    return [
        {
            "created_at": NOW - timedelta(minutes=RNG.randint(0, MINUTES)),
            "fingerprint": fingerprint,
            "action": action,
            "successful": ok,
            "reporter_hash": RNG.choice(pool),
            "source": SOURCE_SYNTHETIC,
        }
        for ok in outcomes
    ]


def build_dataset() -> tuple[list[dict], list[dict]]:
    observations: list[dict] = []
    recoveries: list[dict] = []

    # --- 1. github-mcp: healthy for most of the hour, then a schema-drift
    #        incident in the last ~12 minutes -> MAJOR on the 5m window.
    gh_pool = reporters("github", 47)
    gh = dict(
        service="github-mcp",
        operation="create_issue",
        version="2.8.1",
        schema_hash="a817ce",
        error_type="validation_error",
        error_code="422",
        message_factory=lambda: f"Repository {RNG.randint(100000, 999999)} was not found",
        reporter_pool=gh_pool,
        per_minute=14,
        failure_rate=lambda m: 0.02 if m > 12 else (0.35 if m > 6 else 0.78),
        ok_latency=(180, 520),
        fail_latency=(300, 700),
    )
    observations += scenario(**gh)
    gh_fingerprint = compute_fingerprint(
        service="github-mcp",
        operation="create_issue",
        version="2.8.1",
        schema_hash="a817ce",
        error_type="validation_error",
        error_code="422",
        normalized_error=normalize_error("Repository 918272 was not found"),
    )
    # Refreshing the stale tool schema almost always fixes it; blind retry does not.
    recoveries += recovery_rows(gh_fingerprint, "refresh_schema", 124, 117, gh_pool)
    recoveries += recovery_rows(gh_fingerprint, "retry", 91, 17, gh_pool)
    recoveries += recovery_rows(gh_fingerprint, "wait", 20, 8, gh_pool)

    # --- 2. search-api: steady upstream timeouts -> DEGRADED.
    search_pool = reporters("search", 23)
    observations += scenario(
        service="search-api",
        operation="search",
        version="3.0.2",
        schema_hash="c41b90",
        error_type="timeout",
        error_code="504",
        message_factory=lambda: (
            f"Upstream request timed out after {RNG.choice([30000, 45000])} ms"
        ),
        reporter_pool=search_pool,
        per_minute=9,
        failure_rate=lambda m: 0.21,
        ok_latency=(90, 400),
        fail_latency=(30000, 45000),
    )
    search_fingerprint = compute_fingerprint(
        service="search-api",
        operation="search",
        version="3.0.2",
        schema_hash="c41b90",
        error_type="timeout",
        error_code="504",
        normalized_error=normalize_error("Upstream request timed out after 30000 ms"),
    )
    recoveries += recovery_rows(search_fingerprint, "use_fallback", 40, 31, search_pool)
    recoveries += recovery_rows(search_fingerprint, "retry", 30, 12, search_pool)

    # --- 3. stripe-mcp: boring and healthy, with rare rate limiting.
    stripe_pool = reporters("stripe", 15)
    observations += scenario(
        service="stripe-mcp",
        operation="create_refund",
        version="1.4.0",
        schema_hash="7de2aa",
        error_type="rate_limit",
        error_code="429",
        message_factory=lambda: "Too many requests, retry in 3 seconds",
        reporter_pool=stripe_pool,
        per_minute=7,
        failure_rate=lambda m: 0.012,
        ok_latency=(120, 380),
        fail_latency=(60, 150),
    )

    # --- 4. example-agent-tool: healthy, one rare crash with only three
    #        recovery attempts -> deliberately below the evidence threshold.
    example_pool = reporters("example", 9)
    observations += scenario(
        service="example-agent-tool",
        operation="run",
        version="0.9.0",
        schema_hash="0b91fe",
        error_type="tool_error",
        error_code="500",
        message_factory=lambda: (
            f"Internal worker crashed (pid {RNG.randint(10000, 99999)})"
        ),
        reporter_pool=example_pool,
        per_minute=5,
        failure_rate=lambda m: 0.03,
        ok_latency=(40, 220),
        fail_latency=(500, 2500),
    )
    example_fingerprint = compute_fingerprint(
        service="example-agent-tool",
        operation="run",
        version="0.9.0",
        schema_hash="0b91fe",
        error_type="tool_error",
        error_code="500",
        normalized_error=normalize_error("Internal worker crashed (pid 48211)"),
    )
    recoveries += recovery_rows(example_fingerprint, "reconnect", 3, 2, example_pool)

    return observations, recoveries


async def purge(session) -> int:
    removed = 0
    # Aggregates too: a pruned demo run must not leave synthetic history behind.
    for model in (Observation, RecoveryOutcome, HourlyStat, HourlyRecoveryStat):
        result = await session.execute(
            delete(model).where(model.source == SOURCE_SYNTHETIC)
        )
        removed += result.rowcount or 0
    # Drop catalogue rows that no longer have observations behind them.
    orphans = (
        await session.execute(
            select(Fingerprint.fingerprint).where(
                Fingerprint.fingerprint.not_in(
                    select(Observation.fingerprint).where(
                        Observation.fingerprint.is_not(None)
                    )
                )
            )
        )
    ).scalars().all()
    if orphans:
        await session.execute(
            delete(Fingerprint).where(Fingerprint.fingerprint.in_(orphans))
        )
    await session.commit()
    return removed


async def rebuild_catalogue(session) -> int:
    """Recompute the fingerprints table from the observations table."""
    rows = (
        await session.execute(
            select(
                Observation.fingerprint,
                Observation.service,
                Observation.operation,
                Observation.version,
                Observation.schema_hash,
                Observation.error_type,
                Observation.error_code,
                Observation.normalized_error,
                func.min(Observation.created_at).label("first_seen"),
                func.max(Observation.created_at).label("last_seen"),
                func.count().label("observation_count"),
            )
            .where(Observation.fingerprint.is_not(None))
            .group_by(Observation.fingerprint)
        )
    ).all()

    for row in rows:
        existing = await session.get(Fingerprint, row.fingerprint)
        values = dict(
            service=row.service,
            operation=row.operation,
            version=row.version,
            schema_hash=row.schema_hash,
            error_type=row.error_type,
            error_code=row.error_code,
            normalized_error=row.normalized_error,
            first_seen=row.first_seen,
            last_seen=row.last_seen,
            observation_count=int(row.observation_count),
        )
        if existing is None:
            session.add(Fingerprint(fingerprint=row.fingerprint, **values))
        else:
            for key, value in values.items():
                setattr(existing, key, value)
    await session.commit()
    return len(rows)


async def main(reset: bool, purge_only: bool) -> None:
    await init_db()
    async with SessionLocal() as session:
        if reset or purge_only:
            removed = await purge(session)
            print(f"removed {removed} synthetic rows")
            if purge_only:
                await rebuild_catalogue(session)
                return

        observations, recoveries = build_dataset()
        await session.execute(insert(Observation), observations)
        await session.execute(insert(RecoveryOutcome), recoveries)
        await session.commit()

        catalogue = await rebuild_catalogue(session)

    print(f"inserted {len(observations)} observations (source=synthetic)")
    print(f"inserted {len(recoveries)} recovery outcomes (source=synthetic)")
    print(f"catalogued {catalogue} fingerprints")
    print()
    print("Start the server and open http://localhost:8000")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset", action="store_true", help="delete existing synthetic rows first"
    )
    parser.add_argument(
        "--purge", action="store_true", help="delete synthetic rows and exit"
    )
    args = parser.parse_args()
    asyncio.run(main(reset=args.reset, purge_only=args.purge))
