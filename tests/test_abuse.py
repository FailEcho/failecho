"""The V1 abuse floor: caps on influence, caps on request volume.

Two independent defences, tested separately:

* the per-reporter evidence cap limits how much one reporter can move a
  recommendation, no matter how many reports it sends or from how many IPs;
* the rate limiter limits how many write requests one client IP can send.
"""

from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.core.intelligence import RecoveryAction, _cap, recommend
from app.core.ratelimit import write_limiter
from tests.conftest import mcp_call, observe, query


# ---------------------------------------------------------------------------
# per-reporter evidence cap
# ---------------------------------------------------------------------------


def test_cap_scales_successes_proportionally():
    """Capping lowers volume without inventing a better success rate."""
    assert _cap(3, 3, 5) == (3, 3)
    assert _cap(100, 100, 5) == (5, 5)
    assert _cap(100, 50, 5) == (5, 2)  # 50% stays ~50%, floored


def report_recovery(client, fingerprint, *, action, successful, reporter=None, times=1):
    headers = {"X-Reporter-ID": reporter} if reporter else None
    for _ in range(times):
        response = client.post(
            "/v1/outcome",
            json={
                "fingerprint": fingerprint,
                "action": action,
                "successful": successful,
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text


def test_one_reporter_cannot_dominate_a_fingerprint(client):
    fingerprint = observe(client)["fingerprint"]
    report_recovery(
        client, fingerprint, action="retry", successful=True, reporter="spammer", times=50
    )

    action = query(client)["recovery_actions"][0]
    assert action["attempts"] == 50, "raw counts are still reported honestly"
    assert action["effective_attempts"] == settings.max_reporter_weight_per_hour
    assert action["effective_successes"] == settings.max_reporter_weight_per_hour
    assert action["unique_reporters"] == 1


def test_more_reporters_mean_more_evidence(client):
    """Same raw volume, more independent reporters -> higher confidence."""
    fingerprint = observe(client)["fingerprint"]
    report_recovery(
        client, fingerprint, action="retry", successful=True, reporter="solo", times=30
    )
    solo = next(
        a for a in query(client)["recovery_actions"] if a["action"] == "retry"
    )

    for index in range(6):
        report_recovery(
            client,
            fingerprint,
            action="use_fallback",
            successful=True,
            reporter=f"agent-{index}",
            times=5,
        )
    crowd = next(
        a for a in query(client)["recovery_actions"] if a["action"] == "use_fallback"
    )

    assert solo["attempts"] == 30 and crowd["attempts"] == 30
    assert crowd["effective_attempts"] == 30
    assert solo["effective_attempts"] == 5
    assert crowd["unique_reporters"] == 6
    assert crowd["confidence"] > solo["confidence"]
    assert query(client)["recommendation"]["action"] == "use_fallback"


def test_spam_cannot_buy_a_recommendation(client):
    """A single reporter's 50 successes are worth 5 attempts, not 50."""
    fingerprint = observe(client)["fingerprint"]
    report_recovery(
        client, fingerprint, action="retry", successful=True, reporter="spammer", times=50
    )
    recommendation = query(client)["recommendation"]
    assert recommendation["effective_attempts"] == 5
    assert recommendation["unique_reporters"] == 1
    # Confidence for 5/5 from one reporter, not 50/50 from a crowd.
    assert recommendation["confidence"] < 0.5


def test_low_diversity_is_discounted_not_blocked():
    """Anonymous and single-reporter evidence still counts -- for less."""
    diverse = RecoveryAction(
        action="refresh_schema", attempts=20, successes=19, unique_reporters=8
    )
    anonymous = RecoveryAction(action="refresh_schema", attempts=20, successes=19)

    assert diverse.diversity_factor == 1.0
    assert anonymous.diversity_factor == settings.low_diversity_confidence_factor
    assert recommend([anonymous]) is not None, "anonymous evidence is not blocked"
    assert anonymous.evidence_score < diverse.evidence_score


def test_anonymous_reporting_still_works_end_to_end(client):
    fingerprint = observe(client)["fingerprint"]
    report_recovery(client, fingerprint, action="reconnect", successful=True, times=6)
    intel = query(client)
    assert intel["recommendation"]["action"] == "reconnect"
    assert intel["recovery_actions"][0]["unique_reporters"] == 0


def test_reporter_ids_are_never_stored_raw_on_the_write_path(client, rows):
    observe(client, headers={"X-Reporter-ID": "plaintext-agent-id"})
    fingerprint = rows("observations")[0]["fingerprint"]
    report_recovery(
        client, fingerprint, action="retry", successful=True, reporter="plaintext-agent-id"
    )
    dump = json.dumps(rows("observations") + rows("recovery_outcomes"))
    assert "plaintext-agent-id" not in dump


# ---------------------------------------------------------------------------
# rate limiting
# ---------------------------------------------------------------------------


@pytest.fixture()
def tight_limit():
    """Shrink the write budget so a test does not need 121 requests."""
    original = write_limiter.limit
    write_limiter.limit = 3
    write_limiter.reset()
    yield 3
    write_limiter.limit = original
    write_limiter.reset()


def test_write_rate_limiter_rejects_excess_traffic(client, tight_limit):
    for _ in range(tight_limit):
        observe(client)

    response = client.post(
        "/v1/observe",
        json={
            "service": "github-mcp",
            "operation": "create_issue",
            "outcome": "failure",
            "error_type": "validation_error",
        },
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"]
    assert "rate limit" in response.json()["detail"].lower()


def test_outcome_endpoint_is_rate_limited_too(client, tight_limit):
    fingerprint = observe(client)["fingerprint"]
    for _ in range(tight_limit - 1):
        report_recovery(client, fingerprint, action="retry", successful=True)

    response = client.post(
        "/v1/outcome",
        json={"fingerprint": fingerprint, "action": "retry", "successful": True},
    )
    assert response.status_code == 429


def test_reads_are_never_rate_limited(client, tight_limit):
    for _ in range(tight_limit + 5):
        assert client.post("/v1/query", json={"service": "x", "operation": "y"}).status_code == 200
    assert client.get("/v1/services").status_code == 200
    assert client.get("/v1/stats").status_code == 200
    assert client.get("/health").status_code == 200


def test_mcp_writes_share_the_rest_budget(client, tight_limit):
    """Switching transport must not hand an agent a second budget."""
    for _ in range(tight_limit):
        observe(client)

    result = mcp_call(
        client,
        "report_tool_failure",
        {
            "service": "github-mcp",
            "operation": "create_issue",
            "error_type": "validation_error",
            "error_message": "Repository 918272 was not found",
        },
    )
    assert result["accepted"] is False
    assert result["error"] == "rate_limited"
    assert result["retry_after_seconds"] >= 1


def test_mcp_reads_are_not_rate_limited(client, tight_limit):
    for _ in range(tight_limit):
        observe(client)
    intel = mcp_call(
        client,
        "check_tool_failure",
        {"service": "github-mcp", "operation": "create_issue", "error_type": "x"},
    )
    assert "known" in intel


def test_limiter_window_is_per_client_key():
    from app.core.ratelimit import FixedWindowLimiter

    limiter = FixedWindowLimiter(limit=2, window_seconds=60, max_clients=10)
    assert limiter.check("a") == (True, 0)
    assert limiter.check("a") == (True, 0)
    allowed, retry_after = limiter.check("a")
    assert allowed is False and retry_after >= 1
    assert limiter.check("b")[0] is True, "one noisy client must not block others"


def test_limiter_memory_is_bounded():
    from app.core.ratelimit import FixedWindowLimiter

    limiter = FixedWindowLimiter(limit=1, window_seconds=60, max_clients=5)
    for index in range(50):
        limiter.check(f"ip-{index}")
    assert len(limiter._buckets) <= 5


def test_proxy_headers_are_ignored_unless_trusted():
    """Otherwise any client could forge its own rate-limit identity."""
    from app.core.ratelimit import client_key

    headers = {"x-forwarded-for": "1.2.3.4", "cf-connecting-ip": "5.6.7.8"}
    assert client_key("10.0.0.1", headers) == "10.0.0.1"

    object.__setattr__(settings, "trust_proxy_headers", True)
    try:
        assert client_key("10.0.0.1", headers) == "5.6.7.8"
    finally:
        object.__setattr__(settings, "trust_proxy_headers", False)
