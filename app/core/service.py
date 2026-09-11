"""Transport-agnostic application services.

Every transport -- REST today, MCP now, anything later -- calls these
functions. They take an ``AsyncSession`` and plain Pydantic models, know
nothing about HTTP, and return the same response models the REST layer
publishes. That is what keeps "the MCP tool behaves exactly like /v1/query"
true by construction instead of by discipline.
"""

from __future__ import annotations

import hmac

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import ago, isoformat_z, utcnow
from app.core.config import (
    COUNTER_CROSS_AGENT_HELP,
    COUNTER_QUERY_KNOWN,
    COUNTER_QUERY_UNKNOWN,
    NON_REAL_SOURCES,
    OUTCOME_FAILURE,
    REPORTER_KIND_DEMO,
    SOURCE_AGENT,
    SOURCE_DEMO_AGENT,
    SOURCE_FIRST_PARTY,
    settings,
)
from app.core.fingerprint import compute_fingerprint
from app.core.intelligence import (
    bump_counter,
    classify_status,
    fingerprint_stats,
    is_cross_reporter_evidence,
    recommend,
    recovery_actions,
    scope_counts,
)
from app.core.normalize import normalize_error
from app.db.models import (
    Fingerprint,
    HourlyRecoveryStat,
    HourlyStat,
    Observation,
    RecoveryOutcome,
)
from app.schemas.observe import ObserveRequest, ObserveResponse
from app.schemas.outcome import OutcomeRequest, OutcomeResponse
from app.schemas.query import (
    FailureRates,
    ObservationCounts,
    QueryRequest,
    QueryResponse,
    Recommendation,
    RecoveryActionStats,
)


def source_from_kind(
    reporter_kind: str | None, operator_token: str | None = None
) -> str:
    """Map the optional self-label headers to a stored source label.

    ``X-Reporter-Kind: demo`` only ever *downgrades* a report -- a caller can
    exclude itself from adoption metrics, never promote itself into them -- so
    it is trusted as sent.

    ``X-FailEcho-Operator`` is different: it claims the report came from
    FailEcho's own agents, and answers repeat that claim to other agents. So
    it is honoured only with the configured secret. A claim that fails the
    check is stored as demo: kept out of adoption exactly like the real
    thing, but never presented to anyone as operator evidence.
    """
    if operator_token is not None:
        expected = settings.first_party_token
        if expected and hmac.compare_digest(operator_token.encode(), expected.encode()):
            return SOURCE_FIRST_PARTY
        return SOURCE_DEMO_AGENT
    if reporter_kind and reporter_kind.strip().lower() == REPORTER_KIND_DEMO:
        return SOURCE_DEMO_AGENT
    return SOURCE_AGENT


async def _touch_fingerprint(
    session: AsyncSession,
    fingerprint: str,
    payload: ObserveRequest,
    normalized: str | None,
) -> bool:
    """Upsert the fingerprint catalogue row. Returns True if it already existed."""
    existing = await session.get(Fingerprint, fingerprint)
    now = utcnow()
    if existing is not None:
        existing.last_seen = now
        existing.observation_count += 1
        return True

    session.add(
        Fingerprint(
            fingerprint=fingerprint,
            service=payload.service,
            operation=payload.operation,
            version=payload.version,
            schema_hash=payload.schema_hash,
            error_type=payload.error_type,
            error_code=payload.error_code,
            normalized_error=normalized,
            first_seen=now,
            last_seen=now,
            observation_count=1,
        )
    )
    try:
        await session.flush()
    except IntegrityError:
        # Another request created it between our SELECT and INSERT.
        await session.rollback()
        row = await session.get(Fingerprint, fingerprint)
        if row is not None:
            row.last_seen = now
            row.observation_count += 1
        return True
    return False


