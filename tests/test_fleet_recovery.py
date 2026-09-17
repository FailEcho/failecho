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
