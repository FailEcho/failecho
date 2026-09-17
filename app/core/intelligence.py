"""Aggregation layer: counts -> status -> recommendation.

Everything in this module is deterministic arithmetic over SQL counts. There
is no model, no anomaly detector and no learned parameter. That is a feature:
an agent (or a human) can reproduce every number we return.

MVP DISCLAIMER
--------------
The incident heuristic below is intentionally a threshold on a failure rate
over two fixed windows. It is *not* seasonality-aware, *not* change-point
detection and *not* statistically calibrated. Thresholds live in
:mod:`app.core.config` so they are one edit away.
"""

from __future__ import annotations

import re

import math
from dataclasses import dataclass

from sqlalchemy import Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import ago, hour_bucket
from app.core.config import (
    OUTCOME_FAILURE,
    STATUS_DEGRADED,
    STATUS_HEALTHY,
    STATUS_INSUFFICIENT_DATA,
    STATUS_MAJOR,
    settings,
)
from app.db.database import hour_bucket_expr
from app.db.models import (
    Fingerprint,
    HourlyRecoveryStat,
    HourlyStat,
    Observation,
    RecoveryOutcome,
)

# ---------------------------------------------------------------------------
# Windowed counts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowCounts:
    """Observation counts inside one time window."""

    total: int = 0
    failures: int = 0

    @property
    def successes(self) -> int:
        return self.total - self.failures

    @property
    def failure_rate(self) -> float | None:
        """Failures / total, or ``None`` when the window is empty.

        ``None`` means "we do not know", which is never the same as 0.0.
        """
        if self.total <= 0:
            return None
        return self.failures / self.total


def _scope_filters(
    service: str,
    operation: str,
    version: str | None = None,
    schema_hash: str | None = None,
) -> list:
    """Build the WHERE clauses identifying one health scope.

    ``version`` / ``schema_hash`` are optional so the same helper serves the
    narrow scope used by /v1/query and the broad service+operation rollup
    shown by /v1/services.
    """
    filters = [Observation.service == service, Observation.operation == operation]
    if version is not None:
        filters.append(Observation.version == version)
    if schema_hash is not None:
        filters.append(Observation.schema_hash == schema_hash)
    return filters


def _counts_select(filters: list, seconds: int) -> Select:
    return select(
        func.count().label("total"),
        func.coalesce(
            func.sum(case((Observation.outcome == OUTCOME_FAILURE, 1), else_=0)), 0
        ).label("failures"),
    ).where(*filters, Observation.created_at >= ago(seconds))


#: Operation names that look like they change state. A heuristic, said to be
#: one in every place it is surfaced: an unknown API's naming is not ours.
WRITE_LIKE = re.compile(
    r"^(create|update|delete|remove|destroy|put|post|patch|write|insert|upsert|"
    r"send|publish|set|add|merge|push|commit|submit|cancel|approve|reject)"
    r"(_|[A-Z]|$)"
)


def write_like(operation: str) -> bool:
    return bool(WRITE_LIKE.match(operation))


@dataclass(frozen=True)
class LifetimeCounts:
    """Everything ever seen for one service+operation: live rows plus the
    hourly aggregates that outlive retention."""

    successes: int
    failures: int
    #: How many live rows declared the operation a write, and how many a read.
    #: Declarations are only kept on live rows; the aggregates do not carry
    #: them, and callers who declare tend to keep declaring.
    declared_write: int = 0
    declared_read: int = 0

    @property
    def total(self) -> int:
        return self.successes + self.failures

    def write_verdict(self, operation: str) -> tuple[bool, str]:
        """Is this a write, and how do we know.

        A reporter's declaration wins: one line of annotation from the caller
        beats anything inferred from a name, and GraphQL -- all POST -- cannot
        be inferred at all. Majority of declarations, in case reporters
        disagree. Only with none at all does the name heuristic speak.
        """
        if self.declared_write + self.declared_read > 0:
            return self.declared_write >= self.declared_read, "declared"
        return write_like(operation), "name"