async def record_observation(
    session: AsyncSession,
    payload: ObserveRequest,
    reporter_hash: str | None = None,
    source: str = SOURCE_AGENT,
) -> ObserveResponse:
    """Store one tool-call outcome. Backs POST /v1/observe and the MCP tools."""
    # PRIVACY: normalization happens here, at the edge. `payload.error_message`
    # (the raw string) is used for exactly one expression and then dropped -- it
    # is never assigned to a column and never logged.
    normalized = (
        normalize_error(payload.error_message)
        if payload.outcome == OUTCOME_FAILURE
        else None
    )

    fingerprint: str | None = None
    known = False
    if payload.outcome == OUTCOME_FAILURE:
        fingerprint = compute_fingerprint(
            service=payload.service,
            operation=payload.operation,
            version=payload.version,
            schema_hash=payload.schema_hash,
            error_type=payload.error_type,
            error_code=payload.error_code,
            normalized_error=normalized,
        )
        known = await _touch_fingerprint(session, fingerprint, payload, normalized)

    session.add(
        Observation(
            service=payload.service,
            operation=payload.operation,
            version=payload.version,
            schema_hash=payload.schema_hash,
            outcome=payload.outcome,
            fingerprint=fingerprint,
            error_type=payload.error_type,
            error_code=payload.error_code,
            normalized_error=normalized,
            latency_ms=payload.latency_ms,
            reporter_hash=reporter_hash,
            source=source,
        )
    )
    await session.commit()

    if fingerprint is not None:
        stats = await fingerprint_stats(session, fingerprint)
        count = stats.total
    else:
        count = int(
            (
                await session.execute(
                    select(func.count()).where(
                        Observation.service == payload.service,
                        Observation.operation == payload.operation,
                        Observation.version == payload.version,
                        Observation.schema_hash == payload.schema_hash,
                    )
                )
            ).scalar_one()
            or 0
        )

    return ObserveResponse(
        accepted=True,
        fingerprint=fingerprint,
        known=known,
        observations=count,
        normalized_error=normalized,
    )


async def record_recovery_outcome(
    session: AsyncSession,
    payload: OutcomeRequest,
    reporter_hash: str | None = None,
    source: str = SOURCE_AGENT,
) -> OutcomeResponse:
    """Store one recovery attempt. Backs POST /v1/outcome and the MCP tool."""
    session.add(
        RecoveryOutcome(
            fingerprint=payload.fingerprint,
            action=payload.action,
            successful=payload.successful,
            reporter_hash=reporter_hash,
            source=source,
        )
    )
    await session.commit()
    return OutcomeResponse(accepted=True)


async def evidence_sources(session: AsyncSession, fingerprint: str) -> list[str]:
    """Every provenance label behind a fingerprint, raw rows and aggregates.

    Tells a caller whether independent agents saw this failure, or only
    FailEcho's own agents, or only demo data -- before it acts on the answer.
    """
    found: set[str] = set()
    for table in (Observation, HourlyStat, RecoveryOutcome, HourlyRecoveryStat):
        result = await session.execute(
            select(table.source).where(table.fingerprint == fingerprint).distinct()
        )
        found.update(result.scalars())
    return sorted(found)


async def _includes_demo_data(session: AsyncSession, fingerprint: str) -> bool:
    """True when demo rows (seeded or demo-agent) back this fingerprint.

    Callers -- especially autonomous ones -- deserve to know when the numbers
    they are about to act on came from `scripts/seed_demo.py`.
    """
    raw = (
        await session.execute(
            select(func.count())
            .where(
                Observation.fingerprint == fingerprint,
                Observation.source.in_(NON_REAL_SOURCES),
            )
            .limit(1)
        )
    ).scalar_one()
    if raw:
        return True
    archived = (
        await session.execute(
            select(func.count())
            .where(
                HourlyStat.fingerprint == fingerprint,
                HourlyStat.source.in_(NON_REAL_SOURCES),
            )
            .limit(1)
        )
    ).scalar_one()
    return bool(archived)


