"""Failure rates, incident status, recovery evidence and confidence."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.intelligence import (
    WindowCounts,
    classify_status,
    recommend,
    RecoveryAction,
    wilson_lower_bound,
)
from tests.conftest import observe, query


# --- pure functions -------------------------------------------------------


def test_window_counts_rate():
    counts = WindowCounts(total=10, failures=3)
    assert counts.successes == 7
    assert counts.failure_rate == pytest.approx(0.3)


def test_empty_window_rate_is_unknown_not_zero():
    assert WindowCounts().failure_rate is None


@pytest.mark.parametrize(
    "short,long,expected",
    [
        (WindowCounts(), WindowCounts(total=9, failures=9), "INSUFFICIENT_DATA"),
        (WindowCounts(), WindowCounts(total=100, failures=2), "HEALTHY"),
        (WindowCounts(), WindowCounts(total=100, failures=21), "DEGRADED"),
        (WindowCounts(), WindowCounts(total=100, failures=60), "MAJOR"),
        # Boundaries belong to the worse bucket: >= is not <.
        (WindowCounts(), WindowCounts(total=100, failures=5), "DEGRADED"),
        (WindowCounts(), WindowCounts(total=100, failures=30), "MAJOR"),
        # A fresh spike in the 5m window wins over a calm hour.
        (
            WindowCounts(total=20, failures=16),
            WindowCounts(total=200, failures=18),
            "MAJOR",
        ),
        # ...but only once the short window has volume.
        (
            WindowCounts(total=2, failures=2),
            WindowCounts(total=200, failures=2),
            "HEALTHY",
        ),
    ],
)
def test_classify_status(short, long, expected):
    assert classify_status(short, long) == expected


def test_thresholds_are_configurable_constants():
    assert settings.healthy_max_failure_rate == 0.05
    assert settings.degraded_max_failure_rate == 0.30
    assert settings.min_observations_for_status == 10


def test_wilson_rewards_sample_size():
    """Same rate, more evidence -> higher confidence."""
    assert wilson_lower_bound(5, 5) < wilson_lower_bound(50, 50)
    assert wilson_lower_bound(117, 124) == pytest.approx(0.8881, abs=1e-4)
    assert wilson_lower_bound(0, 0) == 0.0


def test_recommendation_requires_evidence():
    thin = [RecoveryAction(action="reconnect", attempts=3, successes=3)]
    assert recommend(thin) is None

    weak = [RecoveryAction(action="retry", attempts=91, successes=17)]
    assert recommend(weak) is None

    strong = [
        RecoveryAction(action="retry", attempts=91, successes=17, unique_reporters=12),
        RecoveryAction(
            action="refresh_schema", attempts=124, successes=117, unique_reporters=47
        ),
    ]
    action, confidence = recommend(strong)
    assert action.action == "refresh_schema"
    assert confidence == pytest.approx(0.8881, abs=1e-4)
    assert confidence < 1.0


def test_confidence_is_capped_below_one():
    huge = [RecoveryAction(action="retry", attempts=100000, successes=100000)]
    _, confidence = recommend(huge)
    assert confidence <= settings.max_confidence < 1.0


# --- through the API ------------------------------------------------------


def test_failures_and_successes_move_the_rate(client):
    for _ in range(2):
        observe(client)
    for _ in range(8):
        observe(client, outcome="success", error_type=None, error_code=None,
                error_message=None)

    intel = query(client)
    assert intel["observations"]["total"] == 2
    assert intel["failure_rate"]["last_5m"] == pytest.approx(0.2)
    assert intel["failure_rate"]["last_1h"] == pytest.approx(0.2)
    assert intel["status"] == "DEGRADED"


def test_all_success_scope_is_healthy(client):
    for _ in range(20):
        observe(client, outcome="success", error_type=None, error_code=None,
                error_message=None)
    intel = query(client)
    assert intel["failure_rate"]["last_1h"] == 0.0
    assert intel["status"] == "HEALTHY"
    assert intel["known"] is False


def test_major_when_most_calls_fail(client):
    for _ in range(15):
        observe(client)
    for _ in range(5):
        observe(client, outcome="success", error_type=None, error_code=None,
                error_message=None)
    assert query(client)["status"] == "MAJOR"


def test_insufficient_data_returns_no_recommendation(client):
    observe(client)
    intel = query(client)
    assert intel["known"] is True
    assert intel["status"] == "INSUFFICIENT_DATA"
    assert intel["recommendation"] is None


def test_recovery_success_rates_are_aggregated(client):
    fingerprint = observe(client)["fingerprint"]
    for successful in [True] * 9 + [False]:
        client.post(
            "/v1/outcome",
            json={
                "fingerprint": fingerprint,
                "action": "refresh_schema",
                "successful": successful,
            },
        )
    for successful in [True, False, False, False, False]:
        client.post(
            "/v1/outcome",
            json={"fingerprint": fingerprint, "action": "retry", "successful": successful},
        )

    actions = {a["action"]: a for a in query(client)["recovery_actions"]}
    assert actions["refresh_schema"]["attempts"] == 10
    assert actions["refresh_schema"]["successes"] == 9
    assert actions["refresh_schema"]["success_rate"] == pytest.approx(0.9)
    assert actions["retry"]["success_rate"] == pytest.approx(0.2)

    recommendation = query(client)["recommendation"]
    assert recommendation["action"] == "refresh_schema"
    assert 0 < recommendation["confidence"] < 1


def test_recommendation_withheld_until_threshold(client):
    fingerprint = observe(client)["fingerprint"]
    for _ in range(settings.min_recovery_attempts - 1):
        client.post(
            "/v1/outcome",
            json={"fingerprint": fingerprint, "action": "reconnect", "successful": True},
        )
    assert query(client)["recommendation"] is None

    client.post(
        "/v1/outcome",
        json={"fingerprint": fingerprint, "action": "reconnect", "successful": True},
    )
    assert query(client)["recommendation"]["action"] == "reconnect"


def test_unknown_failure_is_honest(client):
    intel = query(client, service="nobody-mcp", operation="never_called")
    assert intel["known"] is False
    assert intel["status"] == "INSUFFICIENT_DATA"
    assert intel["recommendation"] is None
    assert intel["observations"]["total"] == 0
    assert intel["failure_rate"]["last_1h"] is None


def test_new_incidents_are_flagged(client):
    observe(client)
    assert query(client)["looks_new"] is True


def test_unique_reporters_are_counted_not_events(client):
    for _ in range(10):
        observe(client, headers={"X-Reporter-ID": "noisy-agent"})
    intel = query(client)
    assert intel["observations"]["total"] == 10
    assert intel["observations"]["unique_reporters"] == 1
