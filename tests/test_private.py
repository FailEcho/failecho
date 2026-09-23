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


# -- the shipped wrapper, against a real server ----------------------------------


def test_the_wrapper_runs_the_whole_private_loop_against_a_real_server():
    """Not the pieces, the loop: a team's agent fails, recovers, reports it,
    five times -- and then asks, and gets its own fix back in the one line an
    agent reads, labelled as its own. The public network sees none of it."""
    import json as _json
    import urllib.request

    from failecho_autoreport import FailEcho
    from tests.live_server import running_failecho

    token = "wrapper-team-token-ffffffffffff"
    with running_failecho(FIN_RATE_LIMIT_WRITES_PER_MINUTE="10000") as live:
        fe = FailEcho(endpoint=live.base, team_token=token, report_success=False)
        for _ in range(5):
            fe.record_failure("billing.internal", "charge", RuntimeError("503 service unavailable"), 40)
            assert fe.flush(timeout=10)
            fe.recovered("billing.internal", "charge", "wait_and_retry", True,
                         error_type="server_error", error_code="503")
            assert fe.flush(timeout=10)

        line = FailEcho.advice_text(fe.check("billing.internal", "charge", "server_error", "503"))
        assert line == "FailEcho (your team's own history): try wait_and_retry, worked 5/5."

        stranger = FailEcho(endpoint=live.base, report_success=False)
        assert FailEcho.advice_text(stranger.check("billing.internal", "charge", "server_error", "503")) is None
        with urllib.request.urlopen(f"{live.base}/v1/stats", timeout=5) as r:
            assert _json.load(r)["observations_total"] == 0, "a private report reached a public number"


def test_the_pages_do_not_promise_private_mode_where_it_does_not_work(client):
    """On 23 Sep llms.txt said FAILECHO_TEAM worked with the plugin and the
    proxy when neither read it: a team following the docs would have reported
    to the public network believing otherwise. Every page that offers private
    mode says which integrations it works with, and which not yet."""
    for path in ("/llms.txt", "/setup"):
        flat = " ".join(client.get(path).text.split())
        # 23 Sep: 0.1.7 and 0.2.3 are published; older versions still ignore the token
        assert "0.1.7 and newer" in flat and "0.2.3 and newer" in flat, path
        assert "report publicly" in flat or "report to the public network" in flat, path
        assert "not in the published package" not in flat and "not yet in its published package" not in flat, path
        assert "failecho-autoreport, the plugin, the proxy" not in flat, path


def test_every_integration_that_is_documented_actually_reads_the_token():
    """The variable is named in each of these; the behaviour behind it is
    tested in the integration's own test file."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for path in ("plugin/hooks/failecho_hook.py", "opencode-plugin/plugin/failecho.js",
                 "npm-relay/bin/proxy.js", "failecho_autoreport/__init__.py"):
        assert "FAILECHO_TEAM" in (root / path).read_text(), path


def test_llms_txt_itself_documents_private_mode_and_signing(client):
    """Both sections were written on 22 Sep into the API description by
    mistake -- the anchor they were inserted before exists in both strings --
    so agents reading llms.txt were never told either existed."""
    body = client.get("/llms.txt").text
    assert "## Private mode: your team's evidence, shared with nobody" in body
    assert "## Proving who is reporting" in body
    assert "X-FailEcho-Team" in body and "X-Reporter-Signature" in body


# -- skip: the verdict a small team needs most (2026-09-23) -----------------------


def _fail_and_try(client, token, times, action="backoff", successful=False, reporter="agent-1"):
    fingerprint = report(client, token=token, **{"X-Reporter-ID": reporter}).json()["fingerprint"]
    for _ in range(times):
        client.post("/v1/outcome", json={"fingerprint": fingerprint, "action": action, "successful": successful},
                    headers={"X-FailEcho-Team": token, "X-Reporter-ID": reporter})
    return fingerprint


def test_a_team_is_told_to_stop_when_nothing_it_tried_has_worked(client):
    """The public network's most useful verdict had no private equivalent: a
    team that kept hitting an exhausted quota was never told to stop."""
    _fail_and_try(client, TEAM, 5)
    rec = ask(client).json()["team_evidence"]["recommendation"]
    assert rec["action"] == "skip" and rec["scope"] == "team" and rec["from_other_agents"] is False
    assert rec["based_on_attempts"] == 5


def test_four_failures_are_not_yet_a_verdict(client):
    _fail_and_try(client, TEAM, 4)
    assert ask(client).json()["team_evidence"]["recommendation"] is None


def test_one_success_in_the_window_means_it_is_not_hopeless(client):
    fingerprint = _fail_and_try(client, TEAM, 6)
    client.post("/v1/outcome", json={"fingerprint": fingerprint, "action": "wait_until_reset", "successful": True},
                headers={"X-FailEcho-Team": TEAM, "X-Reporter-ID": "agent-2"})
    assert ask(client).json()["team_evidence"]["recommendation"] is None


def test_one_agents_retry_storm_counts_what_the_public_network_counts(client):
    """At most five attempts per agent per hour, like the public cap: twenty
    failed retries in one burst are a verdict worth five, not twenty."""
    _fail_and_try(client, TEAM, 20)
    rec = ask(client).json()["team_evidence"]["recommendation"]
    assert rec["action"] == "skip" and rec["based_on_attempts"] == 5
    listed = ask(client).json()["team_evidence"]["recovery_actions"][0]
    assert listed["attempts"] == 20, "raw counts are still reported as they are"


def test_old_failures_do_not_make_a_skip(client, db_path):
    import sqlite3
    from datetime import timedelta

    from app.core.clock import utcnow

    _fail_and_try(client, TEAM, 6)
    old = (utcnow() - timedelta(days=3)).isoformat(sep=" ")
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("update private_recovery_outcomes set created_at = ?", (old,))
        connection.commit()
    finally:
        connection.close()
    assert ask(client).json()["team_evidence"]["recommendation"] is None, "a fix may have landed since"


def test_a_fix_that_works_beats_a_skip(client):
    fingerprint = _fail_and_try(client, TEAM, 5, action="backoff")
    for _ in range(5):
        client.post("/v1/outcome", json={"fingerprint": fingerprint, "action": "wait_until_reset", "successful": True},
                    headers={"X-FailEcho-Team": TEAM, "X-Reporter-ID": "agent-2"})
    assert ask(client).json()["team_evidence"]["recommendation"]["action"] == "wait_until_reset"


def test_every_integration_says_a_team_skip_as_an_instruction():
    """"try skip, worked 0/5" reads as nonsense; the public skip is said as an
    instruction, and so is the team's."""
    import shutil
    import subprocess
    from pathlib import Path

    from failecho_autoreport import FailEcho

    answer = {"recommendation": None, "recovery_actions": [],
              "team_evidence": {"private": True, "recommendation": {"action": "skip", "scope": "team",
                                                                    "based_on_attempts": 5},
                                "recovery_actions": [{"action": "backoff", "attempts": 5, "successes": 0}]}}
    python_line = FailEcho.advice_text(answer)
    assert python_line == "FailEcho (your team's own history): skip -- nothing your agents tried recently has fixed this failure."
    root = Path(__file__).resolve().parents[1]
    if shutil.which("node"):
        node = subprocess.run(["node", "-e", "const p=require(process.argv[1]);"
                               "process.stdout.write(p.adviceText(JSON.parse(process.argv[2]))||'')",
                               str(root / "npm-relay" / "bin" / "proxy.js"), __import__("json").dumps(answer)],
                              capture_output=True, text=True, timeout=30)
        assert node.stdout == python_line
    import importlib.util
    spec = importlib.util.spec_from_file_location("hook", root / "plugin" / "hooks" / "failecho_hook.py")
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    note = hook.context_note("svc", "op", {"known": False, **answer})
    assert "Do not retry this call" in note and "5 attempts" in note


