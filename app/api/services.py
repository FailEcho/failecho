"""Public status endpoints: GET /v1/services and GET /v1/stats."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import case, func, select

from app.api.deps import SessionDep
from app.core.cache import dashboard_cache
from datetime import timedelta

from app.core.clock import ago, isoformat_z, utcnow
from app.core.config import (
    COUNTER_CROSS_AGENT_HELP,
    COUNTER_QUERY_KNOWN,
    COUNTER_QUERY_UNKNOWN,
    NON_REAL_SOURCES,
    OUTCOME_FAILURE,
    SOURCE_AGENT,
    SOURCE_DEMO_AGENT,
    SOURCE_SYNTHETIC,
    settings,
)
from app.core.intelligence import (
    WindowCounts,
    classify_status,
    recommend,
    recovery_actions,
)
from app.db.models import (
    DailyCounter,
    Fingerprint,
    HourlyRecoveryStat,
    HourlyStat,
    Observation,
    RecoveryOutcome,
)
from app.schemas.services import NetworkStats, RecoveryIntelligence, ServiceStatus

#: Rolling day used for the "real telemetry" counters on the homepage.
DAY_SECONDS = 24 * 3600

router = APIRouter(tags=["status"])


def _rollup(seconds: int):
    """Per service+operation totals/failures inside a window."""
    return (
        select(
            Observation.service,
            Observation.operation,
            func.count().label("total"),
            func.coalesce(
                func.sum(case((Observation.outcome == OUTCOME_FAILURE, 1), else_=0)), 0
            ).label("failures"),
            func.max(Observation.created_at).label("last_seen"),
            func.coalesce(
                func.sum(case((Observation.source == SOURCE_AGENT, 1), else_=0)), 0
            ).label("real_total"),
        )
        .where(Observation.created_at >= ago(seconds))
        .group_by(Observation.service, Observation.operation)
    )


@router.get(
    "/services",
    response_model=list[ServiceStatus],
    summary="Current status of every service/operation the network has seen",
    description=(
        "Rolled up across versions and schema hashes, over the last hour. "
        "Sorted worst-first so an incident is the first row you read."
    ),
)
async def services(
    session: SessionDep,
    service: str | None = Query(
        default=None, description="Optional exact-match filter on service name."
    ),
    limit: int = Query(
        default=settings.services_default_limit, ge=1, le=500, description="Max rows."
    ),
) -> list[ServiceStatus]:
    # Identical for every caller, so compute it at most once per TTL.
    cache_key = f"services:{service}:{limit}"
    cached = dashboard_cache.get(cache_key)
    if cached is not None:
        return cached

    long_rows = {
        (r.service, r.operation): r
        for r in (await session.execute(_rollup(settings.window_long_seconds))).all()
    }
    short_rows = {
        (r.service, r.operation): r
        for r in (await session.execute(_rollup(settings.window_short_seconds))).all()
    }

    results: list[ServiceStatus] = []
    for key, long_row in long_rows.items():
        if service is not None and key[0] != service:
            continue
        short_row = short_rows.get(key)
        short = WindowCounts(
            total=int(short_row.total) if short_row else 0,
            failures=int(short_row.failures) if short_row else 0,
        )
        long = WindowCounts(total=int(long_row.total), failures=int(long_row.failures))
        results.append(
            ServiceStatus(
                service=key[0],
                operation=key[1],
                status=classify_status(short, long),
                failure_rate_5m=_round(short.failure_rate),
                failure_rate_1h=_round(long.failure_rate),
                observations_5m=short.total,
                observations_1h=long.total,
                last_seen=isoformat_z(long_row.last_seen),
                # No real observations behind the row -> it is demo-only.
                demo_data=int(long_row.real_total or 0) == 0,
            )
        )

    severity = {"MAJOR": 0, "DEGRADED": 1, "HEALTHY": 2, "INSUFFICIENT_DATA": 3}
    results.sort(
        key=lambda r: (
            severity[r.status],
            -(r.failure_rate_1h or 0.0),
            r.service,
            r.operation,
        )
    )
    return dashboard_cache.set(cache_key, results[:limit])


@router.get(
    "/stats",
    response_model=NetworkStats,
    summary="Network-wide counters",
    description=(
        "Powers the live homepage. Real and synthetic telemetry are reported "
        "separately and never summed into a single adoption number. Totals "
        "include observations that survive only as hourly aggregates after "
        "pruning."
    ),
)
async def stats(session: SessionDep) -> NetworkStats:
    cached = dashboard_cache.get("stats")
    if cached is not None:
        return cached

    raw_total = (
        await session.execute(select(func.count()).select_from(Observation))
    ).scalar_one()

    # Pruned rows survive as hourly aggregates; raw and aggregate never
    # overlap, so adding them is a total rather than a double count.
    archived_row = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(HourlyStat.success_count + HourlyStat.failure_count), 0
                ).label("total"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                HourlyStat.source == SOURCE_SYNTHETIC,
                                HourlyStat.success_count + HourlyStat.failure_count,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("synthetic"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                HourlyStat.source == SOURCE_DEMO_AGENT,
                                HourlyStat.success_count + HourlyStat.failure_count,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("demo_agent"),
            )
        )
    ).one()
    archived_total = int(archived_row.total or 0)
    archived_synthetic = int(archived_row.synthetic or 0)
    archived_demo_agent = int(archived_row.demo_agent or 0)

    long_row = (
        await session.execute(
            select(
                func.count().label("total"),
                func.coalesce(
                    func.sum(case((Observation.outcome == OUTCOME_FAILURE, 1), else_=0)),
                    0,
                ).label("failures"),
                func.count(func.distinct(Observation.service)).label("services"),
            ).where(Observation.created_at >= ago(settings.window_long_seconds))
        )
    ).one()

    active_failures = (
        await session.execute(
            select(func.count(func.distinct(Observation.fingerprint))).where(
                Observation.fingerprint.is_not(None),
                Observation.created_at >= ago(settings.window_short_seconds),
            )
        )
    ).scalar_one()

    real_active_failures = (
        await session.execute(
            select(func.count(func.distinct(Observation.fingerprint))).where(
                Observation.fingerprint.is_not(None),
                Observation.source == SOURCE_AGENT,
                Observation.created_at >= ago(settings.window_short_seconds),
            )
        )
    ).scalar_one()

    fingerprints_total = (
        await session.execute(select(func.count()).select_from(Fingerprint))
    ).scalar_one()

    raw_recoveries = (
        await session.execute(select(func.count()).select_from(RecoveryOutcome))
    ).scalar_one()
    archived_recoveries = (
        await session.execute(
            select(func.coalesce(func.sum(HourlyRecoveryStat.attempts), 0))
        )
    ).scalar_one()

    raw_synthetic = (
        await session.execute(
            select(func.count()).where(Observation.source == SOURCE_SYNTHETIC)
        )
    ).scalar_one()
    demo_agent_rows = (
        await session.execute(
            select(func.count()).where(Observation.source == SOURCE_DEMO_AGENT)
        )
    ).scalar_one()

    # ---- real telemetry, kept strictly separate from demo data -----------
    real_total = (
        await session.execute(
            select(func.count()).where(Observation.source == SOURCE_AGENT)
        )
    ).scalar_one()
    archived_real = archived_total - archived_synthetic - archived_demo_agent

    real_day = (
        await session.execute(
            select(
                func.count().label("total"),
                func.count(func.distinct(Observation.reporter_hash)).label("reporters"),
                func.count(func.distinct(Observation.fingerprint)).label("fingerprints"),
                func.coalesce(
                    func.sum(case((Observation.outcome == OUTCOME_FAILURE, 1), else_=0)),
                    0,
                ).label("failures"),
            ).where(
                Observation.source == SOURCE_AGENT,
                Observation.created_at >= ago(DAY_SECONDS),
            )
        )
    ).one()

    real_recoveries_day = (
        await session.execute(
            select(func.count()).where(
                RecoveryOutcome.source == SOURCE_AGENT,
                RecoveryOutcome.created_at >= ago(DAY_SECONDS),
            )
        )
    ).scalar_one()

    # Experiment counters live in daily buckets; sum the last two UTC days and
    # keep it simple rather than pretending to a rolling window we do not have.
    days = [
        (utcnow() - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in (0, 1)
    ]
    counter_rows = (
        await session.execute(
            select(DailyCounter.name, func.sum(DailyCounter.value))
            .where(DailyCounter.day.in_(days))
            .group_by(DailyCounter.name)
        )
    ).all()
    counters = {name: int(value or 0) for name, value in counter_rows}

    known_hits = counters.get(COUNTER_QUERY_KNOWN, 0)
    unknown_hits = counters.get(COUNTER_QUERY_UNKNOWN, 0)
    total_queries = known_hits + unknown_hits
    real_failures = int(real_day.failures or 0)
    real_total_day = int(real_day.total or 0)

    synthetic_total = int(raw_synthetic or 0) + archived_synthetic
    demo_agent_total = int(demo_agent_rows or 0) + archived_demo_agent

    return dashboard_cache.set("stats", NetworkStats(
        observations_total=int(raw_total or 0) + archived_total,
        observations_1h=int(long_row.total or 0),
        failures_1h=int(long_row.failures or 0),
        active_failures=int(active_failures or 0),
        real_active_failures=int(real_active_failures or 0),
        fingerprints_total=int(fingerprints_total or 0),
        recovery_outcomes_total=int(raw_recoveries or 0) + int(archived_recoveries or 0),
        services_tracked=int(long_row.services or 0),
        demo_data=bool(synthetic_total or demo_agent_total),
        demo_mode=settings.demo_mode,
        synthetic_observations=synthetic_total,
        demo_agent_observations=demo_agent_total,
        real_observations_total=int(real_total or 0) + archived_real,
        real_observations_24h=real_total_day,
        real_failures_24h=real_failures,
        real_successes_24h=real_total_day - real_failures,
        known_query_hits_24h=known_hits,
        unknown_query_hits_24h=unknown_hits,
        known_hit_rate_24h=(
            round(known_hits / total_queries, 4) if total_queries else None
        ),
        recovery_outcome_ratio_24h=(
            round(int(real_recoveries_day or 0) / real_failures, 4)
            if real_failures
            else None
        ),
        cross_agent_help_24h=counters.get(COUNTER_CROSS_AGENT_HELP, 0),
        real_reporters_24h=int(real_day.reporters or 0),
        real_failure_fingerprints=int(real_day.fingerprints or 0),
        archived_observations=archived_total,
        generated_at=isoformat_z(utcnow()) or "",
    ))


@router.get(
    "/recovery-intelligence",
    response_model=list[RecoveryIntelligence],
    summary="Best evidenced recovery actions right now",
    description=(
        "The failures the network currently has the strongest recovery "
        "evidence for, best confidence first. Entries backed by synthetic demo "
        "rows are flagged with `demo_data: true`. Only actions that clear the "
        "evidence threshold appear here -- an empty list means the network has "
        "nothing worth acting on yet."
    ),
)
async def recovery_intelligence(
    session: SessionDep,
    limit: int = Query(default=5, ge=1, le=25, description="Max entries."),
    include_demo: bool = Query(
        default=True, description="Include entries backed by synthetic demo data."
    ),
) -> list[RecoveryIntelligence]:
    cache_key = f"recovery:{limit}:{include_demo}"
    cached = dashboard_cache.get(cache_key)
    if cached is not None:
        return cached

    # Candidate fingerprints: anything with recovery evidence, live or archived.
    live = (
        await session.execute(
            select(
                RecoveryOutcome.fingerprint, func.count().label("attempts")
            ).group_by(RecoveryOutcome.fingerprint)
        )
    ).all()
    archived = (
        await session.execute(
            select(
                HourlyRecoveryStat.fingerprint,
                func.coalesce(func.sum(HourlyRecoveryStat.attempts), 0).label("attempts"),
            ).group_by(HourlyRecoveryStat.fingerprint)
        )
    ).all()

    volume: dict[str, int] = {}
    for row in list(live) + list(archived):
        volume[row.fingerprint] = volume.get(row.fingerprint, 0) + int(row.attempts or 0)

    results: list[RecoveryIntelligence] = []
    # Look at the busiest candidates only: this endpoint feeds a homepage
    # panel, not an analytics warehouse.
    for fingerprint in sorted(volume, key=volume.get, reverse=True)[: limit * 4]:
        catalogue = await session.get(Fingerprint, fingerprint)
        if catalogue is None:
            continue
        actions = await recovery_actions(session, fingerprint)
        chosen = recommend(actions)
        if chosen is None:
            continue
        action, confidence = chosen

        demo = (
            await session.execute(
                select(func.count())
                .where(
                    Observation.fingerprint == fingerprint,
                    Observation.source.in_(NON_REAL_SOURCES),
                )
                .limit(1)
            )
        ).scalar_one()
        if not demo:
            demo = (
                await session.execute(
                    select(func.count())
                    .where(
                        HourlyStat.fingerprint == fingerprint,
                        HourlyStat.source.in_(NON_REAL_SOURCES),
                    )
                    .limit(1)
                )
            ).scalar_one()
        is_demo = bool(demo)
        if is_demo and not include_demo:
            continue

        results.append(
            RecoveryIntelligence(
                service=catalogue.service,
                operation=catalogue.operation,
                error_type=catalogue.error_type,
                error_code=catalogue.error_code,
                normalized_error=catalogue.normalized_error,
                fingerprint=fingerprint,
                action=action.action,
                attempts=action.attempts,
                successes=action.successes,
                success_rate=round(action.success_rate, 4),
                confidence=round(confidence, 4),
                unique_reporters=action.unique_reporters,
                observations=catalogue.observation_count,
                last_seen=isoformat_z(catalogue.last_seen),
                demo_data=is_demo,
            )
        )

    results.sort(key=lambda r: (-r.confidence, -r.attempts))
    return dashboard_cache.set(cache_key, results[:limit])


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)
