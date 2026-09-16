"""The MCP surface: four tools, same brain as the REST API.

These run against the real Streamable HTTP endpoint mounted inside the app, so
they exercise the transport an actual MCP client would speak.
"""

from __future__ import annotations

from tests.conftest import mcp_call, observe, query

GITHUB = dict(
    service="github-mcp",
    operation="create_issue",
    version="2.8.1",
    schema_hash="a817ce",
    error_type="validation_error",
    error_code="422",
)


def rpc(client, method: str, params: dict | None = None) -> dict:
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={"accept": "application/json, text/event-stream"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_endpoint_is_served_without_a_redirect(client):
    """MCP clients POST to /mcp exactly; a 307 would be theirs to follow."""
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"accept": "application/json, text/event-stream"},
        follow_redirects=False,
    )
    assert response.status_code == 200


def test_lists_the_four_tools_with_usable_descriptions(client):
    tools = {t["name"]: t for t in rpc(client, "tools/list")["result"]["tools"]}
    assert set(tools) == {
        "check_tool_failure",
        "report_tool_failure",
        "report_tool_success",
        "report_recovery_outcome",
    }

    check = tools["check_tool_failure"]
    # An LLM decides whether to call a tool from this text alone.
    assert "fails" in check["description"]
    assert "BEFORE retrying" in check["description"]
    assert set(check["inputSchema"]["required"]) == {"service", "operation"}

    report = tools["report_tool_failure"]
    assert "PRIVACY" in report["description"]
    for forbidden in ("prompts", "API keys", "tool results"):
        assert forbidden in report["description"]


def test_check_unknown_failure_is_honest(client):
    result = mcp_call(
        client,
        "check_tool_failure",
        {"service": "nobody-mcp", "operation": "never_called", "error_type": "weird"},
    )
    assert result["known"] is False
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["recommendation"] is None
    assert result["observations"]["total"] == 0


def test_check_known_failure_returns_intelligence(client):
    for _ in range(12):
        observe(client)
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

    result = mcp_call(
        client,
        "check_tool_failure",
        {**GITHUB, "error_message": "Repository 424242 was not found"},
    )
    assert result["known"] is True
    assert result["fingerprint"] == fingerprint
    assert result["status"] == "MAJOR"
    assert result["observations"]["total"] == 13
    assert result["recovery_actions"][0]["action"] == "refresh_schema"
    assert result["recommendation"]["action"] == "refresh_schema"
    assert 0 < result["recommendation"]["confidence"] < 1
    assert result["demo_data_included"] is False


def test_report_failure_through_mcp(client):
    result = mcp_call(
        client,
        "report_tool_failure",
        {
            **GITHUB,
            "error_message": "Repository 918272 was not found",
            "latency_ms": 421,
            "reporter_id": "mcp-agent-1",
        },
    )
    assert result["accepted"] is True
    assert result["known"] is False
    assert result["observations"] == 1
    assert result["normalized_error"] == "Repository <N> was not found"

    again = mcp_call(
        client,
        "report_tool_failure",
        {**GITHUB, "error_message": "Repository 555812 was not found"},
    )
    assert again["known"] is True
    assert again["observations"] == 2
    assert again["fingerprint"] == result["fingerprint"]


def test_report_success_through_mcp(client):
    assert mcp_call(
        client,
        "report_tool_success",
        {
            "service": "github-mcp",
            "operation": "create_issue",
            "version": "2.8.1",
            "schema_hash": "a817ce",
            "latency_ms": 318,
        },
    ) == {"accepted": True}

    stats = client.get("/v1/stats").json()
    assert stats["observations_total"] == 1
    assert stats["failures_1h"] == 0


def test_report_recovery_outcome_through_mcp(client):
    fingerprint = mcp_call(
        client,
        "report_tool_failure",
        {**GITHUB, "error_message": "Repository 918272 was not found"},
    )["fingerprint"]

    for _ in range(6):
        assert mcp_call(
            client,
            "report_recovery_outcome",
            {
                "fingerprint": fingerprint,
                "action": "refresh_schema",
                "successful": True,
                "reporter_id": "mcp-agent-1",
            },
        ) == {"accepted": True}

    intel = mcp_call(
        client,
        "check_tool_failure",
        {**GITHUB, "error_message": "Repository 111111 was not found"},
    )
    assert intel["recovery_actions"][0]["attempts"] == 6
    assert intel["recommendation"]["action"] == "refresh_schema"


def test_mcp_and_rest_agree_on_fingerprints(client):
    """One brain, two transports: the fingerprint must be identical."""
    rest = observe(client, error_message="Repository 918272 was not found")
    via_mcp = mcp_call(
        client,
        "check_tool_failure",
        {**GITHUB, "error_message": "Repository 424242 was not found"},
    )
    assert via_mcp["fingerprint"] == rest["fingerprint"]
    assert via_mcp["normalized_error"] == rest["normalized_error"]

    reported = mcp_call(
        client,
        "report_tool_failure",
        {**GITHUB, "error_message": "Repository 777777 was not found"},
    )
    assert reported["fingerprint"] == rest["fingerprint"]


def test_mcp_reports_land_in_the_same_intelligence_as_rest(client):
    mcp_call(
        client,
        "report_tool_failure",
        {**GITHUB, "error_message": "Repository 918272 was not found"},
    )
    intel = query(client)
    assert intel["known"] is True
    assert intel["observations"]["total"] == 1


def test_mcp_reporter_ids_are_hashed(client, rows):
    mcp_call(
        client,
        "report_tool_failure",
        {**GITHUB, "error_message": "boom", "reporter_id": "mcp-secret-agent"},
    )
    stored = rows("observations")[0]
    assert stored["reporter_hash"] is not None
    assert stored["reporter_hash"] != "mcp-secret-agent"


def test_mcp_validates_input(client):
    """A malformed fingerprint is rejected, not silently stored."""
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "report_recovery_outcome",
                "arguments": {
                    "fingerprint": "nope",
                    "action": "retry",
                    "successful": True,
                },
            },
        },
        headers={"accept": "application/json, text/event-stream"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body.get("error") or body["result"].get("isError") is True


def test_get_on_the_endpoint_is_405_not_a_held_connection(client):
    """A stateless server has no server-initiated stream to offer. The SDK
    would hold a GET open forever anyway -- a free connection-hold vector,
    confirmed live before this. The spec's answer for a server without the
    stream is 405, with Allow naming what works."""
    r = client.get("/mcp", headers={"Accept": "text/event-stream"})
    assert r.status_code == 405
    assert "POST" in r.headers["allow"]
    assert "stateless" in r.json()["detail"]


def test_post_still_works_after_the_get_guard(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                    headers={"Accept": "application/json, text/event-stream"})
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert names == {"check_tool_failure", "report_tool_failure", "report_tool_success", "report_recovery_outcome"}
