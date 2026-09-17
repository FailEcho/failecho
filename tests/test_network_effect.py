"""The product test: one agent's failure teaches the next agent.

This is the whole thesis of the network, so it is tested end to end over the
MCP transport -- the same path the demo in ``examples/live_agent`` uses.

    Reporters A, C, D, E, F hit the same failure with different dynamic ids
        -> normalization collapses them onto one fingerprint
        -> each reports that retry failed and refresh_schema worked
    Reporter B hits a normalized-equivalent error it has never seen
        -> gets refresh_schema, with confidence derived from stored outcomes
"""

from __future__ import annotations

from tests.conftest import mcp_call

SERVICE = "demo-issues-api"
OPERATION = "create_issue"
VERSION = "2.0.0"
SCHEMA_HASH = "v2body"

#: Same logical failure, textually different every time -- as in real life.
def failure_message(repository: str) -> str:
    return (
        f"Repository {repository} rejected field body: "
        'field "body" is no longer accepted, use "content"'
    )


def report_failure(client, repository: str, reporter_id: str) -> dict:
    return mcp_call(
        client,
        "report_tool_failure",
        {
            "service": SERVICE,
            "operation": OPERATION,
            "version": VERSION,
            "schema_hash": SCHEMA_HASH,
            "error_type": "validation_error",
            "error_code": "422",
            "error_message": failure_message(repository),
            "reporter_id": reporter_id,
            "latency_ms": 120,
        },
    )


def check(client, repository: str) -> dict:
    return mcp_call(
        client,
        "check_tool_failure",
        {
            "service": SERVICE,
            "operation": OPERATION,
            "version": VERSION,
            "schema_hash": SCHEMA_HASH,
            "error_type": "validation_error",
            "error_code": "422",
            "error_message": failure_message(repository),
        },
    )


def report_outcome(client, fingerprint, action, successful, reporter_id) -> dict:
    return mcp_call(
        client,
        "report_recovery_outcome",
        {
            "fingerprint": fingerprint,
            "action": action,
            "successful": successful,
            "reporter_id": reporter_id,
        },
    )


def explore(client, repository: str, reporter_id: str) -> str:
    """One agent with no evidence: retry fails, refresh_schema works."""
    fingerprint = report_failure(client, repository, reporter_id)["fingerprint"]
    report_outcome(client, fingerprint, "retry", False, reporter_id)
    report_failure(client, repository, reporter_id)  # the retry failed too
    report_outcome(client, fingerprint, "refresh_schema", True, reporter_id)
    mcp_call(
        client,
        "report_tool_success",
        {
            "service": SERVICE,
            "operation": OPERATION,
            "version": "3.0.0",
            "schema_hash": "v3cont",
            "reporter_id": reporter_id,
        },
    )
    return fingerprint


EXPLORERS = [
    ("123456", "agent-a"),
    ("234567", "agent-c"),
    ("345678", "agent-d"),
    ("456789", "agent-e"),
    ("567890", "agent-f"),
]


def test_agent_b_learns_from_agents_it_never_met(client):
    """The end-to-end network effect, over MCP."""
    fingerprints = {explore(client, repo, agent) for repo, agent in EXPLORERS}
    assert len(fingerprints) == 1, "dynamic ids must normalize to one fingerprint"
    shared = fingerprints.pop()

    # Agent B has never reported anything and has never met agent A.
    intel = check(client, "987654")

    assert intel["fingerprint"] == shared, "same logical failure, same key"
    assert intel["known"] is True
    assert intel["observations"]["unique_reporters"] == 5
    assert intel["normalized_error"] == (
        'Repository <N> rejected field body: field "body" is no longer '
        'accepted, use "content"'
    )

    recommendation = intel["recommendation"]
    assert recommendation is not None, "five independent reporters is enough"
    assert recommendation["action"] == "refresh_schema"
    assert 0 < recommendation["confidence"] < 1

    # The recommendation is derived from stored outcomes, not invented.
    actions = {a["action"]: a for a in intel["recovery_actions"]}
    assert actions["refresh_schema"]["attempts"] == 5
    assert actions["refresh_schema"]["successes"] == 5
    assert actions["retry"]["attempts"] == 5
    assert actions["retry"]["successes"] == 0
    assert recommendation["based_on_attempts"] == 5
    assert recommendation["based_on_successes"] == 5
    assert recommendation["unique_reporters"] == 5

    # ...and the useless action is visible as useless, not merely absent.
    assert actions["retry"]["success_rate"] == 0.0
    assert actions["retry"]["confidence"] == 0.0


