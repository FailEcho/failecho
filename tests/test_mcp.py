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
    assert "demo_data_included" not in result, "the compact default carries no demo flag; verbose does"


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
        {**GITHUB, "error_message": "Repository 424242 was not found", "verbose": True},
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


def test_check_answers_compactly_by_default_and_fully_on_request(client):
    """Over MCP every token of the answer lands in a model's context on every
    check. The default keeps what changes a decision; verbose is the record."""
    import json

    from tests.conftest import insert_recovery, mcp_call, observe

    fp = observe(client)["fingerprint"]
    for i in range(6):
        insert_recovery(fingerprint=fp, action="refresh_schema", successful=True, minutes_ago=10 + i, reporter_hash=f"r{i % 3}")
    args = {"service": "github-mcp", "operation": "create_issue", "version": "2.8.1", "schema_hash": "a817ce",
            "error_type": "validation_error", "error_code": "422", "error_message": "Repository 918272 was not found"}
    compact = mcp_call(client, "check_tool_failure", args)
    full = mcp_call(client, "check_tool_failure", {**args, "verbose": True})
    assert compact["known"] and compact["recommendation"]["action"] == "refresh_schema"
    assert compact["recovery_actions"][0] == {"action": "refresh_schema", "successes": 6, "attempts": 6}
    assert set(compact) <= {"known", "status", "fingerprint", "observations", "recovery_actions", "recommendation",
                            "service_evidence", "related_failures", "success_evidence"}
    for gone in ("first_seen", "failure_rate", "evidence_sources", "demo_data_included", "looks_new"):
        assert gone not in compact and gone in full
    assert "effective_attempts" not in compact["recovery_actions"][0] and "effective_attempts" in full["recovery_actions"][0]
    assert len(json.dumps(compact)) < 0.5 * len(json.dumps(full))


def test_check_unknown_stays_honest_in_the_compact_shape(client):
    from tests.conftest import mcp_call

    result = mcp_call(client, "check_tool_failure", {"service": "x.example", "operation": "op", "error_type": "timeout"})
    assert result["known"] is False and result["recommendation"] is None and result["recovery_actions"] == []
    assert result["observations"]["total"] == 0


def test_the_tool_list_is_lean():
    """Most clients paste the whole tool list into the model on every turn.
    No titles on parameters, and the four definitions together stay under a
    budget; a description that grows past it costs every user every turn."""
    import json

    from app.mcp_server import mcp_server

    total = 0
    for tool in mcp_server._tool_manager._tools.values():
        assert "title" not in tool.parameters
        for prop in tool.parameters["properties"].values():
            assert "title" not in prop
            assert "anyOf" not in prop, "an optional field is its plain type, left out of required"
        total += len(tool.description or "") + len(json.dumps(tool.parameters))
    assert total < 6000, f"tool list is {total} chars (~{total // 4} tokens)"


def test_an_explicit_null_is_still_accepted(client):
    """The schema no longer spells out `null`, but clients written against
    the old one send it; the tool must not start rejecting them."""
    from tests.conftest import mcp_call

    result = mcp_call(client, "check_tool_failure", {"service": "x.example", "operation": "op", "error_type": None,
                                                      "error_code": None, "reporter_id": None})
    assert result["known"] is False
