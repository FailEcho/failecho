"""GET /health, /v1/services, /v1/stats and the homepage."""

from __future__ import annotations

from tests.conftest import observe


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_services_is_empty_before_any_telemetry(client):
    assert client.get("/v1/services").json() == []


def test_services_reports_status_and_rates(client):
    for _ in range(2):
        observe(client)
    for _ in range(8):
        observe(client, outcome="success", error_type=None, error_code=None,
                error_message=None)

    rows = client.get("/v1/services").json()
    assert len(rows) == 1
    row = rows[0]
    # reported as "github-mcp", recorded under the canonical name
    assert row["service"] == "github"
    assert row["operation"] == "create_issue"
    assert row["status"] == "DEGRADED"
    assert row["failure_rate_1h"] == 0.2
    assert row["observations_1h"] == 10


def test_services_sorts_worst_first_and_filters(client):
    for _ in range(15):
        observe(client)  # github-mcp -> MAJOR
    for _ in range(20):
        observe(client, service="calm-api", outcome="success", error_type=None,
                error_code=None, error_message=None)

    rows = client.get("/v1/services").json()
    assert [r["service"] for r in rows] == ["github", "calm-api"]

    filtered = client.get("/v1/services", params={"service": "calm-api"}).json()
    assert len(filtered) == 1
    assert filtered[0]["status"] == "HEALTHY"


def test_stats_counts_the_network(client):
    fingerprint = observe(client)["fingerprint"]
    observe(client, outcome="success", error_type=None, error_code=None,
            error_message=None)
    client.post(
        "/v1/outcome",
        json={"fingerprint": fingerprint, "action": "retry", "successful": True},
    )

    stats = client.get("/v1/stats").json()
    assert stats["observations_total"] == 2
    assert stats["observations_1h"] == 2
    assert stats["failures_1h"] == 1
    assert stats["active_failures"] == 1
    assert stats["fingerprints_total"] == 1
    assert stats["recovery_outcomes_total"] == 1
    assert stats["services_tracked"] == 1
    assert stats["demo_data"] is False
    assert stats["synthetic_observations"] == 0
    assert stats["generated_at"].endswith("Z")


def test_homepage_and_docs_are_served(client):
    home = client.get("/")
    assert home.status_code == 200
    assert "AI agents shouldn't debug" in home.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/docs").status_code == 200

    schema = client.get("/openapi.json").json()
    assert set(schema["paths"]) >= {
        "/v1/observe",
        "/v1/query",
        "/v1/outcome",
        "/v1/services",
        "/health",
    }
    # The OpenAPI schema is meant to be read by agents, so fields must be described.
    observe_schema = schema["components"]["schemas"]["ObserveRequest"]["properties"]
    assert all("description" in prop for prop in observe_schema.values())


def test_outcome_validates_fingerprint_and_action(client):
    bad_fingerprint = client.post(
        "/v1/outcome",
        json={"fingerprint": "not-a-fingerprint", "action": "retry", "successful": True},
    )
    assert bad_fingerprint.status_code == 422

    bad_action = client.post(
        "/v1/outcome",
        json={"fingerprint": "a" * 32, "action": "drop tables;", "successful": True},
    )
    assert bad_action.status_code == 422

    ok = client.post(
        "/v1/outcome",
        json={"fingerprint": "a" * 32, "action": "Refresh Schema", "successful": True},
    )
    assert ok.status_code == 200
    assert ok.json() == {"accepted": True}
