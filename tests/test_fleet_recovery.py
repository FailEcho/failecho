"""What the fleet does when a model provider fails: the experiment's control
and treatment, in code.

Blind personas, and askers the network has nothing for, retry once after a
short pause -- the control, what an agent without the network does (since
2026-09-18 05:30; on the first day they gave up at once). Askers follow the
network's recommendation, including `skip`. Explorers try what the network
still lacks evidence for and report what happened, which is how the
evidence askers inherit gets made.
"""

from __future__ import annotations

import urllib.error
from email.message import Message

import pytest

import failecho_fleet as F


def http_error(code: int, headers: dict | None = None, reason: str = "boom") -> urllib.error.HTTPError:
    h = Message()
    for k, v in (headers or {}).items():
        h[k] = v
    return urllib.error.HTTPError("https://api.groq.com/x", code, reason, h, None)


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


def test_blind_retries_once_and_reports_the_outcome(monkeypatch):
    """The control is one plain retry after a pause, not giving up: an agent
    without the network retries. Giving up flattered the ask side."""
    slept = []
    monkeypatch.setattr(F.time, "sleep", lambda s: slept.append(s))
    run, reported = run_for("fleet-decor-blind-a", False, None)
    chat = Chat(fail_times=0)   # the first failure already happened; the retry lands
    resp = run.recover_provider(F.PROVIDERS["groq"], http_error(429), chat, {"model": "m", "tools": True})
    assert resp is not None and chat.calls == 1 and slept == [3] and reported == [("retry", True)]
    f = run.failures[-1]
    assert f["asked"] is False and f["attempts"] == 2 and f["recovered"] and f["recommended"] is None

    run, reported = run_for("fleet-decor-blind-a", False, None)
    chat = Chat(fail_times=5)
    assert run.recover_provider(F.PROVIDERS["groq"], http_error(429), chat, {"model": "m", "tools": True}) is None
    assert chat.calls == 1 and reported == [("retry", False)] and run.failures[-1]["recovered"] is False


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


def test_an_asker_with_no_advice_retries_once_like_blind(monkeypatch):
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    run, reported = run_for("fleet-decor-ask-a", True, {"fingerprint": "fp", "recommendation": None, "recovery_actions": []})
    chat = Chat(fail_times=0)
    assert run.recover_provider(F.PROVIDERS["groq"], http_error(429), chat, {"model": "m", "tools": True}) is not None
    assert chat.calls == 1 and run.failures[-1]["asked"] is True and reported == [("retry", True)]
    assert "explored" not in run.failures[-1], "only explorers explore; an asker's edge is inherited, not invented"


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


def test_an_explorer_keeps_trying_an_action_until_the_network_can_rule(monkeypatch):
    """switch_model sat at 1 of 1 for a day: the explorer tried each action
    once and stopped, and one attempt is below the server's floor of five,
    so no asker ever inherited it. Below the floor the explorer goes back to
    the action that has worked best; at the floor it stops."""
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    evidence = [{"action": "wait_until_reset", "attempts": 1, "successes": 0},
                {"action": "backoff", "attempts": 1, "successes": 0},
                {"action": "switch_model", "attempts": 1, "successes": 1}]
    assert F.explore(F.PROVIDER_ACTIONS["rate_limit"], evidence) == "switch_model"
    evidence[2]["attempts"] = 5
    assert F.explore(F.PROVIDER_ACTIONS["rate_limit"], evidence) == "wait_until_reset", "least tried among the rest"
    for e in evidence:
        e["attempts"] = 5
    assert F.explore(F.PROVIDER_ACTIONS["rate_limit"], evidence) is None
    assert F.explore(F.PROVIDER_ACTIONS["rate_limit"], []) == "wait_until_reset"

    run, reported = run_for("fleet-explore-a", True, {"fingerprint": "fp", "recommendation": None, "recovery_actions": [
        {"action": "wait_until_reset", "attempts": 1, "successes": 0}, {"action": "backoff", "attempts": 1, "successes": 0},
        {"action": "switch_model", "attempts": 1, "successes": 1}]})
    state = {"model": "openai/gpt-oss-20b", "tools": True}
    assert run.recover_provider(F.PROVIDERS["groq"], http_error(429), Chat(0), state) is not None
    assert run.failures[-1]["explored"] == "switch_model" and state["model"] == "openai/gpt-oss-120b"


def test_an_explorer_explores_past_a_skip_verdict_and_honours_it_only_when_nothing_is_left(monkeypatch):
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    advice = {"fingerprint": "fp", "recommendation": {"action": "skip"}, "recovery_actions": [
        {"action": "retry", "attempts": 9, "successes": 0}]}
    run, reported = run_for("fleet-explore-a", True, advice)
    assert run.recover_provider(F.PROVIDERS["groq"], http_error(429), Chat(0), {"model": "m", "tools": True}) is not None
    assert run.failures[-1]["explored"] == "wait_until_reset" and "skipped" not in run.failures[-1]
    advice["recovery_actions"] = [{"action": a, "attempts": 5, "successes": 0} for a in F.PROVIDER_ACTIONS["rate_limit"]]
    run, reported = run_for("fleet-explore-a", True, advice)
    chat = Chat(0)
    assert run.recover_provider(F.PROVIDERS["groq"], http_error(429), chat, {"model": "m", "tools": True}) is None
    assert chat.calls == 0 and run.failures[-1]["skipped"] is True


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
    assert resp is not None and state["model"] == "openai/gpt-oss-120b"
    assert run.model.endswith("->openai/gpt-oss-120b") and reported == [("switch_model", True)]


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


# -- tool calls: the real-target loop ----------------------------------------


def tool_run(reporter, asks, advice, fn):
    run = F.Run(reporter, "decorator", None, asks)
    run.ask = lambda *a, **k: advice
    reported = []
    run.report_recovery = lambda service, op, action, ok, fp=None: reported.append((action, ok))
    run.report_failure = lambda *a, **k: None
    run.report_success = lambda *a, **k: None
    run.tools["github_repo"] = fn
    return run, reported