async def lifetime_counts(
    session: AsyncSession, service: str, operation: str
) -> LifetimeCounts:
    live = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(case((Observation.outcome == OUTCOME_FAILURE, 1), else_=0)), 0
                ).label("failures"),
                func.count().label("total"),
                func.coalesce(
                    func.sum(case((Observation.mutates.is_(True), 1), else_=0)), 0
                ).label("declared_write"),
                func.coalesce(
                    func.sum(case((Observation.mutates.is_(False), 1), else_=0)), 0
                ).label("declared_read"),
            ).where(Observation.service == service, Observation.operation == operation)
        )
    ).one()
    archived = (
        await session.execute(
            select(
                func.coalesce(func.sum(HourlyStat.success_count), 0).label("successes"),
                func.coalesce(func.sum(HourlyStat.failure_count), 0).label("failures"),
            ).where(HourlyStat.service == service, HourlyStat.operation == operation)
        )
    ).one()
    live_failures = int(live.failures or 0)
    live_successes = int(live.total or 0) - live_failures
    return LifetimeCounts(
        successes=live_successes + int(archived.successes or 0),
        failures=live_failures + int(archived.failures or 0),
        declared_write=int(live.declared_write or 0),
        declared_read=int(live.declared_read or 0),
    )


def success_is_unverified(is_write: bool, counts: LifetimeCounts) -> bool:
    """Many successes, no failure ever, on a write.

    From outside, a backend that never performs a write and returns 200 is
    indistinguishable from one that is flawless. Both look like this. Reads are
    not flagged: a lookup that has never failed is merely a lookup that has
    never failed.
    """
    return (
        is_write
        and counts.failures == 0
        and counts.successes >= settings.unverified_success_min_calls
    )


async def window_counts(
    session: AsyncSession, filters: list, seconds: int
) -> WindowCounts:
    row = (await session.execute(_counts_select(filters, seconds))).one()
    return WindowCounts(total=int(row.total or 0), failures=int(row.failures or 0))


async def scope_counts(
    session: AsyncSession,
    *,
    service: str,
    operation: str,
    version: str | None = None,
    schema_hash: str | None = None,
) -> tuple[WindowCounts, WindowCounts]:
    """Return ``(short_window, long_window)`` counts for a health scope."""
    filters = _scope_filters(service, operation, version, schema_hash)
    short = await window_counts(session, filters, settings.window_short_seconds)
    long = await window_counts(session, filters, settings.window_long_seconds)
    return short, long


# ---------------------------------------------------------------------------
# Incident detection (MVP heuristic)
# ---------------------------------------------------------------------------


def classify_status(short: WindowCounts, long: WindowCounts) -> str:
    """Map windowed counts to HEALTHY / DEGRADED / MAJOR / INSUFFICIENT_DATA.

    Rules, in order:

    1. Fewer than ``min_observations_for_status`` observations in the long
       window -> INSUFFICIENT_DATA. We would rather say nothing than guess.
    2. Otherwise pick the *rate*: the short window wins once it carries at
       least ``min_observations_short_window`` observations, because a fresh
       incident should not be diluted by an hour of healthy history.
    3. Threshold that rate: <5% HEALTHY, <30% DEGRADED, else MAJOR.
    """
    if long.total < settings.min_observations_for_status:
        return STATUS_INSUFFICIENT_DATA

    if short.total >= settings.min_observations_short_window:
        rate = short.failure_rate
    else:
        rate = long.failure_rate

    if rate is None:
        return STATUS_INSUFFICIENT_DATA
    if rate < settings.healthy_max_failure_rate:
        return STATUS_HEALTHY
    if rate < settings.degraded_max_failure_rate:
        return STATUS_DEGRADED
    return STATUS_MAJOR


# ---------------------------------------------------------------------------
# Fingerprint-level statistics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FingerprintStats:
    total: int = 0
    last_short: int = 0
    last_long: int = 0
    unique_reporters: int = 0


