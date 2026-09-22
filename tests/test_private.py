"""Private mode: a team's evidence, and nobody else's.

The promise is small and absolute: a report sent with a team token is never
pooled, never counted, never aggregated and never answered back to anyone
else. So most of this file is the same question asked from every direction --
can a private row reach a public number, or another team, or a caller with no
token at all.

The design that makes those answers easy is a separate table. Every public
count reads `observations`; a private row is not in it, so it cannot leak by
being forgotten about in one aggregation path out of a dozen.
"""

from __future__ import annotations

import pytest

TEAM = "team-token-aaaaaaaaaaaaaaaaaaaaaaaa"
OTHER_TEAM = "team-token-bbbbbbbbbbbbbbbbbbbbbbbb"

FAILURE = {
    "service": "internal.api.example",
    "operation": "charge_card",
    "outcome": "failure",
    "error_type": "server_error",
    "error_code": "503",
}


def report(client, payload=None, token=TEAM, **headers):
    return client.post("/v1/observe", json=payload or FAILURE,
                       headers={**({"X-FailEcho-Team": token} if token else {}), **headers})


def ask(client, token=TEAM, payload=None):
    return client.post("/v1/query", json=payload or {"service": FAILURE["service"],
                                                     "operation": FAILURE["operation"],
                                                     "error_type": "server_error",
                                                     "error_code": "503"},
                       headers={"X-FailEcho-Team": token} if token else {})


# -- the write path ----------------------------------------------------------


def test_a_private_report_is_accepted_and_says_so(client):
    body = report(client).json()
    assert body["accepted"] is True
    assert body["private"] is True
    assert body["fingerprint"], "a team still needs the fingerprint to file outcomes"


def test_a_private_report_reaches_no_public_number(client):
    for _ in range(6):
        report(client)
    stats = client.get("/v1/stats").json()
    assert stats["observations_total"] == 0
    assert stats["real_observations_total"] == 0
    assert stats["sparse_observations"] == 0
    assert stats["first_party_observations"] == 0
    services = client.get("/v1/services").json()
    assert all(s["service"] != FAILURE["service"] for s in services)


def test_a_public_query_never_sees_private_evidence(client):
    for _ in range(6):
        report(client)
    answer = ask(client, token=None).json()
    assert answer["known"] is False
    assert answer["observations"]["total"] == 0
    assert answer["team_evidence"] is None


def test_another_team_sees_nothing(client):
    for _ in range(6):
        report(client)
    answer = ask(client, token=OTHER_TEAM).json()
    assert answer["team_evidence"] is None
    assert answer["observations"]["total"] == 0


def test_a_private_row_never_becomes_a_public_fingerprint(client, db_path):
    import sqlite3

    report(client)
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("select count(*) from observations").fetchone()[0] == 0
        assert connection.execute("select count(*) from fingerprints").fetchone()[0] == 0
        assert connection.execute("select count(*) from private_observations").fetchone()[0] == 1
    finally:
        connection.close()


def test_the_token_itself_is_never_stored(client, db_path):
    import sqlite3

    report(client)
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute("select team_hash from private_observations").fetchall()
    finally:
        connection.close()
    assert rows and TEAM not in rows[0][0]
    assert len(rows[0][0]) == 32


# -- the read path -----------------------------------------------------------


def test_a_team_gets_its_own_evidence_back(client):
    for _ in range(4):
        report(client)
    report(client, {**FAILURE, "outcome": "success"})

    evidence = ask(client).json()["team_evidence"]
    assert evidence["private"] is True
    assert evidence["observations"] == 5
    assert evidence["failures"] == 4
    assert evidence["recommendation"] is None, "failures alone never prove a fix"


def test_a_team_can_be_told_what_fixed_this_before(client):
    fingerprint = report(client).json()["fingerprint"]
    for _ in range(5):
        client.post("/v1/outcome",
                    json={"fingerprint": fingerprint, "action": "wait_and_retry", "successful": True},
                    headers={"X-FailEcho-Team": TEAM})

    evidence = ask(client).json()["team_evidence"]
    assert evidence["recommendation"]["action"] == "wait_and_retry"
    assert evidence["recommendation"]["scope"] == "team"
    assert evidence["recommendation"]["from_other_agents"] is False, (
        "a team's own history is not somebody else's experience"
    )


def test_a_teams_own_history_does_not_lower_the_bar(client):
    """Four successes is not five. Private mode is the same arithmetic on a
    smaller pool, not a looser one."""
    fingerprint = report(client).json()["fingerprint"]
    for _ in range(4):
        client.post("/v1/outcome",
                    json={"fingerprint": fingerprint, "action": "wait_and_retry", "successful": True},
                    headers={"X-FailEcho-Team": TEAM})
    assert ask(client).json()["team_evidence"]["recommendation"] is None