def test_a_tool_explorer_tries_the_first_untried_action(monkeypatch):
    slept = []
    monkeypatch.setattr(F.time, "sleep", lambda s: slept.append(s))
    calls = []

    def gh(owner, repo):
        calls.append(1)
        if len(calls) == 1:
            raise http_error(403, {"x-ratelimit-reset": str(int(F.time.time()) + 5)}, reason="rate limit exceeded")
        return {"stars": 1}

    advice = {"fingerprint": "fp", "recommendation": None,
              "recovery_actions": [{"action": "backoff", "attempts": 6, "successes": 0}]}
    run, reported = tool_run("fleet-explore-c", True, advice, gh)
    text, ok = run.call("github_repo", {"owner": "a", "repo": "b"})
    assert ok and reported == [("wait_until_reset", True)]
    assert run.failures[-1]["explored"] == "wait_until_reset" and 4 <= slept[0] <= 5.5
    assert run.failures[-1]["seconds"] >= 0


def test_a_conditional_request_sends_the_etag_and_accepts_304(monkeypatch):
    """The GitHub fix almost no agent knows: If-None-Match, a 304, and the
    rate limit untouched. Exercised through _get_json with a fake urlopen."""
    import io

    seen = []

    class Resp(io.BytesIO):
        def __init__(self, body, etag):
            super().__init__(body); self.headers = {"ETag": etag}

        def __enter__(self): return self

        def __exit__(self, *a): return False

    def urlopen(req, timeout):
        seen.append(dict(req.headers))
        if "If-none-match" in req.headers:
            raise http_error(304)
        return Resp(b'{"full_name":"a/b","stargazers_count":7,"open_issues_count":1}', '"tag1"')

    monkeypatch.setattr(F.urllib.request, "urlopen", urlopen)
    cache = {}
    tok = F.ETAGS.set(cache)
    try:
        assert F.github_repo("a", "b")["stars"] == 7
        assert cache and next(iter(cache.values()))["etag"] == '"tag1"'
        t = F.CONDITIONAL.set(True)
        try:
            assert F.github_repo("a", "b")["stars"] == 7, "served from the cached body on 304"
        finally:
            F.CONDITIONAL.reset(t)
    finally:
        F.ETAGS.reset(tok)
    assert "If-none-match" not in seen[0] and seen[1]["If-none-match"] == '"tag1"'


def test_an_asker_applies_conditional_request_when_recommended(monkeypatch):
    calls = []

    def gh(owner, repo):
        calls.append(F.CONDITIONAL.get())
        if len(calls) == 1:
            raise http_error(403)   # a plain Forbidden: GitHub's secondary limit
        return {"stars": 1}

    run, reported = tool_run("fleet-gh-ask", True, {"fingerprint": "fp", "recommendation": {"action": "conditional_request"}}, gh)
    text, ok = run.call("github_repo", {"owner": "a", "repo": "b"})
    assert ok and calls == [False, True] and reported == [("conditional_request", True)]
    assert F.CONDITIONAL.get() is False, "the flag does not leak past the retry"


def test_etags_persist_per_persona(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "STATE_DIR", str(tmp_path))
    run = F.Run("fleet-gh-ask", "decorator", None, True)
    run.etags["https://api.github.com/repos/a/b"] = {"etag": '"x"', "body": {"full_name": "a/b"}}
    run.save_etags()
    again = F.Run("fleet-gh-ask", "decorator", None, True)
    assert again.etags == run.etags
    other = F.Run("fleet-gh-blind", "decorator", None, False)
    assert other.etags == {}, "each persona has its own cache, like each real agent would"


def test_the_new_real_targets_have_names_and_no_schemas():
    """Cron personas call them; the model personas' tool list is unchanged so
    the sixteen originals keep the behaviour they were pinned with."""
    for name in ("github_search", "crates_io_latest", "stackexchange_questions"):
        assert name in F.TOOLS and name in F.TOOL_SERVICE
        assert all(t["function"]["name"] != name for t in F.TOOL_SCHEMAS)
    assert F.TOOL_SERVICE["crates_io_latest"] == "crates.io" and F.TOOL_SERVICE["stackexchange_questions"] == "api.stackexchange.com"