async def fingerprint_stats(
    session: AsyncSession, fingerprint: str
) -> FingerprintStats:
    """How common is this exact failure, overall / recently / across reporters.

    ``unique_reporters`` counts distinct non-null reporter hashes in the long
    window. Anonymous observations are deliberately excluded so that a single
    chatty reporter cannot look like a crowd.
    """
    raw_total = (
        await session.execute(
            select(func.count()).where(Observation.fingerprint == fingerprint)
        )
    ).scalar_one()

    # Observations older than the retention window survive only as hourly
    # aggregates. Raw rows are deleted in the same transaction that creates
    # those aggregates, so adding the two can never double count.
    archived_total = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(HourlyStat.success_count + HourlyStat.failure_count), 0
                )
            ).where(HourlyStat.fingerprint == fingerprint)
        )
    ).scalar_one()

    short = (
        await session.execute(
            select(func.count()).where(
                Observation.fingerprint == fingerprint,
                Observation.created_at >= ago(settings.window_short_seconds),
            )
        )
    ).scalar_one()

    long = (
        await session.execute(
            select(func.count()).where(
                Observation.fingerprint == fingerprint,
                Observation.created_at >= ago(settings.window_long_seconds),
            )
        )
    ).scalar_one()

    reporters = (
        await session.execute(
            select(func.count(func.distinct(Observation.reporter_hash))).where(
                Observation.fingerprint == fingerprint,
                Observation.reporter_hash.is_not(None),
                Observation.created_at >= ago(settings.window_long_seconds),
            )
        )
    ).scalar_one()

    return FingerprintStats(
        total=int(raw_total or 0) + int(archived_total or 0),
        last_short=int(short or 0),
        last_long=int(long or 0),
        unique_reporters=int(reporters or 0),
    )


# ---------------------------------------------------------------------------
# Recovery evidence
# ---------------------------------------------------------------------------


def wilson_lower_bound(successes: int, attempts: int, z: float | None = None) -> float:
    """Lower bound of the Wilson score interval for a binomial proportion.

    Why this and not raw success rate: it folds sample size into the number.
    5/5 successes scores lower than 117/124 successes, which is exactly the
    ordering we want when recommending an action to an agent.

    Fully deterministic, ~10 floating-point operations, no dependencies.
    """
    if attempts <= 0:
        return 0.0
    z = settings.confidence_z if z is None else z
    p = successes / attempts
    z2 = z * z
    denominator = 1.0 + z2 / attempts
    centre = p + z2 / (2.0 * attempts)
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * attempts)) / attempts)
    return max(0.0, (centre - margin) / denominator)


