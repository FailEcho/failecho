"""The experiment counters: is the network actually helping anyone?

These four numbers decide whether FailEcho works as a product:

    known_hit_rate_24h        was asking worth the round trip?
    recovery_outcome_ratio    do agents say whether the fix worked?
    real_successes_24h        do we have denominators?
    cross_agent_help_24h      did an agent use evidence it did not generate?
"""

from __future__ import annotations

from tests.conftest import mcp_call, observe, query

FAILURE = {
    "service": "github-mcp",
    "operation": "create_issue",
    "version": "2.8.1",
    "schema_hash": "a817ce",
    "error_type": "validation_error",
    "error_code": "422",
}


def ask(client, message: str, reporter: str | None = None) -> dict:
    headers = {"X-Reporter-ID": reporter} if reporter else None
    response = client.post(
        "/v1/query", json={**FAILURE, "error_message": message}, headers=headers
    )
    assert response.status_code == 200
    return response.json()


def test_known_and_unknown_queries_are_counted(client):
    ask(client, "Repository 111111 was not found")  # nothing known yet
    assert client.get("/v1/stats").json()["unknown_query_hits_24h"] == 1

    observe(client)
    ask(client, "Repository 222222 was not found")

    stats = client.get("/v1/stats").json()
    assert stats["known_query_hits_24h"] == 1
    assert stats["unknown_query_hits_24h"] == 1
    assert stats["known_hit_rate_24h"] == 0.5


def test_hit_rate_is_null_before_any_query(client):
    stats = client.get("/v1/stats").json()
    assert stats["known_hit_rate_24h"] is None
    assert stats["known_query_hits_24h"] == 0


def test_successes_and_failures_are_counted_separately(client):
    """From a reporter past the adoption threshold; a thinner one's rows are
    held under sparse_observations (tests/test_adoption_threshold.py)."""
    from app.core.privacy import hash_reporter_id
    from tests.conftest import insert_observation

    for i, service in enumerate(("api.github.com", "pypi.org")):
        insert_observation(service=service, operation="op", reporter_hash=hash_reporter_id("agent-x"),
                           minutes_ago=30 + i * 15, fingerprint=None, outcome="success")
    headers = {"X-Reporter-ID": "agent-x"}
    for _ in range(3):
        observe(client, headers=headers)
    for _ in range(7):
        observe(client, outcome="success", error_type=None, error_code=None,
                error_message=None, headers=headers)

    stats = client.get("/v1/stats").json()
    assert stats["real_observations_24h"] == 12
    stats["real_successes_24h"] -= 2
    stats["real_observations_24h"] -= 2
    assert stats["real_failures_24h"] == 3
    assert stats["real_successes_24h"] == 7
    assert stats["real_observations_24h"] == 10


def test_recovery_outcome_ratio_measures_loop_closure(client):
    fingerprint = observe(client)["fingerprint"]
    assert client.get("/v1/stats").json()["recovery_outcome_ratio_24h"] == 0.0

    client.post(
        "/v1/outcome",
        json={"fingerprint": fingerprint, "action": "refresh_schema", "successful": True},
    )
    assert client.get("/v1/stats").json()["recovery_outcome_ratio_24h"] == 1.0


def test_recovery_ratio_is_null_with_no_failures(client):
    assert client.get("/v1/stats").json()["recovery_outcome_ratio_24h"] is None


def build_evidence(client, reporters=("agent-a", "agent-c", "agent-d", "agent-e", "agent-f")):
    """Independent reporters agreeing that refresh_schema works."""
    fingerprint = None
    for reporter in reporters:
        fingerprint = observe(client, headers={"X-Reporter-ID": reporter})["fingerprint"]
        client.post(
            "/v1/outcome",
            json={
                "fingerprint": fingerprint,
                "action": "refresh_schema",
                "successful": True,
            },
            headers={"X-Reporter-ID": reporter},
        )
    return fingerprint


def test_cross_agent_help_counts_evidence_from_others(client):
    build_evidence(client)
    assert client.get("/v1/stats").json()["cross_agent_help_24h"] == 0

    # Agent B has contributed nothing and gets a usable recommendation.
    intel = ask(client, "Repository 987654 was not found", reporter="agent-b")
    assert intel["recommendation"]["action"] == "refresh_schema"
    assert client.get("/v1/stats").json()["cross_agent_help_24h"] == 1


def test_cross_agent_help_ignores_anonymous_callers(client):
    build_evidence(client)
    intel = ask(client, "Repository 987654 was not found")  # no reporter id
    assert intel["recommendation"] is not None
    assert client.get("/v1/stats").json()["cross_agent_help_24h"] == 0