def test_recommendation_appears_only_once_evidence_crosses_the_threshold(client):
    for index, (repository, reporter) in enumerate(EXPLORERS, start=1):
        explore(client, repository, reporter)
        intel = check(client, "987654")
        if index < 5:
            assert intel["recommendation"] is None, (
                f"{index} attempts must not be enough"
            )
            assert intel["observations"]["unique_reporters"] == index
        else:
            assert intel["recommendation"]["action"] == "refresh_schema"


def test_one_reporter_cannot_manufacture_the_recommendation(client):
    """A single agent shouting 50 times is worth 5 attempts, not 50."""
    fingerprint = report_failure(client, "123456", "loud-agent")["fingerprint"]
    for _ in range(50):
        report_outcome(client, fingerprint, "use_fallback", True, "loud-agent")

    intel = check(client, "987654")
    action = next(
        a for a in intel["recovery_actions"] if a["action"] == "use_fallback"
    )
    assert action["attempts"] == 50, "raw counts stay honest"
    assert action["effective_attempts"] == 5, "influence is capped"
    assert action["unique_reporters"] == 1

    solo_confidence = intel["recommendation"]["confidence"]

    # Five independent reporters with the same raw volume outrank it.
    for repository, reporter in EXPLORERS:
        explore(client, repository, reporter)
    crowd = check(client, "987654")
    assert crowd["recommendation"]["action"] == "refresh_schema"
    assert crowd["recommendation"]["confidence"] > solo_confidence


def test_independent_reporters_are_counted_separately(client):
    for repository, reporter in EXPLORERS[:3]:
        explore(client, repository, reporter)
    assert check(client, "987654")["observations"]["unique_reporters"] == 3

    # The same agent reporting again does not add a reporter.
    explore(client, "111111", "agent-a")
    assert check(client, "987654")["observations"]["unique_reporters"] == 3


def test_demo_agents_never_count_as_real_adoption(client):
    """The demo must not be able to look like traction."""
    for repository, reporter in EXPLORERS:
        explore(client, repository, reporter)

    stats = client.get("/v1/stats").json()
    assert stats["observations_total"] > 0
    assert stats["demo_agent_observations"] == 0, "TestClient sends no demo header"
    # Every row is `agent`; single-service explorers inside a minute are
    # below the adoption threshold, so they are held as sparse, not lost.
    assert stats["real_observations_total"] + stats["sparse_observations"] == stats["observations_total"]

    # Now the same traffic, self-labelled as a demo agent.
    demo = client.post(
        "/v1/observe",
        json={
            "service": SERVICE,
            "operation": OPERATION,
            "version": VERSION,
            "schema_hash": SCHEMA_HASH,
            "outcome": "failure",
            "error_type": "validation_error",
            "error_code": "422",
            "error_message": failure_message("999999"),
        },
        headers={"X-Reporter-ID": "demo-agent-a", "X-Reporter-Kind": "demo"},
    )
    assert demo.status_code == 200

    after = client.get("/v1/stats").json()
    assert after["demo_agent_observations"] == 1
    assert after["real_observations_total"] == stats["real_observations_total"]
    assert after["demo_data"] is True
    assert check(client, "987654")["demo_data_included"] is True