@dataclass(frozen=True)
class RecoveryAction:
    """Evidence for one recovery action against one fingerprint.

    Two sets of numbers, on purpose:

    * ``attempts`` / ``successes`` are what was actually reported. They are
      shown verbatim so the network never hides data from the reader.
    * ``effective_attempts`` / ``effective_successes`` are those counts after
      the per-reporter hourly cap, and they are what confidence is computed
      from. One reporter cannot buy a recommendation by repeating itself.
    """

    action: str
    attempts: int
    successes: int
    effective_attempts: int | None = None
    effective_successes: int | None = None
    unique_reporters: int = 0
    #: The same action inside the decay window (default 24h). Compared against
    #: everything before it to notice a fix that has stopped working.
    recent_attempts: int = 0
    recent_successes: int = 0
    #: Salted hashes of the reporters behind this action. Server-side only --
    #: never serialised into any response. Used solely to answer "did this
    #: evidence come from somebody other than the caller?".
    reporter_hashes: frozenset[str] = frozenset()

    @property
    def capped_attempts(self) -> int:
        return self.attempts if self.effective_attempts is None else self.effective_attempts

    @property
    def capped_successes(self) -> int:
        return (
            self.successes
            if self.effective_successes is None
            else self.effective_successes
        )

    @property
    def success_rate(self) -> float:
        """Observed rate, uncapped -- what the reporters actually saw."""
        return self.successes / self.attempts if self.attempts else 0.0

    @property
    def effective_success_rate(self) -> float:
        return (
            self.capped_successes / self.capped_attempts if self.capped_attempts else 0.0
        )

    @property
    def recent_success_rate(self) -> float | None:
        if self.recent_attempts <= 0:
            return None
        return self.recent_successes / self.recent_attempts

    @property
    def prior_success_rate(self) -> float | None:
        prior_attempts = self.attempts - self.recent_attempts
        if prior_attempts <= 0:
            return None
        return (self.successes - self.recent_successes) / prior_attempts

    @property
    def decaying(self) -> bool:
        """A fix that used to work and, recently, does not.

        Requires enough history to have been a fix (prior attempts at the
        recommendation floor, prior rate at the recommendation threshold),
        enough recent attempts to mean something, and a drop of at least
        ``decay_min_drop``. "5/5 last month, 0/5 this week" is the case; two
        recent attempts that both failed is not yet.
        """
        prior_attempts = self.attempts - self.recent_attempts
        prior, recent = self.prior_success_rate, self.recent_success_rate
        if prior is None or recent is None:
            return False
        if prior_attempts < settings.min_recovery_attempts:
            return False
        if prior < settings.min_recovery_success_rate:
            return False
        if self.recent_attempts < settings.decay_min_recent_attempts:
            return False
        return (prior - recent) >= settings.decay_min_drop

    @property
    def diversity_factor(self) -> float:
        """1.0 once enough distinct reporters agree, a discount otherwise.

        Anonymous and legacy evidence still counts -- it is simply worth less
        than the same evidence from several independent reporters.
        """
        if self.unique_reporters >= settings.min_unique_reporters_for_full_confidence:
            return 1.0
        return settings.low_diversity_confidence_factor

    @property
    def evidence_score(self) -> float:
        """Wilson lower bound on capped counts, discounted for low diversity."""
        return (
            wilson_lower_bound(self.capped_successes, self.capped_attempts)
            * self.diversity_factor
        )


def _cap(attempts: int, successes: int, cap: int) -> tuple[int, int]:
    """Trim one (reporter, hour) bucket to at most ``cap`` attempts.

    Successes are scaled down proportionally and floored, so capping lowers the
    volume without inventing a better success rate than was reported.
    """
    if attempts <= cap:
        return attempts, successes
    return cap, int(successes * cap / attempts)


