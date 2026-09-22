"""Retention: fold expired raw observations into hourly aggregates, then delete.

Why
---
Raw observations are the hot path -- the 5-minute and 1-hour windows read them
directly -- but they are also the thing that grows without bound. Keeping 48
hours of raw rows and hourly aggregates forever keeps the file small on a tiny
VPS while preserving long-term totals and long-term recovery evidence.

The invariant
-------------
A raw row is aggregated and deleted **inside one transaction**. Aggregates
therefore only ever describe rows that no longer exist, and "raw + aggregates"
is a total, never a double count. Re-running the pruner is a no-op because the
rows it already folded are gone -- that is what makes it idempotent.

Windows shorter than the retention period (5m, 1h) always read raw rows only,
so pruning can never change a live status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import case, delete, false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.adoption import established_reporters
from app.core.clock import utcnow
from app.core.config import (
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    SOURCE_AGENT,
    SOURCE_AGENT_SPARSE,
    settings,
)
from app.core.intelligence import _cap
from app.db.database import hour_bucket_expr
from app.db.models import (
    HourlyRecoveryStat,
    HourlyStat,
    Observation,
    PrivateObservation,
    PrivateRecoveryOutcome,
    RecoveryOutcome,
)


@dataclass
class PruneReport:
    """What one pruning run did. Printed verbatim by scripts/prune.py."""

    cutoff: datetime
    observations_aggregated: int = 0
    observation_buckets: int = 0
    recovery_outcomes_aggregated: int = 0
    recovery_buckets: int = 0
    observations_deleted: int = 0
    recovery_outcomes_deleted: int = 0
    private_observations_deleted: int = 0
    private_outcomes_deleted: int = 0
    dry_run: bool = False
    notes: list[str] = field(default_factory=list)


def _as_datetime(value) -> datetime:
    """Normalize a dialect-specific hour bucket into a naive UTC datetime."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")


