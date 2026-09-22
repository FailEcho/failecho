"""SQLAlchemy models.

Portability note: every column type used here (String, Integer, Float, Boolean,
DateTime, Text) maps cleanly onto PostgreSQL. Timestamps are naive UTC, there
are no SQLite-specific types, and no expression indexes -- so migrating is
``FIN_DATABASE_URL=postgresql+asyncpg://...`` plus one Alembic baseline.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.clock import utcnow
from app.core.config import SOURCE_AGENT


class Base(DeclarativeBase):
    pass


class Observation(Base):
    """One anonymous tool-call outcome reported by an agent.

    Both successes and failures land here: failure rates are meaningless
    without the denominator.
    """

    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    # --- what was called -------------------------------------------------
    service: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- how it went -----------------------------------------------------
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)  # success|failure
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # PRIVACY: normalized only. The raw message never reaches this column.
    normalized_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Declared by the reporter: does this operation change state? Null when
    #: not declared. One line of annotation from the caller beats any
    #: heuristic on the name, so where this is set it wins.
    mutates: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # --- provenance ------------------------------------------------------
    # PRIVACY: salted hash of an optional X-Reporter-ID, or NULL (anonymous).
    reporter_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(
        String(16), default=SOURCE_AGENT, nullable=False
    )  # agent | synthetic

    __table_args__ = (
        # Health of a service/operation/version/schema over a time window.
        Index(
            "ix_obs_scope_time",
            "service",
            "operation",
            "version",
            "schema_hash",
            "created_at",
        ),
        # "how common is this exact failure right now"
        Index("ix_obs_fingerprint_time", "fingerprint", "created_at"),
        # Network-wide rollups for the homepage.
        Index("ix_obs_created_at", "created_at"),
    )


class RecoveryOutcome(Base):
    """What an agent tried after a known failure, and whether it worked."""

    __tablename__ = "recovery_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    successful: Mapped[bool] = mapped_column(Boolean, nullable=False)

    reporter_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(
        String(16), default=SOURCE_AGENT, nullable=False
    )

    __table_args__ = (
        Index("ix_recovery_fingerprint_action", "fingerprint", "action"),
        Index("ix_recovery_created_at", "created_at"),
    )


class Fingerprint(Base):
    """Denormalized catalogue of known failure signatures.

    Redundant with ``observations`` on purpose: it makes "is this failure
    known?" a single primary-key lookup and keeps the homepage counters O(1)
    instead of scanning the observation table.
    """

    __tablename__ = "fingerprints"

    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)

    service: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    normalized_error: Mapped[str | None] = mapped_column(String(512), nullable=True)

    first_seen: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    observation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        Index("ix_fingerprints_service_operation", "service", "operation"),
        Index("ix_fingerprints_last_seen", "last_seen"),
    )


class HourlyStat(Base):
    """Hourly rollup of observations whose raw rows have been pruned.

    Retention model: raw observations live for ``FIN_RETENTION_HOURS`` (48h by
    default) and are then folded into these buckets and deleted. The two never
    overlap -- a row is aggregated and deleted in the same transaction -- so
    "raw + aggregates" is a total, never a double count.

    ``unique_reporters`` is a per-bucket distinct count. If a bucket is ever
    topped up by a second prune run (late-arriving rows), that count is merged
    with ``max()``, which makes it a lower bound rather than a fiction.
    """

    __tablename__ = "hourly_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    service: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unique_reporters: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_sum: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    source: Mapped[str] = mapped_column(String(16), default=SOURCE_AGENT, nullable=False)

    __table_args__ = (
        Index("ix_hourly_bucket", "bucket_start"),
        Index("ix_hourly_scope", "service", "operation", "bucket_start"),
        Index("ix_hourly_fingerprint", "fingerprint"),
        Index("ix_hourly_source", "source"),
    )


class HourlyRecoveryStat(Base):
    """Hourly rollup of recovery outcomes whose raw rows have been pruned.

    ``effective_*`` are the per-reporter-capped counts (see
    :data:`app.core.config.Settings.max_reporter_weight_per_hour`). The cap is
    applied at aggregation time because an hour bucket is exactly the window
    the cap is defined over, so pruning cannot change a recommendation.
    """

    __tablename__ = "hourly_recovery_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)

    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    successes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    effective_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    effective_successes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unique_reporters: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    source: Mapped[str] = mapped_column(String(16), default=SOURCE_AGENT, nullable=False)

    __table_args__ = (
        Index("ix_hourly_recovery_fingerprint", "fingerprint", "action"),
        Index("ix_hourly_recovery_bucket", "bucket_start"),
    )


class ReporterKey(Base):
    """A reporter that has proven it holds the private key for its id.

    Verification is a property of the reporter, not of a row: a reporter signs
    or it does not, and rows stay exactly as they were. That also means this
    table can be added to a live database without touching `observations`.

    PRIVACY: the stored id is the same salted hash as everywhere else. The
    public key is not kept -- it is in the reporter id the client sends with
    every request, and keeping a copy here would make the hash reversible.
    """

    __tablename__ = "reporter_keys"

    reporter_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_verified: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_verified: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    signed_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (Index("ix_reporter_keys_last", "last_verified"),)


class DailyCounter(Base):
    """Small aggregate counters, one row per (UTC day, name).

    These exist for one purpose: measuring whether the network is actually
    working -- how often a query finds evidence, and how often that evidence
    came from somebody else. They hold integers only. No reporter identities,
    no fingerprints, nothing that could identify a caller.

    Only real agent traffic is counted; demo and synthetic callers are skipped,
    so the experiment numbers cannot be inflated by the demo.
    """

    __tablename__ = "daily_counters"

    day: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