async def recovery_actions(
    session: AsyncSession, fingerprint: str
) -> list[RecoveryAction]:
    """Aggregate recovery evidence: live rows plus pruned hourly aggregates.

    The per-reporter cap is applied per (action, reporter, hour) bucket, which
    is exactly the grain the aggregates use -- so pruning a bucket cannot
    change the answer.
    """
    cap = settings.max_reporter_weight_per_hour
    bucket = hour_bucket_expr(RecoveryOutcome.created_at)

    rows = (
        await session.execute(
            select(
                RecoveryOutcome.action,
                bucket.label("bucket"),
                RecoveryOutcome.reporter_hash,
                func.count().label("attempts"),
                func.coalesce(
                    func.sum(case((RecoveryOutcome.successful.is_(True), 1), else_=0)),
                    0,
                ).label("successes"),
            )
            .where(RecoveryOutcome.fingerprint == fingerprint)
            .group_by(RecoveryOutcome.action, bucket, RecoveryOutcome.reporter_hash)
        )
    ).all()

    # The recent window, from the live rows. Retention keeps raw outcomes well
    # past the decay window, so nothing here depends on the hourly aggregates.
    recent_rows = (
        await session.execute(
            select(
                RecoveryOutcome.action,
                func.count().label("attempts"),
                func.coalesce(
                    func.sum(case((RecoveryOutcome.successful.is_(True), 1), else_=0)),
                    0,
                ).label("successes"),
            )
            .where(
                RecoveryOutcome.fingerprint == fingerprint,
                RecoveryOutcome.created_at >= ago(settings.decay_window_seconds),
            )
            .group_by(RecoveryOutcome.action)
        )
    ).all()
    recent = {r.action: (int(r.attempts), int(r.successes or 0)) for r in recent_rows}

    totals: dict[str, dict] = {}
    for row in rows:
        entry = totals.setdefault(
            row.action,
            {"attempts": 0, "successes": 0, "eff_a": 0, "eff_s": 0, "reporters": set()},
        )
        attempts, successes = int(row.attempts), int(row.successes or 0)
        # NULL reporter_hash means anonymous. All anonymous reports in a bucket
        # are treated as a single reporter: unattributed evidence cannot prove
        # it is independent.
        eff_a, eff_s = _cap(attempts, successes, cap)
        entry["attempts"] += attempts
        entry["successes"] += successes
        entry["eff_a"] += eff_a
        entry["eff_s"] += eff_s
        if row.reporter_hash is not None:
            entry["reporters"].add(row.reporter_hash)

    archived = (
        await session.execute(
            select(
                HourlyRecoveryStat.action,
                func.coalesce(func.sum(HourlyRecoveryStat.attempts), 0).label("attempts"),
                func.coalesce(func.sum(HourlyRecoveryStat.successes), 0).label("successes"),
                func.coalesce(
                    func.sum(HourlyRecoveryStat.effective_attempts), 0
                ).label("eff_a"),
                func.coalesce(
                    func.sum(HourlyRecoveryStat.effective_successes), 0
                ).label("eff_s"),
                func.coalesce(func.max(HourlyRecoveryStat.unique_reporters), 0).label(
                    "reporters"
                ),
            )
            .where(HourlyRecoveryStat.fingerprint == fingerprint)
            .group_by(HourlyRecoveryStat.action)
        )
    ).all()

    actions: list[RecoveryAction] = []
    for action in set(totals) | {row.action for row in archived}:
        live = totals.get(
            action,
            {"attempts": 0, "successes": 0, "eff_a": 0, "eff_s": 0, "reporters": set()},
        )
        old = next((r for r in archived if r.action == action), None)
        # Reporter identities are not retained in aggregates, so the archived
        # per-bucket maximum is used as a lower bound rather than a sum.
        archived_reporters = int(old.reporters) if old else 0
        actions.append(
            RecoveryAction(
                action=action,
                attempts=live["attempts"] + (int(old.attempts) if old else 0),
                successes=live["successes"] + (int(old.successes) if old else 0),
                effective_attempts=live["eff_a"] + (int(old.eff_a) if old else 0),
                effective_successes=live["eff_s"] + (int(old.eff_s) if old else 0),
                unique_reporters=max(len(live["reporters"]), archived_reporters),
                reporter_hashes=frozenset(live["reporters"]),
                recent_attempts=recent.get(action, (0, 0))[0],
                recent_successes=recent.get(action, (0, 0))[1],
            )
        )

    # Strongest evidence first, ties broken by volume then name (stable output).
    actions.sort(key=lambda a: (-a.evidence_score, -a.capped_attempts, a.action))
    return actions


@dataclass(frozen=True)
class RelatedFailure:
    """Another failure shape on the same service+operation that a recovery
    action has been seen to fix."""

    fingerprint: str
    error_type: str | None
    error_code: str | None
    observations: int
    fixed_by: tuple[tuple[str, int, int], ...]  # (action, successes, attempts)