async def query_intelligence(
    session: AsyncSession,
    payload: QueryRequest,
    reporter_hash: str | None = None,
    source: str = SOURCE_AGENT,
) -> QueryResponse:
    """Answer "what is happening with this failure right now".

    Nothing from the request is persisted: no observation, no fingerprint row,
    no reporter row. The only side effect is three integer counters -- did this
    query find evidence, and did that evidence come from someone other than the
    caller -- which is how we measure whether the network works at all. Demo
    callers are not counted.

    ``reporter_hash`` is optional and is used only for that comparison; it is
    never written anywhere. Backs POST /v1/query and the MCP
    ``check_tool_failure`` tool.
    """
    normalized = normalize_error(payload.error_message)
    fingerprint = compute_fingerprint(
        service=payload.service,
        operation=payload.operation,
        version=payload.version,
        schema_hash=payload.schema_hash,
        error_type=payload.error_type,
        error_code=payload.error_code,
        normalized_error=normalized,
    )

    catalogue = await session.get(Fingerprint, fingerprint)
    stats = await fingerprint_stats(session, fingerprint)
    known = (catalogue is not None and stats.total > 0) or stats.total > 0

    short, long = await scope_counts(
        session,
        service=payload.service,
        operation=payload.operation,
        version=payload.version,
        schema_hash=payload.schema_hash,
    )
    status_value = classify_status(short, long)

    actions = await recovery_actions(session, fingerprint) if known else []
    chosen = recommend(actions)

    looks_new = bool(
        catalogue is not None
        and catalogue.first_seen >= ago(settings.window_short_seconds)
    )

    response = QueryResponse(
        known=known,
        fingerprint=fingerprint,
        status=status_value,
        first_seen=isoformat_z(catalogue.first_seen) if catalogue else None,
        last_seen=isoformat_z(catalogue.last_seen) if catalogue else None,
        looks_new=looks_new,
        observations=ObservationCounts(
            total=stats.total,
            last_5m=stats.last_short,
            last_1h=stats.last_long,
            unique_reporters=stats.unique_reporters,
        ),
        failure_rate=FailureRates(
            last_5m=round_or_none(short.failure_rate),
            last_1h=round_or_none(long.failure_rate),
        ),
        normalized_error=normalized,
        recovery_actions=[
            RecoveryActionStats(
                action=a.action,
                attempts=a.attempts,
                successes=a.successes,
                success_rate=round(a.success_rate, 4),
                effective_attempts=a.capped_attempts,
                effective_successes=a.capped_successes,
                unique_reporters=a.unique_reporters,
                confidence=round(min(a.evidence_score, settings.max_confidence), 4),
            )
            for a in actions
        ],
        recommendation=(
            Recommendation(
                action=chosen[0].action,
                confidence=round(chosen[1], 4),
                based_on_attempts=chosen[0].attempts,
                based_on_successes=chosen[0].successes,
                effective_attempts=chosen[0].capped_attempts,
                unique_reporters=chosen[0].unique_reporters,
            )
            if chosen
            else None
        ),
        demo_data_included=await _includes_demo_data(session, fingerprint)
        if known
        else False,
        evidence_sources=await evidence_sources(session, fingerprint) if known else [],
    )

    # Experiment counters. Real traffic only, integers only, no identities.
    #
    # Wrapped: measuring the network must never be able to break it. If a
    # counter write fails for any reason, the agent still gets its answer --
    # losing a metric is survivable, returning a 500 to an agent that is
    # already handling a failure is not.
    if source == SOURCE_AGENT:
        try:
            await bump_counter(
                session, COUNTER_QUERY_KNOWN if known else COUNTER_QUERY_UNKNOWN
            )
            if chosen and is_cross_reporter_evidence(chosen[0], reporter_hash):
                await bump_counter(session, COUNTER_CROSS_AGENT_HELP)
            await session.commit()
        except Exception:  # noqa: BLE001 - telemetry is never worth an outage
            await session.rollback()

    return response


def round_or_none(value: float | None) -> float | None:
    return None if value is None else round(value, 4)
