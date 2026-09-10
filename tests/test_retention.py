"""Retention: aggregate, delete, and never double count.

The invariant under test is that raw rows and hourly aggregates describe
disjoint sets of observations, so "raw + aggregates" is a total. Everything
else here follows from that.
"""

from __future__ import annotations

from app.core.fingerprint import compute_fingerprint
from app.core.normalize import normalize_error
from tests.conftest import (
    insert_fingerprint,
    insert_observation,
    insert_recovery,
    observe,
    query,
    run_prune,
    rows as _rows_fixture,  # noqa: F401  (re-exported fixture)
)

# The fingerprint the default conftest payloads produce, so rows inserted
# straight into SQLite line up with what /v1/query computes.
FINGERPRINT = compute_fingerprint(
    service="github-mcp",
    operation="create_issue",
    version="2.8.1",
    schema_hash="a817ce",
    error_type="validation_error",
    error_code="422",
    normalized_error=normalize_error("Repository 918272 was not found"),
)


def seed_history(client, *, old: int = 10, recent: int = 4) -> None:
    """Rows from 3 hours ago (expired) plus rows from now (still hot)."""
    insert_fingerprint(FINGERPRINT)
    for index in range(old):
        insert_observation(
            minutes_ago=180,
            fingerprint=FINGERPRINT,
            outcome="failure" if index % 2 else "success",
            reporter_hash=f"reporter-{index % 3}",
            latency_ms=100 + index,
        )
    for _ in range(recent):
        insert_observation(minutes_ago=1, fingerprint=FINGERPRINT, outcome="failure")


def test_expired_observations_are_aggregated_and_deleted(client, rows):
    seed_history(client)
    assert len(rows("observations")) == 14

    run_prune("--hours", "2")

    remaining = rows("observations")
    assert len(remaining) == 4, "rows inside the retention window stay raw"
    assert all(row["fingerprint"] == FINGERPRINT for row in remaining)

    buckets = rows("hourly_stats")
    assert buckets, "expired rows must survive as aggregates"
    assert sum(b["success_count"] + b["failure_count"] for b in buckets) == 10
    assert sum(b["success_count"] for b in buckets) == 5
    assert sum(b["failure_count"] for b in buckets) == 5
    assert max(b["unique_reporters"] for b in buckets) == 3
    assert sum(b["latency_count"] for b in buckets) == 10


def test_historical_totals_survive_pruning(client, rows):
    seed_history(client)
    before = client.get("/v1/stats").json()["observations_total"]
    fingerprint_before = query(client)["observations"]["total"]

    run_prune("--hours", "2")

    after = client.get("/v1/stats").json()
    assert after["observations_total"] == before == 14
    assert after["archived_observations"] == 10
    assert query(client)["observations"]["total"] == fingerprint_before == 14


def test_prune_is_idempotent(client, rows):
    seed_history(client)
    run_prune("--hours", "2")
    first = client.get("/v1/stats").json()
    bucket_rows = rows("hourly_stats")

    run_prune("--hours", "2")
    run_prune("--hours", "2")

    assert client.get("/v1/stats").json()["observations_total"] == first["observations_total"]
    assert rows("hourly_stats") == bucket_rows, "no new or inflated buckets"
    assert len(rows("observations")) == 4


def test_second_prune_tops_up_the_same_bucket(client, rows):
    """A late-arriving row for an already-pruned hour merges, not duplicates."""
    insert_fingerprint(FINGERPRINT)
    insert_observation(minutes_ago=180, fingerprint=FINGERPRINT, outcome="failure")
    run_prune("--hours", "2")
    assert len(rows("hourly_stats")) == 1

    insert_observation(minutes_ago=180, fingerprint=FINGERPRINT, outcome="failure")
    run_prune("--hours", "2")

    buckets = rows("hourly_stats")
    assert len(buckets) == 1, "same hour bucket, merged"
    assert buckets[0]["failure_count"] == 2


def test_recent_windows_are_unaffected_by_pruning(client):
    """5m/1h windows read raw rows only, so pruning cannot change a status."""
    for _ in range(12):
        observe(client)
    before = query(client)
    run_prune("--hours", "2")
    after = query(client)

    assert after["status"] == before["status"] == "MAJOR"
    assert after["failure_rate"]["last_1h"] == before["failure_rate"]["last_1h"]
    assert after["observations"]["last_1h"] == before["observations"]["last_1h"]


def test_recovery_evidence_survives_pruning(client, rows):
    insert_fingerprint(FINGERPRINT)
    insert_observation(minutes_ago=180, fingerprint=FINGERPRINT)
    for index in range(12):
        insert_recovery(
            fingerprint=FINGERPRINT,
            action="refresh_schema",
            successful=index != 0,
            minutes_ago=180,
            reporter_hash=f"reporter-{index % 4}",
        )

    before = query(client, error_message="Repository 111111 was not found")
    before_action = _find(before, "refresh_schema")

    run_prune("--hours", "2")

    assert rows("recovery_outcomes") == []
    archived = rows("hourly_recovery_stats")
    assert len(archived) == 1
    assert archived[0]["attempts"] == 12
    assert archived[0]["successes"] == 11
    assert archived[0]["unique_reporters"] == 4

    after = query(client, error_message="Repository 222222 was not found")
    after_action = _find(after, "refresh_schema")
    assert after_action["attempts"] == before_action["attempts"] == 12
    assert after_action["effective_attempts"] == before_action["effective_attempts"]
    assert after_action["confidence"] == before_action["confidence"]


def test_reporter_cap_is_preserved_across_pruning(client, rows):
    """One spammer's archived evidence stays capped after aggregation."""
    insert_fingerprint(FINGERPRINT)
    insert_observation(minutes_ago=180, fingerprint=FINGERPRINT)
    for _ in range(40):
        insert_recovery(
            fingerprint=FINGERPRINT,
            action="retry",
            successful=True,
            minutes_ago=180,
            reporter_hash="spammer",
        )

    run_prune("--hours", "2")

    archived = rows("hourly_recovery_stats")[0]
    assert archived["attempts"] == 40
    assert archived["effective_attempts"] == 5
    action = _find(query(client, error_message="Repository 333333 was not found"), "retry")
    assert action["attempts"] == 40
    assert action["effective_attempts"] == 5


def test_dry_run_changes_nothing(client, rows):
    seed_history(client)
    result = run_prune("--hours", "2", "--dry-run")

    assert "dry run" in result.stdout
    assert "Aggregated 10 observations" in result.stdout
    assert len(rows("observations")) == 14
    assert rows("hourly_stats") == []


def test_prune_reports_what_it_did(client):
    seed_history(client)
    output = run_prune("--hours", "2").stdout
    assert "Aggregated 10 observations" in output
    assert "Deleted 10 raw observations" in output
    assert "Database size:" in output


def test_default_window_keeps_recent_data(client, rows):
    """With the 48h default, nothing from the last three hours is touched."""
    seed_history(client)
    run_prune()
    assert len(rows("observations")) == 14
    assert rows("hourly_stats") == []


def _find(intel: dict, action: str) -> dict:
    return next(a for a in intel["recovery_actions"] if a["action"] == action)