async def related_failures(
    session: AsyncSession, service: str, operation: str, fingerprint: str, limit: int = 5
) -> list[RelatedFailure]:
    """Other fingerprints on this service+operation, and what fixed them.

    One root cause often wears several masks: an expired token surfaces as
    not_found from one client, auth_error from another, a timeout from a
    third. The error shapes differ, so they are three fingerprints, and each
    alone may never reach the recommendation floor. What joins them is the
    fix. If the same action resolved all three, they are probably one thing.

    This returns the evidence and leaves the inference to the caller: it is
    a list of neighbours with their fixes, not a recommendation, and it only
    includes neighbours that were actually fixed by something -- a neighbour
    nobody has recovered from carries no signal here.
    """
    rows = (
        await session.execute(
            select(
                Fingerprint.fingerprint,
                Fingerprint.error_type,
                Fingerprint.error_code,
                Fingerprint.observation_count,
                RecoveryOutcome.action,
                func.count().label("attempts"),
                func.coalesce(
                    func.sum(case((RecoveryOutcome.successful.is_(True), 1), else_=0)), 0
                ).label("successes"),
            )
            .join(RecoveryOutcome, RecoveryOutcome.fingerprint == Fingerprint.fingerprint)
            .where(
                Fingerprint.service == service,
                Fingerprint.operation == operation,
                Fingerprint.fingerprint != fingerprint,
            )
            .group_by(
                Fingerprint.fingerprint, Fingerprint.error_type, Fingerprint.error_code,
                Fingerprint.observation_count, RecoveryOutcome.action,
            )
        )
    ).all()

    by_fp: dict[str, dict] = {}
    for r in rows:
        if int(r.successes or 0) <= 0:
            continue  # tried, never worked: not a fix, no signal
        entry = by_fp.setdefault(
            r.fingerprint,
            {"error_type": r.error_type, "error_code": r.error_code,
             "observations": int(r.observation_count or 0), "fixed_by": []},
        )
        entry["fixed_by"].append((r.action, int(r.successes), int(r.attempts)))

    related = [
        RelatedFailure(
            fingerprint=fp,
            error_type=e["error_type"],
            error_code=e["error_code"],
            observations=e["observations"],
            fixed_by=tuple(sorted(e["fixed_by"], key=lambda t: (-t[1], t[0]))),
        )
        for fp, e in by_fp.items()
    ]
    related.sort(key=lambda r: (-r.observations, r.fingerprint))
    return related[:limit]


def recommend(actions: list[RecoveryAction]) -> tuple[RecoveryAction, float] | None:
    """Pick an action to recommend, or ``None`` when evidence is too thin.

    Eligibility (constants in config):

    * at least ``min_recovery_attempts`` **effective** attempts, i.e. after the
      per-reporter cap -- 200 reports from one agent are worth 5
    * observed success rate at least ``min_recovery_success_rate``, measured on
      the capped counts

    Reporter diversity does not gate the recommendation, it discounts it:
    evidence from fewer than ``min_unique_reporters_for_full_confidence``
    distinct reporters (including entirely anonymous evidence) is multiplied by
    ``low_diversity_confidence_factor``. Blocking it outright would silently
    break anonymous reporting, which is a supported mode.

    Confidence is the Wilson lower bound after those adjustments, capped at
    ``max_confidence``. We never emit 1.0 -- the network is a prior, not an
    oracle.
    """
    eligible = [
        a
        for a in actions
        if a.capped_attempts >= settings.min_recovery_attempts
        and a.effective_success_rate >= settings.min_recovery_success_rate
    ]
    if not eligible:
        return futility(actions)
    best = max(eligible, key=lambda a: (a.evidence_score, a.capped_attempts))
    confidence = min(best.evidence_score, settings.max_confidence)
    return best, confidence


#: The recommendation the network gives when the evidence says nothing works.
ACTION_SKIP = "skip"


