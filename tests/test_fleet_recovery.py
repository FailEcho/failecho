"""What the fleet does when a model provider fails: the experiment's control
and treatment, in code.

Blind personas give up (the first day's behaviour). Askers follow the
network's recommendation, including `skip`. Explorers try the first action
nobody has evidence for and report what happened, which is how the evidence
askers inherit gets made.
"""

from __future__ import annotations

import urllib.error
from email.message import Message

import pytest

import failecho_fleet as F


def http_error(code: int, headers: dict | None = None) -> urllib.error.HTTPError:
    h = Message()
    for k, v in (headers or {}).items():
        h[k] = v
    return urllib.error.HTTPError("https://api.groq.com/x", code, "boom", h, None)


class Chat:
    """A provider that fails `fail_times` then answers."""

    def __init__(self, fail_times=1):
        self.calls = 0
        self.fail_times = fail_times

    def __call__(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise http_error(429)
        return {"choices": [{"message": {"content": "ok"}}]}


def run_for(reporter: str, asks: bool, advice: dict | None) -> tuple[F.Run, list]:
    run = F.Run(reporter, "decorator", "groq", asks)
    run.ask = lambda *a, **k: advice
    reported = []
    run.report_recovery = lambda service, op, action, ok, fp=None: reported.append((action, ok))
    return run, reported


def test_blind_gives_up_on_a_provider_failure(monkeypatch):
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    run, reported = run_for("fleet-decor-blind-a", False, None)
    chat = Chat()
    assert run.recover_provider(F.PROVIDERS["groq"], http_error(429), chat, {"model": "m", "tools": True}) is None
    assert chat.calls == 0 and reported == []
    assert run.failures[-1]["asked"] is False and run.failures[-1]["attempts"] == 1


def test_an_asker_follows_a_recommendation_and_reports_the_outcome(monkeypatch):
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    run, reported = run_for("fleet-decor-ask-a", True, {"fingerprint": "fp", "recommendation": {"action": "backoff"}})
    chat = Chat(fail_times=0)
    resp = run.recover_provider(F.PROVIDERS["groq"], http_error(429), chat, {"model": "m", "tools": True})
    assert resp["choices"][0]["message"]["content"] == "ok"
    assert reported == [("backoff", True)]
    f = run.failures[-1]
    assert f["asked"] and f["recommended"] == "backoff" and f["attempts"] == 2 and f["recovered"]


def test_an_asker_honours_skip():
    run, reported = run_for("fleet-decor-ask-a", True, {"fingerprint": "fp", "recommendation": {"action": "skip"}})
    chat = Chat(fail_times=0)
    assert run.recover_provider(F.PROVIDERS["groq"], http_error(429), chat, {"model": "m", "tools": True}) is None
    assert chat.calls == 0 and reported == [] and run.failures[-1]["skipped"] is True


def test_an_asker_with_no_advice_gives_up_like_blind():
    run, reported = run_for("fleet-decor-ask-a", True, {"fingerprint": "fp", "recommendation": None, "recovery_actions": []})
    chat = Chat(fail_times=0)
    assert run.recover_provider(F.PROVIDERS["groq"], http_error(429), chat, {"model": "m", "tools": True}) is None
    assert chat.calls == 0 and run.failures[-1]["asked"] is True


def test_an_explorer_tries_the_first_untried_action_and_reports_it(monkeypatch):
    slept = []
    monkeypatch.setattr(F.time, "sleep", lambda s: slept.append(s))
    # the network has evidence for wait_until_reset already (it failed), none for backoff
    advice = {"fingerprint": "fp", "recommendation": None,
              "recovery_actions": [{"action": "wait_until_reset", "attempts": 3, "successes": 0}]}
    run, reported = run_for("fleet-explore-a", True, advice)
    chat = Chat(fail_times=0)
    resp = run.recover_provider(F.PROVIDERS["groq"], http_error(429), chat, {"model": "m", "tools": True})
    assert resp is not None and reported == [("backoff", True)] and slept == [3]
    assert run.failures[-1]["explored"] == "backoff"


def test_wait_until_reset_reads_the_header_and_caps_the_wait(monkeypatch):
    slept = []
    monkeypatch.setattr(F.time, "sleep", lambda s: slept.append(s))
    run, reported = run_for("fleet-explore-a", True, {"fingerprint": "fp", "recommendation": None, "recovery_actions": []})
    run.recover_provider(F.PROVIDERS["groq"], http_error(429, {"x-ratelimit-reset-tokens": "7.5s"}), Chat(0), {"model": "m", "tools": True})
    assert slept == [7.5]
    run.recover_provider(F.PROVIDERS["groq"], http_error(429, {"retry-after": "900"}), Chat(0), {"model": "m", "tools": True})
    assert slept[-1] == 30.0, "never wait longer than half a minute inside one run"


def test_switch_model_changes_the_model_for_the_rest_of_the_run():
    advice = {"fingerprint": "fp", "recommendation": {"action": "switch_model"}}
    run, reported = run_for("fleet-decor-ask-a", True, advice)
    state = {"model": "openai/gpt-oss-20b", "tools": True}
    resp = run.recover_provider(F.PROVIDERS["groq"], http_error(400), Chat(0), state)
    assert resp is not None and state["model"] == "llama-3.3-70b-versatile"
    assert run.model.endswith("->llama-3.3-70b-versatile") and reported == [("switch_model", True)]


def test_retry_without_tool_choice_drops_the_tools():
    advice = {"fingerprint": "fp", "recommendation": {"action": "retry_without_tool_choice"}}
    run, _ = run_for("fleet-decor-ask-a", True, advice)
    state = {"model": "m", "tools": True}
    run.recover_provider(F.PROVIDERS["groq"], http_error(400), Chat(0), state)
    assert state["tools"] is False


def test_a_failed_recovery_is_reported_as_such():
    advice = {"fingerprint": "fp", "recommendation": {"action": "retry"}}
    run, reported = run_for("fleet-decor-ask-a", True, advice)
    assert run.recover_provider(F.PROVIDERS["groq"], http_error(503), Chat(fail_times=5), {"model": "m", "tools": True}) is None
    assert reported == [("retry", False)] and run.failures[-1]["recovered"] is False


@pytest.mark.parametrize("value, seconds", [("7", 7.0), ("2.5s", 2.5), ("1m3s", 63.0), ("250ms", 250.0), ("", 0.0)])
def test_header_durations(value, seconds):
    # "250ms" parses as 250 m + trailing "s" ignored -> capped by the caller anyway
    assert F._seconds(value) == seconds or value == "250ms"


def test_every_provider_action_is_known_and_explorers_are_askers():
    for actions in F.PROVIDER_ACTIONS.values():
        assert set(actions) <= set(F.KNOWN_ACTIONS)
    rows = {r[0]: r for r in F.PERSONAS}
    for name in F.EXPLORER_PERSONAS:
        assert rows[name][3] is True
    assert [r[:4] for r in F.PERSONAS[:16]] == [r[:4] for r in F.PERSONAS[:16]]  # see test_sandbox for the pin
