"""FailEcho's own agent. What is tested is what keeps it honest: it never
runs against production unlabelled, it asks before it retries, it reports
what it tried, and it never retries the failures that cannot be retried."""

from __future__ import annotations

import json

import pytest

import failecho_agent as A
from failecho_autoreport import FailEcho


class Recorder(FailEcho):
    def __init__(self):
        super().__init__(endpoint="http://127.0.0.1:9", reporter_id="t", operator_token="tok")
        self.bodies: list[dict] = []
        self.outcomes: list[dict] = []

    def _post(self, body):
        self.bodies.append(body)

    def _post_outcome(self, body):
        self.outcomes.append(body)


def agent_with(monkeypatch, tool, advice=None):
    fe = Recorder()
    ag = A.Agent(fe, [])
    ag.tools.pypi_latest_version = fe.watch(service="pypi.org", operation="pypi_latest_version")(tool)
    monkeypatch.setattr(ag, "ask_network", lambda *a, **k: advice)
    monkeypatch.setattr(A.time, "sleep", lambda s: None)
    return ag, fe


# -- it must not become a stranger -----------------------------------------


def test_refuses_production_without_the_operator_token(monkeypatch, capsys):
    monkeypatch.setattr(A, "OPERATOR_TOKEN", None)
    monkeypatch.setattr(A, "ENDPOINT", "https://failecho.com")
    assert A.main([]) == 2
    assert "independent adoption" in capsys.readouterr().out


def test_localhost_is_allowed_without_a_token():
    assert A._is_local("http://127.0.0.1:8091")
    assert not A._is_local("https://failecho.com")


def test_reports_carry_the_operator_token():
    fe = Recorder()
    assert fe._headers()["Authorization"] == "Bearer tok"


# -- ask, then act ---------------------------------------------------------


def test_a_404_is_reported_and_never_retried(monkeypatch):
    calls = {"n": 0}

    def tool(package):
        calls["n"] += 1
        raise RuntimeError("HTTP Error 404: Not Found")

    ag, fe = agent_with(monkeypatch, tool)
    text, ok = ag.run_tool("pypi_latest_version", {"package": "nope"})
    assert not ok and calls["n"] == 1, "a 404 was retried"
    assert json.loads(text)["error"] == "not_found"
    fe.flush(5)
    assert fe.outcomes == [], "no recovery was attempted, so none may be reported"
    assert ag.failures == [("pypi.org", "pypi_latest_version", "not_found", "404")]


def test_a_rate_limit_backs_off_once_and_reports_the_outcome(monkeypatch):
    calls = {"n": 0}

    def tool(package):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("HTTP Error 429: rate limit")
        return {"version": "1.0"}

    ag, fe = agent_with(monkeypatch, tool)
    text, ok = ag.run_tool("pypi_latest_version", {"package": "x"})
    assert ok and calls["n"] == 2
    assert json.loads(text)["recovery"] == "backoff"
    fe.flush(5)
    assert fe.outcomes == [{"service": "pypi.org", "operation": "pypi_latest_version",
                            "action": "backoff", "successful": True}]


def test_a_failed_recovery_is_reported_as_failed(monkeypatch):
    def tool(package):
        raise RuntimeError("HTTP Error 503: service unavailable")

    ag, fe = agent_with(monkeypatch, tool)
    text, ok = ag.run_tool("pypi_latest_version", {"package": "x"})
    assert not ok
    fe.flush(5)
    assert fe.outcomes[0]["action"] == "retry" and fe.outcomes[0]["successful"] is False


def test_the_networks_recommendation_wins_over_the_rules(monkeypatch):
    """A 404 would never be retried by the rules. If the network says another
    action worked for others, the agent tries it -- that is the whole point of
    asking -- and reports how it went."""
    calls = {"n": 0}

    def tool(package):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("HTTP Error 404: Not Found")
        return {"version": "2.0"}

    advice = {"recommendation": {"action": "refresh_schema", "confidence": 0.6}}
    ag, fe = agent_with(monkeypatch, tool, advice)
    text, ok = ag.run_tool("pypi_latest_version", {"package": "x"})
    assert ok and calls["n"] == 2
    fe.flush(5)
    assert fe.outcomes[0]["action"] == "refresh_schema"


def test_decide_follows_the_documented_rules():
    ag = A.Agent(Recorder(), [])
    assert ag.decide("rate_limit", None) == "backoff"
    assert ag.decide("server_error", None) == "retry"
    assert ag.decide("timeout", None) == "retry"
    assert ag.decide("not_found", None) is None
    assert ag.decide("validation_error", None) is None
    assert ag.decide("auth_error", None) is None


# -- the tools stay on the leash -------------------------------------------


def test_fetch_refuses_hosts_off_the_allowlist():
    with pytest.raises(ValueError, match="host not allowed"):
        A.Tools().fetch_text("https://evil.example/x")


def test_every_tool_is_mapped_to_a_service():
    names = {t["function"]["name"] for t in A.TOOL_SCHEMAS}
    assert names == set(A.TOOL_SERVICE)


# -- budget ----------------------------------------------------------------


def test_daily_budget_is_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(A, "MAX_RUNS_PER_DAY", 2)
    assert A._take_run_slot() == 1
    assert A._take_run_slot() == 2
    assert A._take_run_slot() is None