# -- a team's control over its own data (2026-09-23) ------------------------------

NEW_TEAM = "team-token-zzzzzzzzzzzzzzzzzzzzzzzz"


def test_a_team_can_delete_everything_it_stored(client):
    fingerprint = report(client).json()["fingerprint"]
    report(client)
    client.post("/v1/outcome", json={"fingerprint": fingerprint, "action": "retry", "successful": True},
                headers={"X-FailEcho-Team": TEAM})
    report(client, token=OTHER_TEAM)                       # somebody else's, must survive

    gone = client.delete("/v1/team", headers={"X-FailEcho-Team": TEAM})
    assert gone.status_code == 200
    assert gone.json()["deleted_observations"] == 2 and gone.json()["deleted_outcomes"] == 1
    assert ask(client).json()["team_evidence"] is None
    assert ask(client, token=OTHER_TEAM).json()["team_evidence"]["observations"] == 1, "only this team's rows"
    again = client.delete("/v1/team", headers={"X-FailEcho-Team": TEAM}).json()
    assert again["deleted_observations"] == 0, "idempotent"


def test_delete_needs_a_token_and_never_touches_public_data(client):
    client.post("/v1/observe", json=FAILURE)                # public
    assert client.delete("/v1/team").status_code == 400
    client.delete("/v1/team", headers={"X-FailEcho-Team": TEAM})
    assert client.get("/v1/stats").json()["observations_total"] == 1


def test_a_leaked_token_can_be_replaced_without_losing_history(client):
    fingerprint = report(client).json()["fingerprint"]
    for _ in range(5):
        client.post("/v1/outcome", json={"fingerprint": fingerprint, "action": "wait_and_retry", "successful": True},
                    headers={"X-FailEcho-Team": TEAM})
    moved = client.post("/v1/team/rotate", json={"new_token": NEW_TEAM}, headers={"X-FailEcho-Team": TEAM})
    assert moved.status_code == 200 and moved.json() == {
        "deleted_observations": 0, "deleted_outcomes": 0, "moved_observations": 1, "moved_outcomes": 5}
    assert ask(client).json()["team_evidence"] is None, "the old token reads nothing now"
    assert ask(client, token=NEW_TEAM).json()["team_evidence"]["recommendation"]["action"] == "wait_and_retry"


def test_rotation_never_merges_two_teams(client):
    report(client)
    report(client, token=OTHER_TEAM)
    refused = client.post("/v1/team/rotate", json={"new_token": OTHER_TEAM}, headers={"X-FailEcho-Team": TEAM})
    assert refused.status_code == 409
    assert ask(client, token=OTHER_TEAM).json()["team_evidence"]["observations"] == 1


def test_rotation_refuses_a_weak_or_unchanged_token(client):
    report(client)
    assert client.post("/v1/team/rotate", json={"new_token": "short"},
                       headers={"X-FailEcho-Team": TEAM}).status_code == 422
    assert client.post("/v1/team/rotate", json={"new_token": TEAM},
                       headers={"X-FailEcho-Team": TEAM}).status_code == 400
    assert client.post("/v1/team/rotate", json={"new_token": NEW_TEAM}).status_code == 400