def futility(actions: list[RecoveryAction]) -> tuple[RecoveryAction, float] | None:
    """Recommend ``skip`` when everything tried recently has failed.

    The network's first job was to say what worked. Its second is to say
    when nothing does: an endpoint that has answered 503 to every retry for
    the last day is one where the next retry is a wasted attempt and, for a
    timeout, wasted seconds. Without this, an agent that asks is told "retry:
    0 of 20 worked" and then retries anyway, exactly like one that never
    asked -- which is what the lab's ask/blind tie showed.

    Eligibility, all inside the decay window (default 24h) so a fix that
    landed yesterday is not skipped today:

    * at least ``min_recovery_attempts`` attempts, summed across actions and
      counted after the per-reporter cap (one reporter's retry storm is not
      a verdict)
    * zero successes among them

    Confidence is the Wilson lower bound on the *failure* rate, discounted
    for low reporter diversity the same way a positive recommendation is.
    The action is a real instruction -- do not spend another attempt on
    this; fail fast, escalate, or try something not on the list and report
    the outcome -- and it is reported as a recommendation so a client that
    only reads ``recommendation.action`` sees it.
    """
    recent = [a for a in actions if a.recent_attempts > 0]
    if not recent:
        return None
    attempts = sum(a.recent_attempts for a in recent)
    successes = sum(a.recent_successes for a in recent)
    # capped attempts are lifetime; scale the recent sum by the lifetime cap
    # ratio so a single reporter repeating itself cannot reach the floor
    lifetime_attempts = sum(a.attempts for a in recent) or 1
    capped_lifetime = sum(a.capped_attempts for a in recent)
    capped_recent = int(attempts * capped_lifetime / lifetime_attempts)
    if successes > 0 or capped_recent < settings.min_recovery_attempts:
        return None
    hashes = frozenset().union(*(a.reporter_hashes for a in recent))
    reporters = max(len(hashes), max(a.unique_reporters for a in recent))
    verdict = RecoveryAction(
        action=ACTION_SKIP,
        attempts=attempts,
        successes=0,
        effective_attempts=capped_recent,
        effective_successes=0,
        unique_reporters=reporters,
        recent_attempts=attempts,
        recent_successes=0,
        reporter_hashes=hashes,
    )
    confidence = wilson_lower_bound(capped_recent, capped_recent) * verdict.diversity_factor
    return verdict, min(confidence, settings.max_confidence)


# ---------------------------------------------------------------------------
# Aggregate counters
# ---------------------------------------------------------------------------


async def bump_counter(
    session: AsyncSession, name: str, amount: int = 1, day: str | None = None
) -> None:
    """Increment one daily counter. Integers only, no identities.

    An atomic UPSERT, not read-then-write. The obvious version --
    ``get()`` then ``add()`` or ``+=`` -- races: two concurrent queries both
    see no row, both INSERT, and one of them dies on the unique constraint.
    Under load that surfaced as HTTP 500s on /v1/query, i.e. an agent asking
    what to do got an error instead of its evidence, because of a *metric*.

    SQLite and PostgreSQL share the same ``ON CONFLICT ... DO UPDATE`` syntax,
    so one branch per dialect keeps the migration path intact.
    """
    from app.core.clock import utcnow
    from app.db.database import engine
    from app.db.models import DailyCounter

    bucket = day or utcnow().strftime("%Y-%m-%d")

    if engine.dialect.name == "postgresql":  # pragma: no cover - not used yet
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        from sqlalchemy.dialects.sqlite import insert as dialect_insert

    statement = dialect_insert(DailyCounter).values(
        day=bucket, name=name, value=amount
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=["day", "name"],
            set_={"value": DailyCounter.__table__.c.value + amount},
        )
    )


def is_cross_reporter_evidence(action: RecoveryAction, querying_reporter: str | None) -> bool:
    """Did this recommendation rest on evidence from somebody else?

    The conservative V1 rule:

    * the caller identified itself, and
    * either a live contributing reporter is demonstrably a different reporter,
    * or at least two distinct reporters back the action (so at least one of
      them is not the caller, whoever the caller is).

    Evidence that survived pruning has no identities left, hence the second
    clause. When in doubt this returns False: undercounting the effect is
    honest, overcounting it is not.
    """
    if not querying_reporter:
        return False
    if action.reporter_hashes - {querying_reporter}:
        return True
    return action.unique_reporters >= 2
