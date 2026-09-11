"""Central configuration and tunable constants.

Everything that a human might want to tweak (thresholds, windows, retention,
database URL) lives here so the intelligence layer stays free of magic numbers.

All values can be overridden with environment variables prefixed ``FIN_``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Status vocabulary
# --------------------------------------------------------------------------
STATUS_HEALTHY = "HEALTHY"
STATUS_DEGRADED = "DEGRADED"
STATUS_MAJOR = "MAJOR"
STATUS_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

# Outcome vocabulary
OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"

# Provenance of a row. Four kinds, kept apart everywhere they surface:
#   agent        telemetry from a real autonomous system (counts as adoption)
#   first_party  FailEcho's operator's own agents, proven by a secret header.
#                Real calls and real evidence, but not independent, and never
#                adoption
#   demo_agent   an example/demo agent that self-identified as such
#   synthetic    rows generated locally by scripts/seed_demo.py
# Only SOURCE_AGENT is ever reported as real adoption.
SOURCE_AGENT = "agent"
SOURCE_FIRST_PARTY = "first_party"
SOURCE_DEMO_AGENT = "demo_agent"
SOURCE_SYNTHETIC = "synthetic"

# Illustrative sources: demo data, never field evidence and never adoption.
# first_party is deliberately absent -- it is field evidence, just not
# independent -- and adoption is decided by SOURCE_AGENT alone.
NON_REAL_SOURCES = (SOURCE_DEMO_AGENT, SOURCE_SYNTHETIC)

# Aggregate counter names (see app/db/models.py::DailyCounter).
COUNTER_QUERY_KNOWN = "query_known"
COUNTER_QUERY_UNKNOWN = "query_unknown"
COUNTER_CROSS_AGENT_HELP = "cross_agent_help"

# Request header a caller uses to label itself as a demo agent.
REPORTER_KIND_HEADER = "X-Reporter-Kind"
REPORTER_KIND_DEMO = "demo"

# Header the operator's own agents send, carrying FIN_FIRST_PARTY_TOKEN.
OPERATOR_HEADER = "X-FailEcho-Operator"


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    """Runtime settings. Instantiated once as :data:`settings`."""

    # ---- storage ---------------------------------------------------------
    # SQLite for the prototype. Migration to PostgreSQL is a URL swap:
    #   FIN_DATABASE_URL=postgresql+asyncpg://user:pass@host/dbname
    database_url: str = field(
        default_factory=lambda: _env_str(
            "FIN_DATABASE_URL", "sqlite+aiosqlite:///./data/failure_network.db"
        )
    )
    sql_echo: bool = field(default_factory=lambda: _env_str("FIN_SQL_ECHO", "0") == "1")

    # ---- privacy ---------------------------------------------------------
    # Salt applied before hashing an optional X-Reporter-ID header. Rotating
    # this value makes previously stored reporter hashes unlinkable.
    reporter_salt: str = field(
        default_factory=lambda: _env_str("FIN_REPORTER_SALT", "dev-salt-change-me")
    )
    # Secret proving a caller is one of the operator's own agents
    # (X-FailEcho-Operator). Empty disables the label: every such claim is
    # then stored as demo -- never as adoption, never as operator evidence.
    first_party_token: str = field(
        default_factory=lambda: _env_str("FIN_FIRST_PARTY_TOKEN", "")
    )

    # ---- time windows (seconds) -----------------------------------------
    window_short_seconds: int = field(
        default_factory=lambda: _env_int("FIN_WINDOW_SHORT_SECONDS", 300)  # 5 minutes
    )
    window_long_seconds: int = field(
        default_factory=lambda: _env_int("FIN_WINDOW_LONG_SECONDS", 3600)  # 1 hour
    )

    # ---- incident detection (MVP heuristic, deliberately simple) ---------
    # Below this many observations in the long window we refuse to guess.
    min_observations_for_status: int = field(
        default_factory=lambda: _env_int("FIN_MIN_OBSERVATIONS_FOR_STATUS", 10)
    )
    # The short window only overrides the long window once it has some volume.
    min_observations_short_window: int = field(
        default_factory=lambda: _env_int("FIN_MIN_OBSERVATIONS_SHORT_WINDOW", 5)
    )
    healthy_max_failure_rate: float = field(
        default_factory=lambda: _env_float("FIN_HEALTHY_MAX_FAILURE_RATE", 0.05)
    )
    degraded_max_failure_rate: float = field(
        default_factory=lambda: _env_float("FIN_DEGRADED_MAX_FAILURE_RATE", 0.30)
    )

    # ---- recovery recommendation ----------------------------------------
    min_recovery_attempts: int = field(
        default_factory=lambda: _env_int("FIN_MIN_RECOVERY_ATTEMPTS", 5)
    )
    min_recovery_success_rate: float = field(
        default_factory=lambda: _env_float("FIN_MIN_RECOVERY_SUCCESS_RATE", 0.60)
    )
    # z for the Wilson score interval: 1.96 == 95% confidence level.
    confidence_z: float = field(
        default_factory=lambda: _env_float("FIN_CONFIDENCE_Z", 1.96)
    )
    # We never claim certainty, no matter how much evidence exists.
    max_confidence: float = field(
        default_factory=lambda: _env_float("FIN_MAX_CONFIDENCE", 0.99)
    )

    # ---- abuse floor (V1: transparent caps, not a reputation system) -----
    # One reporter may contribute at most this many observations of the same
    # (fingerprint, action) inside one hour bucket when confidence is computed.
    # Raw counts are still reported verbatim; only the *evidence* is capped.
    max_reporter_weight_per_hour: int = field(
        default_factory=lambda: _env_int("FIN_MAX_REPORTER_WEIGHT_PER_HOUR", 5)
    )
    # Recommendations backed by fewer distinct reporters than this are still
    # made (demo and anonymous data must keep working) but carry less weight.
    min_unique_reporters_for_full_confidence: int = field(
        default_factory=lambda: _env_int("FIN_MIN_UNIQUE_REPORTERS", 3)
    )
    # Multiplier applied to confidence when reporter diversity is unproven.
    low_diversity_confidence_factor: float = field(
        default_factory=lambda: _env_float("FIN_LOW_DIVERSITY_CONFIDENCE_FACTOR", 0.7)
    )

    # ---- rate limiting (in-process, single node -- see README) -----------
    rate_limit_enabled: bool = field(
        default_factory=lambda: _env_str("FIN_RATE_LIMIT_ENABLED", "1") == "1"
    )
    # Writes only. Reads are deliberately unthrottled: querying is the point.
    rate_limit_writes_per_minute: int = field(
        default_factory=lambda: _env_int("FIN_RATE_LIMIT_WRITES_PER_MINUTE", 120)
    )
    # Bound on the limiter's memory so a spray of source IPs cannot grow it.
    rate_limit_max_tracked_clients: int = field(
        default_factory=lambda: _env_int("FIN_RATE_LIMIT_MAX_CLIENTS", 20_000)
    )
    # Behind Cloudflare/nginx the peer address is the proxy, so trust a
    # forwarded header instead. OFF by default: trusting it when not behind a
    # proxy lets any client forge its own rate-limit identity.
    trust_proxy_headers: bool = field(
        default_factory=lambda: _env_str("FIN_TRUST_PROXY", "0") == "1"
    )

    # ---- retention -------------------------------------------------------
    # Raw observations are rolled into hourly aggregates and deleted after
    # this many hours. Aggregates are kept indefinitely.
    retention_hours: int = field(
        default_factory=lambda: _env_int("FIN_RETENTION_HOURS", 48)
    )

    # ---- MCP -------------------------------------------------------------
    mcp_enabled: bool = field(
        default_factory=lambda: _env_str("FIN_MCP_ENABLED", "1") == "1"
    )
    mcp_path: str = field(default_factory=lambda: _env_str("FIN_MCP_PATH", "/mcp"))
    # Comma-separated Host / Origin allowlists for MCP DNS-rebinding
    # protection. Empty (default) disables the check: this service is public,
    # unauthenticated and holds no per-user data, so there is nothing for a
    # rebinding attack to steal that a direct request could not fetch.
    mcp_allowed_hosts: str = field(
        default_factory=lambda: _env_str("FIN_MCP_ALLOWED_HOSTS", "")
    )
    mcp_allowed_origins: str = field(
        default_factory=lambda: _env_str("FIN_MCP_ALLOWED_ORIGINS", "")
    )

    # ---- public identity -------------------------------------------------
    # The canonical public origin, used for canonical tags, Open Graph URLs,
    # /llms.txt and the integration examples. One source of truth: the
    # production domain is never written anywhere else in the codebase.
    public_url: str = field(
        default_factory=lambda: _env_str("FIN_PUBLIC_URL", "http://localhost:8000")
    )
    # Left empty until the repository is public. Nothing renders a GitHub link
    # while this is unset -- an invented URL is worse than no link.
    github_url: str = field(default_factory=lambda: _env_str("FIN_GITHUB_URL", ""))

    def base_url(self) -> str:
        """Public origin without a trailing slash."""
        return self.public_url.rstrip("/")

    def url_for(self, path: str) -> str:
        return f"{self.base_url()}/{path.lstrip('/')}"

    # ---- demo / first-launch --------------------------------------------
    # FIN_DEMO_MODE=1 tells the homepage and /v1/stats that this deployment is
    # a demonstration. It never generates traffic by itself -- it only labels
    # what is already stored.
    demo_mode: bool = field(
        default_factory=lambda: _env_str("FIN_DEMO_MODE", "0") == "1"
    )

    # ---- read caching ----------------------------------------------------
    # The three read-only dashboard endpoints return network-wide numbers that
    # are identical for every caller, so they are memoised for this long.
    # 0 disables it, which is what the test suite uses.
    dashboard_cache_seconds: float = field(
        default_factory=lambda: _env_float("FIN_DASHBOARD_CACHE_SECONDS", 10.0)
    )

    # ---- misc ------------------------------------------------------------
    services_default_limit: int = field(
        default_factory=lambda: _env_int("FIN_SERVICES_DEFAULT_LIMIT", 100)
    )
    # Browser origins allowed to call the API. "*" keeps the public API usable
    # from any page; narrow it once a first-party dashboard exists.
    allowed_origins: str = field(
        default_factory=lambda: _env_str("FIN_ALLOWED_ORIGINS", "*")
    )

    def origin_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    def mcp_host_list(self) -> list[str]:
        return [h.strip() for h in self.mcp_allowed_hosts.split(",") if h.strip()]

    def mcp_origin_list(self) -> list[str]:
        return [o.strip() for o in self.mcp_allowed_origins.split(",") if o.strip()]


settings = Settings()