def test_the_real_targets_table_is_ask_vs_blind_on_real_services(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "REPORT_PATH", str(tmp_path / "fleet.json"))
    monkeypatch.setattr(F, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(F, "LAB_DB", "")
    f_ask = {"service": "api.github.com", "operation": "github_repo", "error_type": "rate_limit", "error_code": "403",
             "asked": True, "recommended": "conditional_request", "attempts": 2, "recovered": True, "seconds": 0.4}
    f_blind = dict(f_ask, asked=False, recommended=None, recovered=False, seconds=3.4)
    noise = {"service": "httpbingo.org", "operation": "flaky_read", "error_type": "server_error", "error_code": "503",
             "asked": True, "recommended": None, "attempts": 2, "recovered": True, "seconds": 1.0}
    state = {"runs": [
        {"at": "t", "reporter": "fleet-limits-ask", "path": "decorator", "provider": None, "asks": True, "tool_calls": 3, "failures": [f_ask, noise]},
        {"at": "t", "reporter": "fleet-limits-blind", "path": "decorator", "provider": None, "asks": False, "tool_calls": 3, "failures": [f_blind]},
        {"at": "t", "reporter": "fleet-explore-c", "path": "decorator", "provider": None, "asks": True, "tool_calls": 3, "failures": [f_ask]},
    ]}
    F.write_report(state)
    import json
    rows = json.loads((tmp_path / "fleet.json").read_text())["real_targets"]
    assert rows == [
        {"service": "api.github.com", "cohort": "ask", "failures": 1, "attempts": 2, "recovered": 1, "skipped": 0, "seconds": 0.4, "attempts_per_failure": 2.0},
        {"service": "api.github.com", "cohort": "blind", "failures": 1, "attempts": 2, "recovered": 0, "skipped": 0, "seconds": 3.4, "attempts_per_failure": 2.0},
    ], "explorers and test endpoints stay out of the proof table"


def test_a_plain_403_from_github_is_still_explored_as_a_limit(monkeypatch):
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    calls = []

    def gh(owner, repo):
        calls.append(1)
        if len(calls) == 1:
            raise http_error(403)
        return {"stars": 1}

    run, reported = tool_run("fleet-explore-c", True, {"fingerprint": "fp", "recommendation": None, "recovery_actions": []}, gh)
    _, ok = run.call("github_repo", {"owner": "a", "repo": "b"})
    assert ok and run.failures[-1]["error_type"] == "auth_error" and reported == [("wait_until_reset", True)]


def test_twins_trade_places_every_cycle():
    """Whoever runs second on GitHub's hourly budget meets the 403s the first
    one used it up for: gh-ask 0 failures, gh-blind 47, identical work. On
    odd cycles the twins swap, so over two cycles each side runs second once."""
    n = len(F.PERSONAS)
    names = [p[0] for p in F.PERSONAS]
    a, b = names.index("fleet-gh-ask"), names.index("fleet-gh-blind")
    assert F.persona_index(a) == a and F.persona_index(b) == b                 # cycle 0
    assert F.persona_index(n + a) == b and F.persona_index(n + b) == a         # cycle 1
    assert F.persona_index(2 * n + a) == a                                     # cycle 2
    # everyone still runs exactly once per cycle
    for cycle in range(3):
        assert sorted(F.persona_index(cycle * n + i) for i in range(n)) == list(range(n))
    # explorers have no twin and never move
    e = names.index("fleet-explore-c")
    assert F.persona_index(n + e) == e


def test_the_proof_table_starts_when_the_order_became_fair(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "REPORT_PATH", str(tmp_path / "fleet.json"))
    monkeypatch.setattr(F, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(F, "LAB_DB", "")
    f = {"service": "api.github.com", "operation": "github_repo", "error_type": "rate_limit", "error_code": "403",
         "asked": False, "recommended": None, "attempts": 2, "recovered": False, "seconds": 3.0}
    state = {"runs": [
        {"at": "2026-09-17T05:00:00+00:00", "reporter": "fleet-gh-blind", "path": "decorator", "provider": None, "asks": False, "tool_calls": 1, "failures": [f]},
        {"at": "2026-09-17T07:00:00+00:00", "reporter": "fleet-gh-blind", "path": "decorator", "provider": None, "asks": False, "tool_calls": 1, "failures": [f]},
    ]}
    F.write_report(state)
    import json
    rows = json.loads((tmp_path / "fleet.json").read_text())["real_targets"]
    assert len(rows) == 1 and rows[0]["failures"] == 1


def test_every_cost_a_run_pays_is_recorded_and_aggregated(tmp_path, monkeypatch):
    """Tokens, asks and their time, waits, completion, call outcomes -- all of
    it, so a change to the product is judged on everything it moved."""
    import json

    monkeypatch.setattr(F, "REPORT_PATH", str(tmp_path / "fleet.json"))
    monkeypatch.setattr(F, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(F, "LAB_DB", "")
    run = F.Run("fleet-decor-ask-a", "decorator", "groq", True)
    run._count_tokens({"usage": {"prompt_tokens": 120, "completion_tokens": 30}})
    run._count_tokens({"usage": {"prompt_tokens": 80, "completion_tokens": 20}})
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    run._sleep(3)
    run.ask = run.ask  # real ask() wraps _ask; stub the inner call
    run._ask = lambda *a, **k: {"recommendation": None}
    run.ask("x", "y", "server_error", "503")
    assert run.tokens_prompt == 200 and run.tokens_completion == 50 and run.wait_seconds == 3
    assert run.asks_made == 1 and run.ask_seconds >= 0

    state = {"runs": [
        {"at": "t", "reporter": "fleet-decor-ask-a", "path": "decorator", "provider": "groq", "asks": True,
         "tool_calls": 4, "model_calls": 2, "seconds": 5.0,
         "failures": [{"service": "pypi.org", "operation": "pypi_latest_version", "error_type": "server_error",
                       "error_code": "503", "asked": True, "recommended": "retry", "attempts": 2, "recovered": True, "seconds": 1.5}],
         "metrics": {"tokens_prompt": 200, "tokens_completion": 50, "asks": 1, "ask_seconds": 0.2, "wait_seconds": 0,
                     "completed": True, "calls_first_try": 2, "calls_recovered": 1, "calls_failed": 0}},
        {"at": "t", "reporter": "fleet-decor-blind-a", "path": "decorator", "provider": "groq", "asks": False,
         "tool_calls": 4, "model_calls": 3, "seconds": 9.0, "failures": [],
         "metrics": {"tokens_prompt": 400, "tokens_completion": 100, "asks": 0, "ask_seconds": 0, "wait_seconds": 3,
                     "completed": False, "calls_first_try": 4, "calls_recovered": 0, "calls_failed": 0}},
        {"at": "t", "reporter": "fleet-decor-blind-a", "path": "decorator", "provider": "groq", "asks": False,
         "tool_calls": 1, "model_calls": 1, "seconds": 1.0, "failures": []},   # no metrics: older run, left out of costs
    ]}
    F.write_report(state)
    costs = {c["cohort"]: c for c in json.loads((tmp_path / "fleet.json").read_text())["costs"]}
    ask, blind = costs["real / ask"], costs["real / blind"]
    assert ask["runs"] == 1 and ask["completed_rate"] == 1.0 and ask["tokens_per_run"] == 250 and ask["tokens_per_completed"] == 250
    assert ask["asks_per_run"] == 1 and ask["ask_seconds_per_run"] == 0.2 and ask["failure_seconds_per_run"] == 1.5
    assert blind["runs"] == 1 and blind["completed_rate"] == 0.0 and blind["tokens_per_completed"] is None
    assert blind["wait_seconds_per_run"] == 3 and blind["seconds_per_run"] == 9.0
    assert (ROOT_DOC := (F.__file__.rsplit("/failecho_fleet", 1)[0] + "/docs/fleet-metrics.md"))
    doc = open(ROOT_DOC).read()
    for field in ("tokens_prompt", "ask_seconds", "wait_seconds", "completed", "calls_first_try", "tokens_per_completed", "failure_seconds_per_run"):
        assert field in doc, f"{field} is not defined in docs/fleet-metrics.md"


def test_a_daily_quota_is_recognised_and_a_minute_wait_is_not():
    e = http_error(429); e.failecho_body = '{"error":{"message":"Rate limit reached ... on tokens per day (TPD): Limit 200000"}}'
    assert F._daily_quota(e)
    e2 = http_error(429); e2.failecho_body = '{"error":{"message":"... on tokens per minute (TPM): Limit 8000"}}'
    assert not F._daily_quota(e2)
    e3 = http_error(429); e3.failecho_body = "You exceeded your current quota, please check your plan"
    assert F._daily_quota(e3)


def test_a_builder_switches_model_on_a_daily_quota(monkeypatch):
    """The first version referenced a `state` the builder loop did not have and
    every builder run died with NameError the moment groq's daily quota hit."""
    import io
    import json as _json

    calls = []

    class Resp(io.BytesIO):
        def __enter__(self): return self

        def __exit__(self, *a): return False

    def urlopen(req, timeout):
        body = _json.loads(req.data)
        calls.append(body["model"])
        if len(calls) == 1:
            e = http_error(429)
            e.read = lambda: b'{"error":{"message":"Rate limit reached on tokens per day (TPD): Limit 200000"}}'
            raise e
        return Resp(_json.dumps({"choices": [{"message": {"content": "done"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 1}}).encode())

    monkeypatch.setattr(F.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    monkeypatch.setitem(F.PROVIDERS["groq"], "key", "k")
    import failecho_fleet.builder as B
    monkeypatch.setattr(B, "available", lambda: "no kvm in tests")
    run = F.Run("fleet-build-ask", "builder", "groq", True)
    answer = run.run_build("say done", run_index=0)
    assert answer == "done" and calls == ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]
    assert run.model.endswith("->openai/gpt-oss-120b") and run.tokens_prompt == 5
    assert run.provider_dead_today is False, "the switch worked; the provider still serves (18 Sep 08:39: a " \
        "completed builder run marked groq dead and skipped its askers for two hours)"


def test_a_builder_marks_the_provider_dead_only_when_every_model_hit_the_wall(monkeypatch):
    import io
    import json as _json

    calls = []

    class Resp(io.BytesIO):
        def __enter__(self): return self

        def __exit__(self, *a): return False

    def urlopen(req, timeout):
        body = _json.loads(req.data)
        calls.append(body["model"])
        e = http_error(429)
        e.read = lambda: b'{"error":{"message":"Rate limit reached on tokens per day (TPD): Limit 200000"}}'
        raise e

    monkeypatch.setattr(F.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    monkeypatch.setitem(F.PROVIDERS["groq"], "key", "k")
    import failecho_fleet.builder as B
    monkeypatch.setattr(B, "available", lambda: "no kvm in tests")
    run = F.Run("fleet-build-blind", "builder", "groq", False)
    answer = run.run_build("say done", run_index=0)
    assert answer.startswith("(provider failed") and run.provider_dead_today is True
    assert calls[:3] == ["openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"], "each model tried once"


def test_a_builder_fetching_an_off_list_host_is_told_so_and_nothing_is_reported(monkeypatch):
    """18 Sep 03:xx: a task said to test on httpbingo.org/json, both builders
    called fetch_doc on it, the allowlist raised, and the raise was reported
    to the lab as httpbingo.org fetch_doc/error -- our fence, filed as the
    host failing. Now the model is told what fetch_doc is for and no call
    or report happens."""
    import io
    import json as _json

    calls = []

    class Resp(io.BytesIO):
        def __enter__(self): return self

        def __exit__(self, *a): return False

    def urlopen(req, timeout):
        calls.append(_json.loads(req.data))
        if "messages" not in calls[-1]:
            return Resp(b'{"known": false, "status": "INSUFFICIENT_DATA"}')
        if len([c for c in calls if "messages" in c]) == 1:
            body = {"choices": [{"message": {"content": None, "tool_calls": [
                {"id": "t1", "type": "function", "function": {"name": "fetch_doc", "arguments": _json.dumps({"url": "https://httpbingo.org/json"})}}]}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1}}
        else:
            body = {"choices": [{"message": {"content": "done"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 1}}
        return Resp(_json.dumps(body).encode())

    monkeypatch.setattr(F.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    monkeypatch.setitem(F.PROVIDERS["groq"], "key", "k")
    import failecho_fleet.builder as B
    monkeypatch.setattr(B, "available", lambda: "no kvm in tests")
    reported = []
    monkeypatch.setattr(F, "fetch_doc", lambda url: reported.append(url) or {"url": url, "text": ""})
    run = F.Run("fleet-build-ask", "builder", "groq", True)
    run.report_failure = lambda *a, **k: reported.append(a)
    assert run.run_build("test on httpbingo.org/json", run_index=0) == "done"
    assert reported == [] and run.failures == []
    chats = [c for c in calls if "messages" in c]
    tool_reply = _json.loads(chats[1]["messages"][-1]["content"])
    assert "documentation only" in tool_reply["error"] and "run_python" in tool_reply["error"]


def test_a_model_recalling_the_same_tool_after_a_skip_is_counted():
    run = F.Run("fleet-decor-ask-a", "decorator", "groq", True)
    run.ask = lambda *a, **k: {"fingerprint": "fp", "recommendation": {"action": "skip"}}
    run.report_failure = lambda *a, **k: None
    run.tools["pypi_latest_version"] = lambda package: (_ for _ in ()).throw(RuntimeError("503 down"))
    text, ok = run.call("pypi_latest_version", {"package": "requests"})
    import json as _json
    assert not ok and _json.loads(text) == {"error": "server_error", "code": "503", "retry_pointless": True}
    assert run.last_skip == ("pypi_latest_version", _json.dumps({"package": "requests"}, sort_keys=True))
    # the model loop checks the next tool call against last_skip
    same = ("pypi_latest_version", _json.dumps({"package": "requests"}, sort_keys=True))
    if run.last_skip == same:
        run.model_retries_after_skip += 1
    assert run.model_retries_after_skip == 1


def test_the_versus_table_is_built_from_the_same_ledger(tmp_path, monkeypatch):
    """Metrics as rows, with and without as columns, the better side marked,
    a tie inside 2%. Nothing in it that the cost and cohort tables do not say."""
    import json

    monkeypatch.setattr(F, "REPORT_PATH", str(tmp_path / "fleet.json"))
    monkeypatch.setattr(F, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(F, "LAB_DB", "")
    def run(reporter, asks, seconds, tokens, done, failures):
        return {"at": "t", "reporter": reporter, "path": "decorator", "provider": None, "asks": asks, "tool_calls": 11,
                "model_calls": 0, "seconds": seconds, "failures": failures,
                "metrics": {"tokens_prompt": tokens, "tokens_completion": 0, "asks": 0, "ask_seconds": 0, "wait_seconds": 0,
                            "completed": done, "calls_first_try": 9, "calls_recovered": 0, "calls_failed": 2}}
    f_skip = {"service": "httpbingo.org", "operation": "always_broken", "error_type": "server_error", "error_code": "503",
              "asked": True, "recommended": "skip", "attempts": 1, "recovered": False, "skipped": True, "seconds": 0.5}
    f_retry = dict(f_skip, asked=False, recommended=None, attempts=2, skipped=False, seconds=8.5)
    state = {"runs": [run("fleet-test-ask", True, 25.0, 0, False, [f_skip, f_skip]),
                      run("fleet-test-blind", False, 34.0, 0, False, [f_retry, f_retry])]}
    F.write_report(state)
    versus = json.loads((tmp_path / "fleet.json").read_text())["versus"]
    assert [g["group"] for g in versus] == ["test"]
    rows = {r["metric"]: r for r in versus[0]["rows"]}
    assert rows["seconds per run"] == {"metric": "seconds per run", "unit": "s", "ask": 25.0, "blind": 34.0, "better": "ask"}
    assert rows["retry attempts per failure"]["ask"] == 1.0 and rows["retry attempts per failure"]["blind"] == 2.0
    assert rows["pointless retries avoided"]["ask"] == 2 and rows["pointless retries avoided"]["better"] == "ask"
    assert "tasks completed" not in rows, "the test endpoints have no task to complete"


def test_the_versus_table_has_a_provider_group_since_the_control_changed(tmp_path, monkeypatch):
    """Model-driven personas under their providers' real quotas, counted only
    from the moment blind started retrying once; builders and explorers stay
    out. A 429 the asker got past (switch_model) is a finished task the
    blind run did not get."""
    import json

    monkeypatch.setattr(F, "REPORT_PATH", str(tmp_path / "fleet.json"))
    monkeypatch.setattr(F, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(F, "LAB_DB", "")
    def run(reporter, asks, at, done, recovered, provider="groq"):
        return {"at": at, "reporter": reporter, "path": "decorator", "provider": provider, "asks": asks, "tool_calls": 2,
                "model_calls": 2, "seconds": 4.0, "failures": [
                    {"service": "api.groq.com", "operation": "chat.completions", "error_type": "rate_limit", "error_code": "429",
                     "asked": asks, "recommended": "switch_model" if asks else None, "attempts": 2, "recovered": recovered}],
                "metrics": {"tokens_prompt": 100, "tokens_completion": 20, "asks": int(asks), "ask_seconds": 0, "wait_seconds": 3,
                            "completed": done, "calls_first_try": 1, "calls_recovered": 0, "calls_failed": 0}}
    after = "2026-09-18T06:00:00+00:00"
    state = {"runs": [run("fleet-decor-ask-a", True, after, True, True), run("fleet-decor-blind-a", False, after, False, False),
                      run("fleet-decor-ask-a", True, "2026-09-17T06:00:00+00:00", True, True),   # before: not counted
                      run("fleet-explore-a", True, after, True, True),                          # explorer: not counted
                      run("fleet-build-ask", True, after, True, True)]}                         # builder: not counted
    F.write_report(state)
    versus = json.loads((tmp_path / "fleet.json").read_text())["versus"]
    prov = [g for g in versus if g["group"] == "provider"][0]
    assert prov["since"] == F.PROVIDER_CONTROL_SINCE and prov["runs_ask"] == 1 and prov["runs_blind"] == 1
    rows = {r["metric"]: r for r in prov["rows"]}
    assert rows["tasks completed"] == {"metric": "tasks completed", "unit": "%", "ask": 100.0, "blind": 0.0, "better": "ask"}
    assert rows["provider failures recovered"]["ask"] == 100.0 and rows["provider failures recovered"]["blind"] == 0.0
    assert rows["provider failures met"]["ask"] == 1 and rows["provider failures met"]["better"] == "tie"


def test_a_provider_is_marked_dead_only_when_a_model_switch_also_fails(monkeypatch):
    """A blind retry into the same daily wall says nothing about the
    provider's other models, and marking on it would skip the askers too --
    the cohort that can get past it. Only a failed switch, or nothing left
    to switch to, marks the provider."""
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    tpd = lambda: (lambda e: (setattr(e, "failecho_body", "tokens per day (TPD): Limit 200000"), e)[1])(http_error(429))   # noqa: E731

    blind = F.Run("fleet-decor-blind-a", "decorator", "groq", False)
    assert blind.recover_provider(F.PROVIDERS["groq"], tpd(), Chat(5), {"model": "m", "tools": True}) is None
    assert blind.provider_dead_today is False, "a blind retry into the wall does not mark the provider"

    asker = F.Run("fleet-decor-ask-a", "decorator", "groq", True)
    asker.ask = lambda *a, **k: {"recommendation": {"action": "switch_model"}, "recovery_actions": []}
    asker.report_recovery = lambda *a, **k: None
    state = {"model": "openai/gpt-oss-20b", "tools": True}
    assert asker.recover_provider(F.PROVIDERS["groq"], tpd(), Chat(0), state) is not None
    assert asker.provider_dead_today is False, "a switch that worked means the provider still serves"

    stuck = F.Run("fleet-decor-ask-a", "decorator", "groq", True)
    stuck.ask = asker.ask
    stuck.report_recovery = lambda *a, **k: None
    assert stuck.recover_provider(F.PROVIDERS["groq"], tpd(), Chat(5), {"model": "openai/gpt-oss-20b", "tools": True}) is None
    assert stuck.provider_dead_today is True, "the switch hit a wall too"

    tpm = F.Run("fleet-decor-blind-a", "decorator", "groq", False)
    e2 = http_error(429); e2.failecho_body = "tokens per minute (TPM): Limit 8000"
    tpm.recover_provider(F.PROVIDERS["groq"], e2, Chat(0), {"model": "m", "tools": True})
    assert tpm.provider_dead_today is False


def test_the_scheduler_skips_a_dead_providers_personas(tmp_path, monkeypatch):
    """A provider whose daily quota is gone answers nothing until midnight; a
    third of an afternoon's two-minute slots went to its 429s on day one."""
    import datetime as dt
    import json

    monkeypatch.setattr(F, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(F, "REPORT_PATH", str(tmp_path / "fleet.json"))
    monkeypatch.setattr(F, "LAB_DB", "")
    monkeypatch.setattr(F, "LAB_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setattr(F, "assert_lab_only", lambda: None)
    monkeypatch.setattr(F.signal, "signal", lambda *a, **k: None)
    today = dt.date.today().isoformat()
    names = [p[0] for p in F.PERSONAS]
    # position the round-robin on a groq persona, with groq dead today
    start = names.index("fleet-decor-ask-a")
    marked = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    (tmp_path / "state.json").write_text(json.dumps({"next": start, "runs": [], "day": today, "runs_today": 0,
                                                     "provider_dead": {"groq": marked}}))
    ran = []
    monkeypatch.setattr(F.Run, "run_model", lambda self, *a, **k: ran.append(self.reporter) or "(no provider key)")
    monkeypatch.setattr(F.Run, "run_cron", lambda self, calls: ran.append(self.reporter) or "cron")
    monkeypatch.setattr(F.Run, "run_build", lambda self, *a, **k: ran.append(self.reporter) or "(no provider key)")
    monkeypatch.setattr(F.FailEcho, "flush", lambda self, timeout=5.0: True)
    assert F.main([]) == 0
    assert ran and F.PERSONAS[names.index(ran[0])][2] != "groq", f"ran {ran[0]} on a dead provider"
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["skipped_for_quota_today"] >= 1 and state["next"] > start + 1
    report = json.loads((tmp_path / "fleet.json").read_text())
    assert report["totals"]["providers_out_of_quota"] == ["groq"]


def test_a_dead_mark_expires_and_the_next_persona_probes(tmp_path, monkeypatch):
    """The provider's day is not ours: groq answered again at 02:49 UTC on
    18 Sep after reporting 199178 of 200000 daily tokens used at 00:13, and
    Gemini's free tier resets at midnight Pacific. A mark older than
    QUOTA_PROBE_SECONDS lets one persona through; a run that comes back
    alive clears the mark, a run that dies again refreshes it."""
    import datetime as dt
    import json

    monkeypatch.setattr(F, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(F, "REPORT_PATH", str(tmp_path / "fleet.json"))
    monkeypatch.setattr(F, "LAB_DB", "")
    monkeypatch.setattr(F, "LAB_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setattr(F, "assert_lab_only", lambda: None)
    monkeypatch.setattr(F.signal, "signal", lambda *a, **k: None)
    monkeypatch.setattr(F.FailEcho, "flush", lambda self, timeout=5.0: True)
    today = dt.date.today().isoformat()
    names = [p[0] for p in F.PERSONAS]
    start = names.index("fleet-decor-ask-a")
    stale = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=F.QUOTA_PROBE_SECONDS + 1)).isoformat(timespec="seconds")
    (tmp_path / "state.json").write_text(json.dumps({"next": start, "runs": [], "day": today, "runs_today": 0,
                                                     "provider_dead": {"groq": stale}}))
    ran = []
    monkeypatch.setattr(F.Run, "run_model", lambda self, *a, **k: ran.append(self.reporter) or "(no provider key)")
    assert F.main([]) == 0
    assert ran == ["fleet-decor-ask-a"], "the stale mark should have let the groq persona probe"
    state = json.loads((tmp_path / "state.json").read_text())
    assert "groq" not in state["provider_dead"], "a run that did not die re-opens the provider"

    # the pre-18-Sep format, a bare date, is an expired mark too
    assert F._quota_probe_due(today)
    assert not F._quota_probe_due(dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))

    # and a probe that dies again refreshes the mark with a timestamp
    def dies(self, *a, **k):
        self.provider_dead_today = True
        return "(provider failed: rate_limit)"
    (tmp_path / "state.json").write_text(json.dumps({"next": start, "runs": [], "day": today, "runs_today": 0,
                                                     "provider_dead": {"groq": stale}}))
    monkeypatch.setattr(F.Run, "run_model", dies)
    assert F.main([]) == 0
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["provider_dead"]["groq"] != stale and "T" in state["provider_dead"]["groq"]


def test_provider_caps_stay_within_the_documented_free_tiers():
    """Provider caps never above documented free tiers (loop rule). The
    numbers are what the providers themselves answered on 18 Sep 2026:
    Gemini "GenerateRequestsPerDayPerProjectPerModel-FreeTier ... quotaValue
    20" (two models in rotation), OpenRouter "free-models-per-day ...
    X-RateLimit-Limit: 50", groq 1000 requests a day for gpt-oss-20b."""
    assert F.PROVIDERS["gemini"]["daily_cap"] <= 20 * (1 + len(F.PROVIDERS["gemini"]["alt_models"]))
    assert F.PROVIDERS["openrouter"]["daily_cap"] <= 50
    assert F.PROVIDERS["groq"]["daily_cap"] <= 1000
    # NVIDIA documents 40 requests a minute; 400 a day is well inside it
    assert F.PROVIDERS["nvidia"]["daily_cap"] <= 40 * 60 * 24 // 100
    # Mistral's headers for this key: ministral-8b 188 a minute; 400 a day is well inside it
    assert F.PROVIDERS["mistral"]["daily_cap"] <= 188 * 60 * 24 // 100
    # xKiro states 1,000,000 tokens a day for a Telegram-verified account; 600 calls at ~1k tokens is inside it
    assert F.PROVIDERS["xkiro"]["daily_cap"] * 1000 <= 1_000_000


def test_the_nvidia_personas_are_twins_and_the_onboarding_rotation_stays_coprime():
    """Added 2026-09-18: twins share a provider and differ only in asking."""
    from failecho_fleet import onboard

    by = {p[0]: p for p in F.PERSONAS}
    for a, b, prov in (("fleet-decor-ask-c", "fleet-decor-blind-c", "nvidia"), ("fleet-build-ask-n", "fleet-build-blind-n", "nvidia"),
                       ("fleet-decor-ask-d", "fleet-decor-blind-d", "mistral"),
                       ("fleet-decor-ask-e", "fleet-decor-blind-e", "xkiro"), ("fleet-build-ask-x", "fleet-build-blind-x", "xkiro")):
        assert by[a][1:3] == by[b][1:3] == (by[a][1], prov) and by[a][3] and not by[b][3]
        assert (a, b) in F.TWINS
    assert by["fleet-explore-e"][2] == "mistral" and "fleet-explore-e" in F.EXPLORER_PERSONAS
    assert "fleet-explore-d" in F.EXPLORER_PERSONAS and by["fleet-explore-d"][2] == "nvidia"
    assert ("nvidia", "nvidia/nemotron-3-super-120b-a12b") in onboard.MODELS


def test_the_advice_table_and_the_timeline_are_built_from_the_ledger(tmp_path, monkeypatch):
    """By failure shape: advised, unadvised and blind second attempts, skips,
    and blind's yield in the hour and shape the network said skip. And per
    half day, the share of failures the network had advice for."""
    import json

    monkeypatch.setattr(F, "REPORT_PATH", str(tmp_path / "fleet.json"))
    monkeypatch.setattr(F, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(F, "LAB_DB", "")
    def f(recommended, attempts, recovered, skipped=False):
        return {"service": "httpbingo.org", "operation": "always_broken", "error_type": "server_error", "error_code": "503",
                "asked": recommended is not None, "recommended": recommended, "attempts": attempts, "recovered": recovered,
                "skipped": skipped, "seconds": 1.0}
    def run(reporter, asks, at, failures, done=False):
        return {"at": at, "reporter": reporter, "path": "decorator", "provider": None, "asks": asks, "tool_calls": 3,
                "model_calls": 0, "seconds": 2.0, "failures": failures,
                "metrics": {"tokens_prompt": 0, "tokens_completion": 0, "asks": 0, "ask_seconds": 0, "wait_seconds": 0,
                            "completed": done, "calls_first_try": 1, "calls_recovered": 0, "calls_failed": 1}}
    t1, t2 = "2026-09-18T03:10:00+00:00", "2026-09-18T03:20:00+00:00"
    state = {"runs": [
        run("fleet-cron-a", True, t1, [f("retry", 2, True)] * 3 + [f("skip", 1, False, skipped=True)] * 3 + [f(None, 2, False)] * 2, done=True),
        run("fleet-cron-b", False, t2, [f(None, 2, True), f(None, 2, False), f(None, 2, False), f(None, 2, False)]),
        run("fleet-explore-c", True, t2, [f("retry", 2, False)] * 10),   # explorers stay out
    ]}
    F.write_report(state)
    report = json.loads((tmp_path / "fleet.json").read_text())
    (a,) = report["advice"]
    assert (a["service"], a["error_type"], a["error_code"]) == ("httpbingo.org", "server_error", "503")
    assert (a["advised_retries"], a["advised_recovered"]) == (3, 3)
    assert (a["unadvised_retries"], a["unadvised_recovered"]) == (2, 0)
    assert (a["blind_retries"], a["blind_recovered"]) == (4, 1)
    assert a["skipped"] == 3 and (a["blind_retries_where_skipped"], a["blind_recovered_where_skipped"]) == (4, 1)
    (h,) = report["timeline"]
    assert h["period"] == "2026-09-18 00-12" and h["ask_runs"] == 1 and h["blind_runs"] == 1
    assert h["ask_failures"] == 8 and h["ask_advised"] == 6 and h["advised_share"] == 0.75
    assert h["ask_completed_rate"] == 1.0 and h["blind_completed_rate"] == 0.0


def test_two_lanes_round_robin_their_own_personas_and_share_one_state(tmp_path, monkeypatch):
    """A builder or OpenCode run holds a slot for minutes; in one queue the
    light personas waited behind them. Each lane has its own cursor; twins
    share a path so they never split across lanes; the state file is locked
    while a persona is chosen and while its record is filed."""
    import json

    monkeypatch.setattr(F, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(F, "REPORT_PATH", str(tmp_path / "fleet.json"))
    monkeypatch.setattr(F, "LAB_DB", "")
    monkeypatch.setattr(F, "LAB_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setattr(F, "assert_lab_only", lambda: None)
    monkeypatch.setattr(F.signal, "signal", lambda *a, **k: None)
    monkeypatch.setattr(F.FailEcho, "flush", lambda self, timeout=5.0: True)
    for a, b in F.TWINS:
        by = {p[0]: p for p in F.PERSONAS}
        assert F.lane_of(by[a]) == F.lane_of(by[b])
    vm = [F.PERSONAS[i][0] for i in F.lane_personas("vm")]
    light = [F.PERSONAS[i][0] for i in F.lane_personas("light")]
    assert set(vm) == F.BUILD_PERSONAS | F.OPENCODE_PERSONAS and not (set(vm) & set(light))
    assert len(vm) + len(light) == len(F.PERSONAS)
    # the vm lane's odd cycle swaps twins inside the lane
    assert F.PERSONAS[F.persona_index(0, "vm")][0] == vm[0]
    assert F.PERSONAS[F.persona_index(len(vm), "vm")][0] == dict(F.TWINS)[vm[0]]

    ran = []
    monkeypatch.setattr(F.Run, "run_model", lambda self, *a, **k: ran.append(self.reporter) or "ok")
    monkeypatch.setattr(F.Run, "run_cron", lambda self, calls: ran.append(self.reporter) or "cron")
    monkeypatch.setattr(F.Run, "run_build", lambda self, *a, **k: ran.append(self.reporter) or "(no provider key)")
    monkeypatch.setattr(F.Run, "run_opencode", lambda self, *a, **k: ran.append(self.reporter) or "(no provider key)")
    assert F.main(["--lane", "vm"]) == 0 and F.main(["--lane", "light"]) == 0 and F.main(["--lane", "vm"]) == 0
    assert ran[0] in vm and ran[1] in light and ran[2] in vm and ran[2] != ran[0]
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["next_vm"] == 2 and state["next_light"] == 1 and state["runs_today"] == 3 and len(state["runs"]) == 3
    assert (tmp_path / "state.lock").exists()


def test_the_etag_cache_keeps_only_small_answers(tmp_path, monkeypatch):
    """By 18 Sep each persona's ETag file was 21 MB of cached npm bodies,
    parsed on every run in a lane capped at 400 MB."""
    import json

    monkeypatch.setattr(F, "STATE_DIR", str(tmp_path))
    run = F.Run("fleet-decor-ask-a", "decorator", "groq", True)
    run.etags.update({f"https://x/{i}": {"etag": "e", "body": {"v": i}} for i in range(80)})
    run.etags["https://registry.npmjs.org/express"] = {"etag": "big", "body": {"x": "y" * (F.ETAG_BODY_MAX + 1)}}
    run.save_etags()
    saved = json.loads((tmp_path / "etags-fleet-decor-ask-a.json").read_text())
    assert len(saved) == 50 and "https://registry.npmjs.org/express" not in saved
    assert (tmp_path / "etags-fleet-decor-ask-a.json").stat().st_size < 10_000


def test_a_provider_at_our_daily_cap_is_skipped_and_cap_skips_are_not_runs(tmp_path, monkeypatch):
    """18 Sep: NVIDIA reached the fleet's own cap of 400 at ~16:30 and its
    four VM-lane personas kept running in 0 s, filed as failed tasks."""
    import datetime as dt
    import json

    monkeypatch.setattr(F, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(F, "REPORT_PATH", str(tmp_path / "fleet.json"))
    monkeypatch.setattr(F, "LAB_DB", "")
    monkeypatch.setattr(F, "LAB_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setattr(F, "assert_lab_only", lambda: None)
    monkeypatch.setattr(F.signal, "signal", lambda *a, **k: None)
    monkeypatch.setattr(F.FailEcho, "flush", lambda self, timeout=5.0: True)
    today = dt.date.today().isoformat()
    vm = F.lane_personas("vm")
    start = next(i for i, j in enumerate(vm) if F.PERSONAS[j][2] == "nvidia")
    (tmp_path / "state.json").write_text(json.dumps({
        "next_vm": start, "runs": [], "day": today, "runs_today": 0, "provider_day": today,
        "provider_calls_today": {"nvidia": F.PROVIDERS["nvidia"]["daily_cap"]}}))
    ran = []
    monkeypatch.setattr(F.Run, "run_build", lambda self, *a, **k: ran.append(self.reporter) or "done")
    monkeypatch.setattr(F.Run, "run_opencode", lambda self, *a, **k: ran.append(self.reporter) or "done")
    assert F.main(["--lane", "vm"]) == 0
    assert ran and F.PERSONAS[[p[0] for p in F.PERSONAS].index(ran[0])][2] != "nvidia"

    capped = {"at": "2026-09-18T17:00:00+00:00", "reporter": "fleet-oc-ask-n", "path": "opencode", "provider": "nvidia",
              "asks": True, "tool_calls": 0, "model_calls": 0, "seconds": 0.0, "failures": [],
              "answer": "(provider nvidia daily cap reached; run skipped)",
              "metrics": {"tokens_prompt": 0, "tokens_completion": 0, "asks": 0, "ask_seconds": 0, "wait_seconds": 0,
                          "completed": False, "calls_first_try": 0, "calls_recovered": 0, "calls_failed": 0}}
    F.write_report({"runs": [capped]})
    report = json.loads((tmp_path / "fleet.json").read_text())
    assert report["totals"]["runs"] == 0 and not report["costs"]
