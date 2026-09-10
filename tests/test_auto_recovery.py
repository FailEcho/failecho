"""The failure-aware wrapper: reports, asks, and never acts on its own.

The safety property matters more than the convenience one, so it is tested
explicitly: this wrapper must not retry, refresh or fall back by itself.
"""

from __future__ import annotations

import pytest

from client.auto_recovery import (
    FailureDecision,
    classify_exception,
    run_with_failure_intelligence,
)


class FakeNetwork:
    """Records calls; returns whatever intelligence the test wants."""

    def __init__(self, intelligence: dict | None = None) -> None:
        self.intelligence = intelligence or {
            "known": False,
            "fingerprint": "f" * 32,
            "status": "INSUFFICIENT_DATA",
            "observations": {"total": 0, "unique_reporters": 0},
            "recovery_actions": [],
            "recommendation": None,
            "demo_data_included": False,
        }
        self.calls: list[tuple[str, dict]] = []

    async def report_tool_failure(self, **kwargs):
        self.calls.append(("report_tool_failure", kwargs))
        return {"accepted": True, "fingerprint": self.intelligence["fingerprint"]}

    async def report_tool_success(self, **kwargs):
        self.calls.append(("report_tool_success", kwargs))
        return {"accepted": True}

    async def check_tool_failure(self, **kwargs):
        self.calls.append(("check_tool_failure", kwargs))
        return self.intelligence

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


class ToolBroke(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.status_code = 422


def run(coro):
    import asyncio

    return asyncio.run(coro)


def test_success_is_reported_and_the_result_returned():
    network = FakeNetwork()

    async def tool():
        return {"id": 1}

    outcome = run(
        run_with_failure_intelligence(
            tool_call=tool,
            service="demo-issues-api",
            operation="create_issue",
            network=network,
        )
    )

    assert outcome.ok is True
    assert outcome.result == {"id": 1}
    assert outcome.decision is None
    assert network.names == ["report_tool_success"]


def test_failure_is_reported_then_the_network_is_asked():
    network = FakeNetwork(
        {
            "known": True,
            "fingerprint": "a" * 32,
            "status": "MAJOR",
            "observations": {"total": 11, "unique_reporters": 5},
            "recovery_actions": [
                {"action": "refresh_schema", "attempts": 5, "successes": 5,
                 "success_rate": 1.0, "confidence": 0.5655, "unique_reporters": 5,
                 "effective_attempts": 5, "effective_successes": 5},
            ],
            "recommendation": {"action": "refresh_schema", "confidence": 0.5655},
            "demo_data_included": True,
        }
    )

    async def tool():
        raise ToolBroke("Repository 123456 rejected field body")

    outcome = run(
        run_with_failure_intelligence(
            tool_call=tool,
            service="demo-issues-api",
            operation="create_issue",
            version="2.0.0",
            schema_hash="v2body",
            network=network,
        )
    )

    assert outcome.failed is True
    assert network.names == ["report_tool_failure", "check_tool_failure"]

    reported = network.calls[0][1]
    assert reported["error_code"] == "422"
    assert reported["error_type"] == "ToolBroke"
    assert reported["service"] == "demo-issues-api"
    assert reported["schema_hash"] == "v2body"

    decision = outcome.decision
    assert isinstance(decision, FailureDecision)
    assert decision.actionable is True
    assert decision.recommendation == "refresh_schema"
    assert decision.confidence == pytest.approx(0.5655)
    assert decision.observations == 11
    assert decision.unique_reporters == 5
    assert decision.demo_data_included is True


def test_wrapper_never_performs_recovery_itself():
    """Safety over convenience: the caller decides, always."""
    calls = {"count": 0}

    async def tool():
        calls["count"] += 1
        raise ToolBroke("Repository 123456 rejected field body")

    network = FakeNetwork(
        {
            "known": True,
            "fingerprint": "a" * 32,
            "status": "MAJOR",
            "observations": {"total": 11, "unique_reporters": 5},
            "recovery_actions": [],
            "recommendation": {"action": "refresh_schema", "confidence": 0.9},
            "demo_data_included": False,
        }
    )

    outcome = run(
        run_with_failure_intelligence(
            tool_call=tool,
            service="s",
            operation="o",
            network=network,
        )
    )

    assert calls["count"] == 1, "the tool must be called exactly once"
    assert outcome.failed is True
    assert outcome.decision.recommendation == "refresh_schema"
    assert "report_recovery_outcome" not in network.names


def test_tool_exception_is_returned_not_raised():
    async def tool():
        raise ToolBroke("boom")

    outcome = run(
        run_with_failure_intelligence(
            tool_call=tool, service="s", operation="o", network=FakeNetwork()
        )
    )
    assert isinstance(outcome.error, ToolBroke)
    assert outcome.decision.actionable is False


def test_success_reporting_can_be_switched_off():
    async def tool():
        return "ok"

    network = FakeNetwork()
    outcome = run(
        run_with_failure_intelligence(
            tool_call=tool,
            service="s",
            operation="o",
            network=network,
            report_success=False,
        )
    )
    assert outcome.ok is True
    assert network.names == []


def test_default_classifier_reads_status_code():
    error_type, code, message = classify_exception(ToolBroke("nope"))
    assert (error_type, code, message) == ("ToolBroke", "422", "nope")
    error_type, code, message = classify_exception(ValueError("plain"))
    assert (error_type, code, message) == ("ValueError", None, "plain")
