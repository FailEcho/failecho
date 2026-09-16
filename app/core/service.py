"""Transport-agnostic application services.

Every transport -- REST today, MCP now, anything later -- calls these
functions. They take an ``AsyncSession`` and plain Pydantic models, know
nothing about HTTP, and return the same response models the REST layer
publishes. That is what keeps "the MCP tool behaves exactly like /v1/query"
true by construction instead of by discipline.
"""

from __future__ import annotations

import hmac

from sqlalchemy import func, select, union
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
from app.core.aliases import canonical_service
from app.core.fingerprint import compute_fingerprint
from app.core.intelligence import (
    bump_counter,
    classify_status,
    fingerprint_stats,
    is_cross_reporter_evidence,
    lifetime_counts,
    recommend,
    recovery_actions,
    scope_counts,
    success_is_unverified,
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
    SuccessEvidence,
)


#: Authentication schemes that are plainly somebody else's credential. Seeing
#: one means the caller is authenticating to something in front of us, not
#: claiming to be us, and comparing it would push a genuine reporter out of the
#: adoption count for no reason.
_NOT_OUR_SCHEMES = frozenset({
    "basic", "digest", "negotiate", "ntlm", "hoba", "mutual", "aws4-hmac-sha256",
})


def operator_token_from(
    header_value: str | None, authorization: str | None = None
) -> str | None:
    """The operator token, from either header the client was able to send.

    ``X-FailEcho-Operator`` wins when both are present. ``Authorization`` is
    accepted as ``Bearer <token>`` because hosts that filter header names still
    let that one through; anything that is not a bearer scheme is ignored
    rather than guessed at, so an unrelated credential is never compared.

    Present-but-empty is a *failed* claim, not an absent one. An empty header
    returns "" so it is still checked and still fails to demo, because the
    alternative is that a malformed claim quietly counts as ordinary agent
    telemetry -- which is the adoption number.

    A bare value with no scheme is treated as the token. Header fields in
    client UIs are labelled "value", so a token gets pasted on its own, and
    requiring the word Bearer means the claim is silently ignored and the
    caller lands in adoption -- which is the failure this exists to prevent.
    The cost of being permissive is the opposite mistake: an unrelated opaque
    credential becomes a failed claim and that reporter is excluded from
    adoption. Under-counting our own reach is the safer error of the two.
    Recognised non-bearer schemes are still ignored, since Basic and Digest
    are clearly somebody else's credential and not a claim to be us.
    """
    if header_value is not None:
        return header_value
    if authorization is None:
        return None
    candidate = authorization.strip()
    scheme, space, value = candidate.partition(" ")
    lowered = scheme.lower()
    if lowered == "bearer":
        # Including "Bearer" with nothing after it: an empty claim, which is
        # a failed claim rather than a token that happens to spell Bearer.
        return value.strip()
    if lowered in _NOT_OUR_SCHEMES:
        return None
    if not space:
        return candidate  # a bare token, pasted into a field labelled "value"
    return None


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
            service=canonical_service(payload.service),
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
    # One name for one service, decided once at the edge, so the fingerprint,
    # the stored row and every count below all agree. Reading by the raw name
    # after writing the canonical one is a silent miss.
    service = canonical_service(payload.service)
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
            service=service,
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
            service=service,
            operation=payload.operation,
            version=payload.version,
            schema_hash=payload.schema_hash,
            outcome=payload.outcome,
            fingerprint=fingerprint,
            error_type=payload.error_type,
            error_code=payload.error_code,
            normalized_error=normalized,
            latency_ms=payload.latency_ms,
            mutates=payload.mutates,
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
                        Observation.service == service,
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
    # One round trip: UNION de-duplicates across the four tables. This sits on
    # the path an agent calls mid-failure, where four separate lookups cost a
    # measurable slice of throughput.
    tables = (Observation, HourlyStat, RecoveryOutcome, HourlyRecoveryStat)
    result = await session.execute(
        union(*(select(t.source).where(t.fingerprint == fingerprint) for t in tables))
    )
    return sorted(result.scalars())


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
    service = canonical_service(payload.service)
    fingerprint = compute_fingerprint(
        service=service,
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
        service=service,
        operation=payload.operation,
        version=payload.version,
        schema_hash=payload.schema_hash,
    )
    status_value = classify_status(short, long)

    actions = await recovery_actions(session, fingerprint) if known else []
    chosen = recommend(actions)

    # Can the successes for this service+operation be believed from outside?
    # Computed on the whole history, not a window: "never failed once" is the
    # pattern, and it only means something over many calls.
    lifetime = await lifetime_counts(session, service, payload.operation)
    is_write, write_source = lifetime.write_verdict(payload.operation)
    success_evidence = (
        SuccessEvidence(
            successes_total=lifetime.successes,
            failures_total=lifetime.failures,
            write_like=is_write,
            write_source=write_source,
            verified=not success_is_unverified(is_write, lifetime),
        )
        if lifetime.total > 0
        else None
    )

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
                recent_attempts=a.recent_attempts,
                recent_success_rate=round_or_none(a.recent_success_rate),
                decaying=a.decaying,
            )
            for a in actions
        ],
        success_evidence=success_evidence,
        recommendation=(
            Recommendation(
                action=chosen[0].action,
                confidence=round(chosen[1], 4),
                based_on_attempts=chosen[0].attempts,
                based_on_successes=chosen[0].successes,
                effective_attempts=chosen[0].capped_attempts,
                unique_reporters=chosen[0].unique_reporters,
                from_other_agents=(
                    is_cross_reporter_evidence(chosen[0], reporter_hash)
                    if reporter_hash
                    else None
                ),
                decaying=chosen[0].decaying,
                warning=(
                    f"{chosen[0].action} succeeded "
                    f"{round((chosen[0].prior_success_rate or 0) * 100)}% of the time before "
                    f"the last {settings.decay_window_seconds // 3600}h and "
                    f"{round((chosen[0].recent_success_rate or 0) * 100)}% inside it "
                    f"({chosen[0].recent_successes}/{chosen[0].recent_attempts}). "
                    "The root cause may have changed while the error shape stayed the same."
                    if chosen[0].decaying
                    else None
                ),
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