def test_cross_agent_help_ignores_self_generated_evidence(client):
    """One agent talking to itself is not the network effect."""
    fingerprint = None
    for _ in range(6):
        fingerprint = observe(client, headers={"X-Reporter-ID": "lonely"})["fingerprint"]
        client.post(
            "/v1/outcome",
            json={"fingerprint": fingerprint, "action": "retry", "successful": True},
            headers={"X-Reporter-ID": "lonely"},
        )

    intel = ask(client, "Repository 987654 was not found", reporter="lonely")
    assert intel["recommendation"] is not None, "it still gets its own evidence back"
    assert client.get("/v1/stats").json()["cross_agent_help_24h"] == 0


def test_cross_agent_help_needs_an_actual_recommendation(client):
    """Thin evidence: no recommendation, so nobody was helped."""
    for reporter in ("agent-a", "agent-c"):
        fingerprint = observe(client, headers={"X-Reporter-ID": reporter})["fingerprint"]
        client.post(
            "/v1/outcome",
            json={"fingerprint": fingerprint, "action": "reconnect", "successful": True},
            headers={"X-Reporter-ID": reporter},
        )

    intel = ask(client, "Repository 987654 was not found", reporter="agent-b")
    assert intel["recommendation"] is None
    assert client.get("/v1/stats").json()["cross_agent_help_24h"] == 0


def test_demo_traffic_never_moves_the_experiment_counters(client):
    build_evidence(client)
    client.post(
        "/v1/query",
        json={**FAILURE, "error_message": "Repository 987654 was not found"},
        headers={"X-Reporter-ID": "demo-agent", "X-Reporter-Kind": "demo"},
    )
    stats = client.get("/v1/stats").json()
    assert stats["known_query_hits_24h"] == 0
    assert stats["cross_agent_help_24h"] == 0


def test_counters_hold_integers_only(client, rows):
    """Nothing identifying may end up in the counter table."""
    build_evidence(client)
    ask(client, "Repository 987654 was not found", reporter="agent-b")

    counters = rows("daily_counters")
    assert counters
    for row in counters:
        assert set(row) == {"day", "name", "value"}
        assert isinstance(row["value"], int)
        assert row["name"] in {"query_known", "query_unknown", "cross_agent_help"}


def test_query_still_stores_no_telemetry(client, rows):
    """Counters are aggregates; the query itself remains unrecorded."""
    build_evidence(client)
    before = len(rows("observations"))
    ask(client, "Repository 987654 was not found", reporter="agent-b")
    assert len(rows("observations")) == before
    assert not any(row["reporter_hash"] == "agent-b" for row in rows("observations"))


def test_mcp_query_counts_the_same_way(client):
    build_evidence(client)
    intel = mcp_call(
        client,
        "check_tool_failure",
        {**FAILURE, "error_message": "Repository 987654 was not found",
         "reporter_id": "agent-b"},
    )
    assert intel["recommendation"]["action"] == "refresh_schema"
    stats = client.get("/v1/stats").json()
    assert stats["known_query_hits_24h"] == 1
    assert stats["cross_agent_help_24h"] == 1


def test_counter_increments_are_atomic(client):
    """Regression: read-then-write raced, and an agent's query 500'd.

    Two concurrent queries both saw "no counter row yet", both INSERTed, and
    one died on the unique constraint -- turning a metric into an outage on
    the endpoint an agent calls while it is already handling a failure.
    """
    import concurrent.futures

    observe(client)  # something for the query to find

    def ask(index: int):
        return client.post(
            "/v1/query",
            json={**FAILURE, "error_message": f"Repository {700000 + index} was not found"},
            headers={"X-Reporter-ID": f"agent-{index}"},
        ).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        codes = list(pool.map(ask, range(48)))

    assert set(codes) == {200}, f"non-200 responses: {sorted(set(codes))}"

    stats = client.get("/v1/stats").json()
    counted = stats["known_query_hits_24h"] + stats["unknown_query_hits_24h"]
    assert counted == 48, f"counted {counted} of 48 queries"


def test_a_broken_counter_never_breaks_the_answer(client, monkeypatch):
    """Telemetry about the network must never take the network down."""
    import app.core.service as service

    async def explode(*args, **kwargs):
        raise RuntimeError("counter backend on fire")

    monkeypatch.setattr(service, "bump_counter", explode)

    observe(client)
    response = client.post(
        "/v1/query",
        json={**FAILURE, "error_message": "Repository 918272 was not found"},
        headers={"X-Reporter-ID": "agent-b"},
    )
    assert response.status_code == 200
    assert response.json()["known"] is True