def test_team_outcomes_never_reach_the_public_recommendation(client):
    fingerprint = report(client).json()["fingerprint"]
    for _ in range(8):
        client.post("/v1/outcome",
                    json={"fingerprint": fingerprint, "action": "wait_and_retry", "successful": True},
                    headers={"X-FailEcho-Team": TEAM})

    public = ask(client, token=None).json()
    assert public["recommendation"] is None
    assert public["recovery_actions"] == []


def test_the_public_answer_is_unchanged_by_the_token(client):
    """A team gets the public answer *plus* its own, never a different one."""
    for _ in range(6):
        client.post("/v1/observe", json=FAILURE)     # public reports, no token
    fingerprint = client.post("/v1/query", json={"service": FAILURE["service"],
                                                 "operation": FAILURE["operation"],
                                                 "error_type": "server_error",
                                                 "error_code": "503"}).json()["fingerprint"]
    for _ in range(5):
        client.post("/v1/outcome", json={"fingerprint": fingerprint,
                                         "action": "backoff", "successful": True})

    public = ask(client, token=None).json()
    with_team = ask(client).json()
    assert public["recommendation"]["action"] == "backoff"
    assert with_team["recommendation"] == public["recommendation"]
    assert with_team["observations"]["total"] == public["observations"]["total"]


def test_a_query_with_a_token_still_stores_nothing(client, db_path):
    import sqlite3

    ask(client)
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("select count(*) from private_observations").fetchone()[0] == 0
        assert connection.execute("select count(*) from observations").fetchone()[0] == 0
    finally:
        connection.close()


# -- the token ---------------------------------------------------------------


def test_a_guessable_token_is_refused(client):
    """Accepting a four-character token would give a team the feeling of
    privacy without any of it."""
    response = report(client, token="team1")
    assert response.status_code == 400
    assert "at least" in response.json()["detail"]


def test_an_empty_token_is_just_the_public_network(client):
    response = client.post("/v1/observe", json=FAILURE, headers={"X-FailEcho-Team": "   "})
    assert response.status_code == 200
    assert response.json()["private"] is False
    assert client.get("/v1/stats").json()["observations_total"] == 1


def test_private_mode_can_be_closed_on_an_instance(client, monkeypatch):
    """Settings are frozen at import, so this patches the dependency's view of
    them -- the point being that an operator can turn private mode off without
    the rest of the service noticing."""
    import app.api.deps as deps

    class Closed:
        def __getattr__(self, name):
            from app.core.config import settings
            return False if name == "private_mode_open" else getattr(settings, name)

    monkeypatch.setattr(deps, "settings", Closed())
    assert report(client).status_code == 403


# -- retention ---------------------------------------------------------------


def test_private_rows_outlive_the_public_window_and_are_never_aggregated(client, db_path):
    """48 hours would make private mode pointless -- a team's own history is
    the entire product. They are deleted on their own clock and folded into
    nothing, because there is nothing shared to fold them into."""
    import asyncio
    import sqlite3
    from datetime import timedelta

    from app.core.clock import utcnow
    from app.core.retention import aggregate_and_prune
    from app.db.database import SessionLocal

    report(client)
    old = (utcnow() - timedelta(days=90)).isoformat(sep=" ")
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("update private_observations set created_at = ?", (old,))
        connection.commit()
    finally:
        connection.close()

    async def prune():
        async with SessionLocal() as session:
            return await aggregate_and_prune(session)

    result = asyncio.run(prune())
    assert result.private_observations_deleted == 1
    assert result.observations_aggregated == 0

    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("select count(*) from hourly_stats").fetchone()[0] == 0
    finally:
        connection.close()


# -- the shipped client ------------------------------------------------------


def test_the_wrapper_can_be_put_in_private_mode(monkeypatch):
    from failecho_autoreport import FailEcho

    monkeypatch.setenv("FAILECHO_TEAM", "team-token-cccccccccccccccccccc")
    fe = FailEcho(endpoint="http://127.0.0.1:9")
    assert fe._headers("/v1/observe", b"{}")["X-FailEcho-Team"].startswith("team-token-")

    monkeypatch.delenv("FAILECHO_TEAM")
    assert "X-FailEcho-Team" not in FailEcho(endpoint="http://127.0.0.1:9")._headers("/v1/observe", b"{}")


def test_private_mode_is_not_offered_over_mcp_tool_arguments():
    """A team token is a secret. Tool arguments are visible to the model and
    to whatever logs the conversation, so private mode is a header on the REST
    path and the wrapper, and the MCP tools do not take one."""
    from app.mcp_server import mcp_server

    source = __import__("pathlib").Path("app/mcp_server.py").read_text()
    assert "team_token" not in source
    assert mcp_server.name == "failecho"
