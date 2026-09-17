"""Schemas for the public status endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import Status


class ServiceStatus(BaseModel):
    """Current health of one service/operation pair, across all versions."""

    service: str = Field(examples=["github-mcp"])
    operation: str = Field(examples=["create_issue"])
    status: Status = Field(description="MVP heuristic status.")
    failure_rate_5m: float | None = Field(
        description="Failure rate over the last 5 minutes, null if no data."
    )
    failure_rate_1h: float | None = Field(
        description="Failure rate over the last hour, null if no data."
    )
    observations_5m: int = Field(description="Observations in the last 5 minutes.")
    observations_1h: int = Field(description="Observations in the last hour.")
    last_seen: str | None = Field(
        default=None, description="ISO-8601 UTC of the most recent observation."
    )
    demo_data: bool = Field(
        default=False,
        description=(
            "True when every observation behind this row came from demo or "
            "synthetic sources, i.e. neither real agents nor FailEcho's own "
            "agents contributed. Mixed rows are reported as real."
        ),
    )
    first_party_data: bool = Field(
        default=False,
        description=(
            "True when FailEcho's own agents (first_party) contributed "
            "observations to this row: real calls, but not independent ones."
        ),
    )


class NetworkStats(BaseModel):
    """Network-wide counters powering the homepage.

    Real and synthetic telemetry are counted separately and never summed into
    one adoption number. A demo row must never be able to look like a user.
    """

    observations_total: int = Field(
        description=(
            "All observations ever recorded, real and synthetic, including "
            "those that now survive only as hourly aggregates."
        )
    )
    observations_1h: int = Field(description="Observations in the last hour.")
    failures_1h: int = Field(description="Failure observations in the last hour.")
    active_failures: int = Field(
        description="Distinct fingerprints observed in the last 5 minutes."
    )
    real_active_failures: int = Field(
        default=0,
        description=(
            "Distinct fingerprints observed in the last 5 minutes from real "
            "agents only. Demo and synthetic rows are excluded."
        ),
    )
    fingerprints_total: int = Field(description="Distinct failure signatures known.")
    recovery_outcomes_total: int = Field(description="Recovery attempts reported.")
    services_tracked: int = Field(description="Distinct services seen in the last hour.")
    demo_data: bool = Field(
        description=(
            "True when any stored row is demo data -- seeded by "
            "scripts/seed_demo.py or reported by a self-labelled demo agent. "
            "The homepage shows a demo banner while this is true."
        )
    )
    demo_mode: bool = Field(
        default=False,
        description=(
            "True when this deployment runs with FIN_DEMO_MODE=1, i.e. it is a "
            "demonstration instance. It never generates traffic; it only "
            "labels what is stored."
        ),
    )
    synthetic_observations: int = Field(
        description="Observations seeded by scripts/seed_demo.py."
    )
    demo_agent_observations: int = Field(
        default=0,
        description=(
            "Observations reported by agents that labelled themselves demo "
            "(X-Reporter-Kind: demo). Real evidence, deliberately excluded "
            "from adoption metrics."
        ),
    )
    first_party_observations: int = Field(
        default=0,
        description=(
            "Observations reported by FailEcho's own agents, proven with the "
            "operator header. Real evidence, shown to agents as first_party, "
            "never counted as adoption."
        ),
    )
    real_observations_total: int = Field(
        default=0,
        description=(
            "Observations from independent agents, all time -- reporters that "
            "have met the adoption threshold (see adoption_threshold). Rows "
            "from thinner reporters are stored and used as evidence but counted "
            "under sparse_observations instead."
        ),
    )
    sparse_observations: int = Field(
        default=0,
        description=(
            "Anonymous or thin `agent` rows: real, kept, used as evidence, not "
            "yet counted as adoption because their reporter has not met the "
            "threshold. One scanner cannot write the adoption number."
        ),
    )
    adoption_threshold: dict = Field(
        default_factory=dict,
        description=(
            "What a reporter must show before it counts as an independent "
            "agent: min_observations, min_services, min_span_seconds."
        ),
    )
    real_observations_24h: int = Field(
        default=0, description="Observations reported by real agents in the last 24h."
    )
    real_reporters_24h: int = Field(
        default=0,
        description=(
            "Distinct identified real reporters in the last 24h. Anonymous "
            "reports are excluded, so this is a lower bound."
        ),
    )
    real_failure_fingerprints: int = Field(
        default=0,
        description="Distinct failure signatures seen from real agents in the last 24h.",
    )
    real_failures_24h: int = Field(
        default=0, description="Real failure observations in the last 24h."
    )
    real_successes_24h: int = Field(
        default=0,
        description=(
            "Real success observations in the last 24h. Without these, every "
            "failure rate in FailEcho is meaningless."
        ),
    )
    known_query_hits_24h: int = Field(
        default=0,
        description="Real queries in the last 24h that matched a known failure.",
    )
    unknown_query_hits_24h: int = Field(
        default=0,
        description="Real queries in the last 24h that found nothing.",
    )
    known_hit_rate_24h: float | None = Field(
        default=None,
        description=(
            "known / (known + unknown) queries, last 24h. Null when no real "
            "queries were made. The headline experiment number: it says how "
            "often asking FailEcho was worth the round trip."
        ),
    )
    recovery_outcome_ratio_24h: float | None = Field(
        default=None,
        description=(
            "Recovery outcomes reported per real failure reported, last 24h. "
            "Null when no real failures were reported. Low values mean agents "
            "report failures but never say whether the fix worked, which is "
            "the difference between evidence and an error counter."
        ),
    )
    cross_agent_help_24h: int = Field(
        default=0,
        description=(
            "Real queries in the last 24h where an identified caller received "
            "a recommendation backed by at least one other reporter -- i.e. an "
            "agent benefited from evidence it did not generate. Conservative: "
            "anonymous callers and single-reporter evidence are not counted."
        ),
    )
    archived_observations: int = Field(
        default=0,
        description=(
            "Observations whose raw rows were pruned and now live only as "
            "hourly aggregates. Included in observations_total."
        ),
    )
    generated_at: str = Field(description="ISO-8601 UTC timestamp of this snapshot.")


class HealthResponse(BaseModel):
    status: str = Field(description="'ok' when the service and database are up.")


class RecoveryIntelligence(BaseModel):
    """One well-evidenced recovery action, for the homepage and for agents."""

    service: str
    operation: str
    error_type: str | None = None
    error_code: str | None = None
    normalized_error: str | None = None
    fingerprint: str
    action: str = Field(description="Best observed recovery action.")
    attempts: int
    successes: int
    success_rate: float
    confidence: float = Field(description="Wilson lower bound, diversity adjusted.")
    unique_reporters: int
    observations: int = Field(description="Observations of this failure signature.")
    last_seen: str | None = None
    demo_data: bool = Field(
        description="True when synthetic demo rows back this entry."
    )
    first_party_data: bool = Field(
        default=False,
        description="True when FailEcho's own agents contributed evidence to this entry.",
    )