async def _merge_hourly_stat(session: AsyncSession, key: dict, values: dict) -> None:
    """Add counts into an existing bucket, or create it.

    Buckets are matched on the full identity (including NULL version /
    schema_hash / fingerprint), so a second prune run that finds late-arriving
    rows tops up the same row instead of creating a duplicate.
    """
    existing = (
        await session.execute(
            select(HourlyStat).where(
                HourlyStat.bucket_start == key["bucket_start"],
                HourlyStat.service == key["service"],
                HourlyStat.operation == key["operation"],
                HourlyStat.version.is_(None)
                if key["version"] is None
                else HourlyStat.version == key["version"],
                HourlyStat.schema_hash.is_(None)
                if key["schema_hash"] is None
                else HourlyStat.schema_hash == key["schema_hash"],
                HourlyStat.fingerprint.is_(None)
                if key["fingerprint"] is None
                else HourlyStat.fingerprint == key["fingerprint"],
                HourlyStat.source == key["source"],
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        session.add(HourlyStat(**key, **values))
        return

    existing.success_count += values["success_count"]
    existing.failure_count += values["failure_count"]
    existing.latency_sum += values["latency_sum"]
    existing.latency_count += values["latency_count"]
    # Reporter identities are not retained, so distinct counts cannot be
    # re-derived across merges. max() keeps it an honest lower bound.
    existing.unique_reporters = max(
        existing.unique_reporters, values["unique_reporters"]
    )


async def _merge_hourly_recovery(
    session: AsyncSession, key: dict, values: dict
) -> None:
    existing = (
        await session.execute(
            select(HourlyRecoveryStat).where(
                HourlyRecoveryStat.bucket_start == key["bucket_start"],
                HourlyRecoveryStat.fingerprint == key["fingerprint"],
                HourlyRecoveryStat.action == key["action"],
                HourlyRecoveryStat.source == key["source"],
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        session.add(HourlyRecoveryStat(**key, **values))
        return

    existing.attempts += values["attempts"]
    existing.successes += values["successes"]
    existing.effective_attempts += values["effective_attempts"]
    existing.effective_successes += values["effective_successes"]
    existing.unique_reporters = max(
        existing.unique_reporters, values["unique_reporters"]
    )


async def aggregate_and_prune(
    session: AsyncSession,
    retention_hours: int | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> PruneReport:
    """Aggregate then delete everything older than the retention window."""
    hours = settings.retention_hours if retention_hours is None else retention_hours
    # Floored to the hour, so a bucket is either wholly archived or wholly
    # live and never both.
    #
    # It was not, and that let a reporter's evidence outrun its own cap. The
    # cap is five effective attempts per reporter, per fingerprint, per action,
    # per hour, and it is applied once to whatever is in the bucket. Cutting at
    # an arbitrary minute -- the prune timer fires at :15 -- split the boundary
    # hour into an archived part and a live part, each of which then got the
    # full cap of its own. Six attempts at 03:10 and six at 03:40 were capped
    # at five together while both were live, and at ten the moment the prune
    # ran. Confidence rising because retention ran is the one thing a number
    # described as "arithmetic you can recompute" cannot do.
    raw_cutoff = (now or utcnow()) - timedelta(hours=hours)
    cutoff = raw_cutoff.replace(minute=0, second=0, microsecond=0)
    report = PruneReport(cutoff=cutoff, dry_run=dry_run)

    # ---- observations ----------------------------------------------------
    # Archives keep a source label and lose reporter identity, so this is the
    # last moment an `agent` row's reporter can be judged against the adoption
    # threshold. Rows from established reporters archive as `agent`; the rest
    # -- anonymous, or too thin while the raw rows lived -- as `agent_sparse`.
    # Both stay evidence; only the label the front page counts changes.
    established = await established_reporters(session)
    archive_source = case(
        (Observation.source != SOURCE_AGENT, Observation.source),
        (Observation.reporter_hash.in_(sorted(established)) if established else false(), SOURCE_AGENT),
        else_=SOURCE_AGENT_SPARSE,
    )
    bucket = hour_bucket_expr(Observation.created_at)
    rows = (
        await session.execute(
            select(
                bucket.label("bucket"),
                Observation.service,
                Observation.operation,
                Observation.version,
                Observation.schema_hash,
                Observation.fingerprint,
                archive_source.label("source"),
                func.coalesce(
                    func.sum(case((Observation.outcome == OUTCOME_SUCCESS, 1), else_=0)),
                    0,
                ).label("successes"),
                func.coalesce(
                    func.sum(case((Observation.outcome == OUTCOME_FAILURE, 1), else_=0)),
                    0,
                ).label("failures"),
                func.count(func.distinct(Observation.reporter_hash)).label("reporters"),
                func.coalesce(func.sum(Observation.latency_ms), 0).label("latency_sum"),
                func.count(Observation.latency_ms).label("latency_count"),
            )
            .where(Observation.created_at < cutoff)
            .group_by(
                bucket,
                Observation.service,
                Observation.operation,
                Observation.version,
                Observation.schema_hash,
                Observation.fingerprint,
                archive_source,
            )
        )
    ).all()

    for row in rows:
        successes, failures = int(row.successes or 0), int(row.failures or 0)
        report.observations_aggregated += successes + failures
        report.observation_buckets += 1
        if dry_run:
            continue
        await _merge_hourly_stat(
            session,
            key={
                "bucket_start": _as_datetime(row.bucket),
                "service": row.service,
                "operation": row.operation,
                "version": row.version,
                "schema_hash": row.schema_hash,
                "fingerprint": row.fingerprint,
                "source": row.source,
            },
            values={
                "success_count": successes,
                "failure_count": failures,
                "unique_reporters": int(row.reporters or 0),
                "latency_sum": int(row.latency_sum or 0),
                "latency_count": int(row.latency_count or 0),
            },
        )

    # ---- recovery outcomes ----------------------------------------------
    # Grouped per reporter so the per-reporter hourly cap can be applied at the
    # same grain the live query applies it. Pruning therefore cannot change a
    # recommendation.
    recovery_bucket = hour_bucket_expr(RecoveryOutcome.created_at)
    recovery_rows = (
        await session.execute(
            select(
                recovery_bucket.label("bucket"),
                RecoveryOutcome.fingerprint,
                RecoveryOutcome.action,
                RecoveryOutcome.source,
                RecoveryOutcome.reporter_hash,
                func.count().label("attempts"),
                func.coalesce(
                    func.sum(case((RecoveryOutcome.successful.is_(True), 1), else_=0)),
                    0,
                ).label("successes"),
            )
            .where(RecoveryOutcome.created_at < cutoff)
            .group_by(
                recovery_bucket,
                RecoveryOutcome.fingerprint,
                RecoveryOutcome.action,
                RecoveryOutcome.source,
                RecoveryOutcome.reporter_hash,
            )
        )
    ).all()

    folded: dict[tuple, dict] = {}
    cap = settings.max_reporter_weight_per_hour
    for row in recovery_rows:
        key = (
            _as_datetime(row.bucket),
            row.fingerprint,
            row.action,
            row.source,
        )
        entry = folded.setdefault(
            key,
            {
                "attempts": 0,
                "successes": 0,
                "effective_attempts": 0,
                "effective_successes": 0,
                "reporters": set(),
            },
        )
        attempts, successes = int(row.attempts), int(row.successes or 0)
        eff_a, eff_s = _cap(attempts, successes, cap)
        entry["attempts"] += attempts
        entry["successes"] += successes
        entry["effective_attempts"] += eff_a
        entry["effective_successes"] += eff_s
        if row.reporter_hash is not None:
            entry["reporters"].add(row.reporter_hash)

    for (bucket_start, fingerprint, action, source), entry in folded.items():
        report.recovery_outcomes_aggregated += entry["attempts"]
        report.recovery_buckets += 1
        if dry_run:
            continue
        await _merge_hourly_recovery(
            session,
            key={
                "bucket_start": bucket_start,
                "fingerprint": fingerprint,
                "action": action,
                "source": source,
            },
            values={
                "attempts": entry["attempts"],
                "successes": entry["successes"],
                "effective_attempts": entry["effective_attempts"],
                "effective_successes": entry["effective_successes"],
                "unique_reporters": len(entry["reporters"]),
            },
        )

    if dry_run:
        report.notes.append("dry run: nothing was written or deleted")
        await session.rollback()
        return report

    # ---- delete the rows we just folded ---------------------------------
    deleted_observations = await session.execute(
        delete(Observation).where(Observation.created_at < cutoff)
    )
    deleted_recovery = await session.execute(
        delete(RecoveryOutcome).where(RecoveryOutcome.created_at < cutoff)
    )
    report.observations_deleted = deleted_observations.rowcount or 0
    report.recovery_outcomes_deleted = deleted_recovery.rowcount or 0

    # ---- private rows: deleted, never aggregated ------------------------
    # A team's evidence is not part of the public network, so there is nothing
    # to fold it into, and folding it anywhere shared is precisely what
    # private mode promises not to do. It lives longer than 48 hours because a
    # team's own history is the entire product; it does not live forever
    # because nobody asked us to hold it forever.
    private_cutoff = (now or utcnow()) - timedelta(days=settings.private_retention_days)
    deleted_private = await session.execute(
        delete(PrivateObservation).where(PrivateObservation.created_at < private_cutoff)
    )
    deleted_private_outcomes = await session.execute(
        delete(PrivateRecoveryOutcome).where(PrivateRecoveryOutcome.created_at < private_cutoff)
    )
    report.private_observations_deleted = deleted_private.rowcount or 0
    report.private_outcomes_deleted = deleted_private_outcomes.rowcount or 0

    # One commit: aggregates and deletions land together or not at all.
    await session.commit()
    return report
