"""First-party telemetry: FailEcho's own agents, labelled and never adoption.

The network bootstraps on real calls from its operator's agents. That data is
genuine field evidence, so agents may use it -- but it is not independent, it
is not adoption, and the label must be impossible to forge.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.core.config import OPERATOR_HEADER, settings
from tests.conftest import insert_observation, observe, run_prune

TOKEN = "operator-test-token"
QUERY = {
    "service": "github-mcp",
    "operation": "create_issue",
    "version": "2.8.1",
    "schema_hash": "a817ce",
    "error_type": "validation_error",
    "error_code": "422",
    "error_message": "Repository 555812 was not found",
}
MCP_HEADERS = {"accept": "application/json, text/event-stream"}


@pytest.fixture()
def operator_token():
    """Configure the operator secret for one test, then restore it."""
    original = settings.first_party_token
    object.__setattr__(settings, "first_party_token", TOKEN)
    yield TOKEN
    object.__setattr__(settings, "first_party_token", original)


def operator(token: str = TOKEN) -> dict:
    return {OPERATOR_HEADER: token}


def ask(client, headers: dict | None = None) -> dict:
    response = client.post("/v1/query", json=QUERY, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def sources(rows, table: str = "observations") -> list[str]:
    return [row["source"] for row in rows(table)]


# ---------------------------------------------------------------------------
# the label, and why it cannot be forged
# ---------------------------------------------------------------------------


def test_the_operator_secret_labels_a_report_first_party(client, rows, operator_token):
    observe(client, headers=operator())
    assert sources(rows) == ["first_party"]


def test_a_wrong_secret_is_stored_as_demo_never_as_operator_evidence(
    client, rows, operator_token
):
    observe(client, headers=operator("a-guess"))
    observe(client, headers=operator(""))
    assert sources(rows) == ["demo_agent", "demo_agent"]


def test_with_no_secret_configured_every_claim_is_demo(client, rows):
    assert settings.first_party_token == ""
    observe(client, headers=operator(TOKEN))
    assert sources(rows) == ["demo_agent"]


def test_the_label_works_over_mcp(client, rows, operator_token):
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "report_tool_failure",
                "arguments": {
                    "service": "github-mcp",
                    "operation": "create_issue",
                    "error_type": "validation_error",
                },
            },
        },
        headers={**MCP_HEADERS, **operator()},
    )
    assert response.status_code == 200, response.text
    assert sources(rows) == ["first_party"]


def test_recovery_outcomes_carry_the_label(client, rows, operator_token):
    fingerprint = observe(client, headers=operator())["fingerprint"]
    response = client.post(
        "/v1/outcome",
        json={"fingerprint": fingerprint, "action": "refresh_schema", "successful": True},
        headers=operator(),
    )
    assert response.status_code == 200, response.text
    assert sources(rows, "recovery_outcomes") == ["first_party"]


# ---------------------------------------------------------------------------
# never adoption
# ---------------------------------------------------------------------------


def test_first_party_never_counts_as_adoption(client, operator_token):
    for _ in range(3):
        observe(client, headers={**operator(), "X-Reporter-ID": "operator-agent-1"})
    ask(client, headers=operator())

    stats = client.get("/v1/stats").json()
    assert stats["first_party_observations"] == 3
    assert stats["real_observations_total"] == 0
    assert stats["real_observations_24h"] == 0
    assert stats["real_reporters_24h"] == 0
    assert stats["real_active_failures"] == 0
    assert stats["known_query_hits_24h"] == 0
    assert stats["unknown_query_hits_24h"] == 0
    # Real evidence, not demo data: no demo banner.
    assert stats["demo_data"] is False


def test_pruned_first_party_rows_are_still_not_adoption(client, operator_token):
    """Aggregates keep the label, and "real" is counted, never derived by
    subtracting the other kinds from the total."""
    insert_observation(minutes_ago=60 * 72, source="first_party")
    run_prune()

    stats = client.get("/v1/stats").json()
    assert stats["archived_observations"] == 1
    assert stats["first_party_observations"] == 1
    assert stats["real_observations_total"] == 0


# ---------------------------------------------------------------------------
# shown to agents, not hidden from them
# ---------------------------------------------------------------------------


def test_answers_say_who_saw_the_failure(client, operator_token):
    observe(client, headers=operator())
    answer = ask(client)
    assert answer["known"] is True
    assert answer["evidence_sources"] == ["first_party"]
    assert answer["demo_data_included"] is False

    observe(client)  # an independent agent hits it too
    assert ask(client)["evidence_sources"] == ["agent", "first_party"]


def test_an_unknown_failure_has_no_evidence_sources(client):
    assert ask(client)["evidence_sources"] == []


def test_the_dashboard_labels_first_party_rows(client, operator_token):
    for _ in range(12):
        fingerprint = observe(client, headers=operator())["fingerprint"]
    for successful in [True] * 9 + [False]:
        client.post(
            "/v1/outcome",
            json={
                "fingerprint": fingerprint,
                "action": "refresh_schema",
                "successful": successful,
            },
            headers=operator(),
        )

    row = client.get("/v1/services").json()[0]
    assert row["first_party_data"] is True
    assert row["demo_data"] is False

    echo = client.get("/v1/recovery-intelligence").json()[0]
    assert echo["first_party_data"] is True
    assert echo["demo_data"] is False


# ---------------------------------------------------------------------------
# naming: evidence is only shared when names match
# ---------------------------------------------------------------------------


def test_tools_tell_agents_how_to_name_what_failed(client):
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=MCP_HEADERS,
    )
    tools = {tool["name"]: tool for tool in response.json()["result"]["tools"]}
    for name in ("check_tool_failure", "report_tool_failure", "report_tool_success"):
        properties = tools[name]["inputSchema"]["properties"]
        assert "serverInfo.name" in properties["service"]["description"]
        assert "mcp__" in properties["operation"]["description"]
    assert "evidence_sources" in tools["check_tool_failure"]["description"]


def test_python_client_sends_the_operator_header(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
    from failecho import FailEcho

    assert FailEcho(operator_token="t")._headers()[OPERATOR_HEADER] == "t"

    monkeypatch.delenv("FAILECHO_OPERATOR_TOKEN", raising=False)
    assert OPERATOR_HEADER not in FailEcho()._headers()

    monkeypatch.setenv("FAILECHO_OPERATOR_TOKEN", "from-env")
    assert FailEcho()._headers()[OPERATOR_HEADER] == "from-env"


# ---------------------------------------------------------------------------
# hosts that will not send a custom header
# ---------------------------------------------------------------------------


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


def test_the_operator_token_is_accepted_as_a_bearer_credential(
    client, rows, operator_token
):
    """Claude Desktop's custom connector refuses header names off its
    allowlist, so `X-FailEcho-Operator` never leaves the client. Authorization
    is always allowed, so the same token is honoured there."""
    observe(client, headers=bearer(TOKEN))
    assert sources(rows) == ["first_party"]


def test_a_wrong_bearer_token_still_fails_to_demo(client, rows, operator_token):
    observe(client, headers=bearer("a-guess"))
    assert sources(rows) == ["demo_agent"]


def test_an_unrelated_authorization_header_is_not_an_operator_claim(
    client, rows, operator_token
):
    """Someone's proxy credential is not a claim to be FailEcho. Comparing it
    would turn an ordinary reporter into a failed operator claim, and quietly
    keep their genuine telemetry out of the adoption count."""
    observe(client, headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert sources(rows) == ["agent"]


def test_an_empty_claim_is_a_failed_claim_not_an_absent_one(
    client, rows, operator_token
):
    """Regression: reading the token with a truthiness check turned an empty
    operator header into no header at all, so a malformed claim landed in the
    adoption count instead of failing safe to demo."""
    observe(client, headers={"X-FailEcho-Operator": ""})
    observe(client, headers=bearer(""))
    assert sources(rows) == ["demo_agent", "demo_agent"]


def test_the_custom_header_wins_when_both_are_sent(client, rows, operator_token):
    observe(client, headers={**bearer("wrong"), "X-FailEcho-Operator": TOKEN})
    assert sources(rows) == ["first_party"]
