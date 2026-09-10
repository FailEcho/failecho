"""POST /v1/observe."""

from __future__ import annotations

from tests.conftest import observe


def test_failure_is_fingerprinted_and_counted(client):
    first = observe(client)
    assert first["accepted"] is True
    assert first["known"] is False, "first sighting cannot already be known"
    assert first["observations"] == 1
    assert first["normalized_error"] == "Repository <N> was not found"

    second = observe(client, error_message="Repository 555812 was not found")
    assert second["fingerprint"] == first["fingerprint"]
    assert second["known"] is True
    assert second["observations"] == 2


def test_different_failures_get_different_fingerprints(client):
    a = observe(client)
    b = observe(client, error_message="Permission denied", error_code="403")
    assert a["fingerprint"] != b["fingerprint"]
    assert b["observations"] == 1


def test_success_needs_no_fingerprint(client):
    result = observe(
        client,
        outcome="success",
        error_type=None,
        error_code=None,
        error_message=None,
        latency_ms=318,
    )
    assert result["accepted"] is True
    assert result["fingerprint"] is None
    assert result["known"] is False
    assert result["observations"] == 1


def test_failure_without_any_error_signature_is_rejected(client):
    response = client.post(
        "/v1/observe",
        json={
            "service": "github-mcp",
            "operation": "create_issue",
            "outcome": "failure",
        },
    )
    assert response.status_code == 422


def test_minimal_payload_is_enough(client):
    """service + operation + outcome + one error hint. Friction kills adoption."""
    response = client.post(
        "/v1/observe",
        json={
            "service": "tiny-tool",
            "operation": "ping",
            "outcome": "failure",
            "error_type": "timeout",
        },
    )
    assert response.status_code == 200
    assert response.json()["fingerprint"]


def test_fingerprint_catalogue_tracks_first_and_last_seen(client, rows):
    observe(client)
    observe(client)
    catalogue = rows("fingerprints")
    assert len(catalogue) == 1
    assert catalogue[0]["observation_count"] == 2
    assert catalogue[0]["first_seen"] <= catalogue[0]["last_seen"]
