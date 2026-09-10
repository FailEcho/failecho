"""The public Python integration: FailEcho, the adapter seam, Pydantic AI.

The properties that matter here are not features. They are promises: never
break the caller's agent, never act on its behalf, never send its data.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))

from failecho import FailEcho, FailureDecision, ToolOutcome  # noqa: E402
from failecho.adapters import FailEchoSink, ToolCall, ToolTelemetrySink  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class Bridge:
    """Route the client's urllib calls into the in-process TestClient."""

    def __init__(self, test_client):
        self.test_client = test_client
        self.sent: list[tuple[str, dict, dict]] = []

    def __call__(self, request, timeout=None):
        payload = json.loads(request.data)
        self.sent.append((request.full_url, payload, dict(request.header_items())))
        response = self.test_client.post(
            request.full_url, content=request.data, headers=dict(request.header_items())
        )

        class _R:
            def read(self_inner):
                return response.content

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

        return _R()

    def paths(self) -> list[str]:
        return [url.split("testserver")[-1] for url, _, _ in self.sent]


class ToolBroke(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.status_code = 422


# ---------------------------------------------------------------------------
# the one call most agents need
# ---------------------------------------------------------------------------


def test_success_is_reported_and_result_returned(client):
    echo = FailEcho("http://testserver", reporter_id="agent-a")
    bridge = Bridge(client)

    async def tool():
        return {"id": 7}

    with patch("urllib.request.urlopen", bridge):
        outcome = run(
            echo.observe_tool_call(
                service="github-mcp",
                operation="create_issue",
                version="2.8.1",
                schema_hash="a817ce",
                call=tool,
            )
        )

    assert outcome.ok is True
    assert outcome.result == {"id": 7}
    assert bridge.paths() == ["/v1/observe"]
    assert bridge.sent[0][1]["outcome"] == "success"
    assert client.get("/v1/stats").json()["real_successes_24h"] == 1


def test_failure_reports_then_queries_and_returns_a_decision(client):
    echo = FailEcho("http://testserver", reporter_id="agent-a")
    bridge = Bridge(client)

    async def tool():
        raise ToolBroke("Repository 918272 was not found")

    with patch("urllib.request.urlopen", bridge):
        outcome = run(
            echo.observe_tool_call(
                service="github-mcp",
                operation="create_issue",
                version="2.8.1",
                schema_hash="a817ce",
                call=tool,
            )
        )

    assert outcome.failed is True
    assert isinstance(outcome.decision, FailureDecision)
    assert outcome.decision.fingerprint
    assert bridge.paths() == ["/v1/observe", "/v1/query"]
    assert bridge.sent[0][1]["outcome"] == "failure"
    assert bridge.sent[0][1]["error_code"] == "422"


def test_all_three_telemetry_kinds_are_captured(client):
    """success + failure + recovery outcome, the full loop."""
    echo = FailEcho("http://testserver", reporter_id="agent-a")
    bridge = Bridge(client)

    async def broken():
        raise ToolBroke("Repository 918272 was not found")

    async def fixed():
        return "ok"

    with patch("urllib.request.urlopen", bridge):
        failed = run(
            echo.observe_tool_call(
                service="github-mcp", operation="create_issue", call=broken
            )
        )
        run(
            echo.report_recovery(
                fingerprint=failed.decision.fingerprint,
                action="refresh_schema",
                successful=True,
            )
        )
        run(echo.observe_tool_call(service="github-mcp", operation="create_issue", call=fixed))

    stats = client.get("/v1/stats").json()
    assert stats["real_failures_24h"] == 1
    assert stats["real_successes_24h"] == 1
    assert stats["recovery_outcomes_total"] == 1
    assert stats["recovery_outcome_ratio_24h"] == 1.0


# ---------------------------------------------------------------------------
# promises
# ---------------------------------------------------------------------------


def test_never_breaks_the_agent_when_failecho_is_down():
    """Unreachable endpoint: the tool result still comes back."""
    echo = FailEcho("http://127.0.0.1:9", timeout=0.2, reporter_id="agent-a")

    async def tool():
        return "business value"

    outcome = run(echo.observe_tool_call(service="s", operation="o", call=tool))
    assert outcome.ok is True
    assert outcome.result == "business value"

    async def broken():
        raise ToolBroke("boom")

    outcome = run(echo.observe_tool_call(service="s", operation="o", call=broken))
    assert outcome.failed is True
    assert outcome.decision.known is False
    assert outcome.decision.recommendation is None


def test_never_performs_recovery_itself(client):
    calls = {"n": 0}

    async def tool():
        calls["n"] += 1
        raise ToolBroke("Repository 918272 was not found")

    echo = FailEcho("http://testserver")
    bridge = Bridge(client)
    with patch("urllib.request.urlopen", bridge):
        run(echo.observe_tool_call(service="github-mcp", operation="create_issue", call=tool))

    assert calls["n"] == 1, "the tool is called exactly once"
    assert "/v1/outcome" not in bridge.paths(), "no recovery reported on our behalf"


def test_sends_metadata_only(client):
    """Whatever the caller's tool touched, none of it may leave the process."""
    echo = FailEcho("http://testserver", reporter_id="agent-a")
    bridge = Bridge(client)

    async def tool():
        raise ToolBroke("Repository 918272 was not found")

    with patch("urllib.request.urlopen", bridge):
        run(
            echo.observe_tool_call(
                service="github-mcp",
                operation="create_issue",
                version="2.8.1",
                schema_hash="a817ce",
                call=tool,
            )
        )

    allowed = {
        "service", "operation", "version", "schema_hash", "outcome",
        "error_type", "error_code", "error_message", "latency_ms",
    }
    for _, payload, _ in bridge.sent:
        assert set(payload) <= allowed, f"unexpected field sent: {set(payload) - allowed}"


def test_reporter_id_travels_as_a_header_and_is_hashed(client, rows):
    echo = FailEcho("http://testserver", reporter_id="plaintext-agent")
    bridge = Bridge(client)

    async def tool():
        return 1

    with patch("urllib.request.urlopen", bridge):
        run(echo.observe_tool_call(service="s", operation="o", call=tool))

    headers = {k.lower(): v for _, _, h in bridge.sent for k, v in h.items()}
    assert headers["x-reporter-id"] == "plaintext-agent"
    stored = rows("observations")[0]["reporter_hash"]
    assert stored and stored != "plaintext-agent"


def test_master_switch_makes_everything_a_noop(client):
    echo = FailEcho("http://testserver", enabled=False)
    bridge = Bridge(client)

    async def tool():
        return "value"

    with patch("urllib.request.urlopen", bridge):
        outcome = run(echo.observe_tool_call(service="s", operation="o", call=tool))

    assert outcome.ok is True and outcome.result == "value"
    assert bridge.sent == [], "disabled client sends nothing"


def test_env_var_disables_the_client(monkeypatch):
    monkeypatch.setenv("FAILECHO_DISABLED", "1")
    assert FailEcho("http://testserver").enabled is False
    monkeypatch.delenv("FAILECHO_DISABLED")
    assert FailEcho("http://testserver").enabled is True


def test_success_sampling_defaults_to_reporting_everything(client):
    """The network needs denominators; sampling exists but stays off."""
    assert FailEcho("http://testserver").success_sample_rate == 1.0

    echo = FailEcho("http://testserver", success_sample_rate=0.0)
    bridge = Bridge(client)

    async def tool():
        return 1

    with patch("urllib.request.urlopen", bridge):
        run(echo.observe_tool_call(service="s", operation="o", call=tool))
    assert bridge.sent == [], "rate 0.0 reports no successes"


def test_demo_flag_labels_traffic(client):
    echo = FailEcho("http://testserver", demo=True)
    bridge = Bridge(client)

    async def tool():
        return 1

    with patch("urllib.request.urlopen", bridge):
        run(echo.observe_tool_call(service="s", operation="o", call=tool))

    headers = {k.lower(): v for _, _, h in bridge.sent for k, v in h.items()}
    assert headers["x-reporter-kind"] == "demo"
    assert client.get("/v1/stats").json()["real_observations_24h"] == 0


# ---------------------------------------------------------------------------
# the adapter seam
# ---------------------------------------------------------------------------


def test_sink_implements_the_four_event_protocol():
    sink = FailEchoSink(FailEcho("http://127.0.0.1:9", timeout=0.2))
    assert isinstance(sink, ToolTelemetrySink)
    for event in ("tool_started", "tool_succeeded", "tool_failed", "recovery_reported"):
        assert hasattr(sink, event)


def test_tool_call_carries_no_payload_fields():
    """The seam cannot leak arguments because it has nowhere to put them."""
    assert set(ToolCall.__dataclass_fields__) == {
        "service",
        "operation",
        "version",
        "schema_hash",
    }


def test_sink_reports_through_to_the_api(client):
    echo = FailEcho("http://testserver", reporter_id="agent-a")
    sink = FailEchoSink(echo)
    call = ToolCall(service="github-mcp", operation="create_issue", version="2.8.1")
    bridge = Bridge(client)

    with patch("urllib.request.urlopen", bridge):
        run(sink.tool_succeeded(call, 120))
        decision = run(sink.tool_failed(call, 300, ToolBroke("Repository 918272 not found")))

    assert isinstance(decision, FailureDecision)
    assert bridge.paths() == ["/v1/observe", "/v1/observe", "/v1/query"]


# ---------------------------------------------------------------------------
# reference integration
# ---------------------------------------------------------------------------

pydantic_ai = pytest.importorskip("pydantic_ai", reason="pydantic-ai not installed")


class StubToolset:
    """Minimal stand-in for a Pydantic AI toolset."""

    id = "github-mcp"

    def __init__(self, fail=False):
        self.fail = fail
        self.seen: list[tuple[str, dict]] = []

    async def call_tool(self, name, tool_args, ctx, tool):
        self.seen.append((name, tool_args))
        if self.fail:
            raise ToolBroke("Repository 918272 was not found")
        return {"id": 1}

    async def for_run(self, ctx):
        return self


def test_pydantic_ai_toolset_reports_success_transparently(client):
    from failecho.integrations.pydantic_ai import instrument_toolset

    inner = StubToolset()
    wrapped = instrument_toolset(inner, FailEcho("http://testserver"))
    bridge = Bridge(client)

    with patch("urllib.request.urlopen", bridge):
        result = run(wrapped.call_tool("create_issue", {"secret": "value"}, None, None))

    assert result == {"id": 1}, "the wrapper is behaviourally invisible"
    assert bridge.paths() == ["/v1/observe"]
    payload = bridge.sent[0][1]
    assert payload["service"] == "github-mcp"
    assert payload["operation"] == "create_issue"
    assert payload["outcome"] == "success"
    assert "secret" not in json.dumps(payload), "tool_args must never be sent"


def test_pydantic_ai_toolset_reports_failure_and_reraises(client):
    from failecho.integrations.pydantic_ai import instrument_toolset

    wrapped = instrument_toolset(StubToolset(fail=True), FailEcho("http://testserver"))
    bridge = Bridge(client)

    with patch("urllib.request.urlopen", bridge):
        with pytest.raises(ToolBroke):
            run(wrapped.call_tool("create_issue", {"repo": "acme/private"}, None, None))

    assert bridge.paths() == ["/v1/observe", "/v1/query"]
    assert bridge.sent[0][1]["outcome"] == "failure"
    assert "acme/private" not in json.dumps(bridge.sent)


def test_pydantic_ai_telemetry_failure_never_breaks_the_tool():
    """FailEcho unreachable: the tool result still returns, the error still raises."""
    from failecho.integrations.pydantic_ai import instrument_toolset

    echo = FailEcho("http://127.0.0.1:9", timeout=0.2)
    assert run(
        instrument_toolset(StubToolset(), echo).call_tool("create_issue", {}, None, None)
    ) == {"id": 1}

    with pytest.raises(ToolBroke):
        run(
            instrument_toolset(StubToolset(fail=True), echo).call_tool(
                "create_issue", {}, None, None
            )
        )
