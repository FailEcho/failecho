"""The OpenCode personas: a real agent product driven headless in the VM,
with FailEcho's MCP server in its config (ask) or without (blind)."""

from __future__ import annotations

import json
from pathlib import Path

import failecho_fleet as F
from failecho_fleet import opencode as OC

# three events as `opencode run --format json` printed them on 18 Sep
SAMPLE = [
    {"type": "step_start", "part": {"type": "step-start"}},
    {"type": "tool_use", "part": {"type": "tool", "tool": "bash", "state": {"status": "completed", "input": {"command": "ls"}}}},
    {"type": "tool_use", "part": {"type": "tool", "tool": "failecho_check_tool_failure",
                                  "state": {"status": "completed", "input": {"service": "api.github.com", "operation": "GET /repos"}}}},
    {"type": "step_finish", "part": {"type": "step-finish", "reason": "tool-calls",
                                     "tokens": {"total": 7678, "input": 7608, "output": 70, "reasoning": 0}}},
    {"type": "text", "part": {"type": "text", "text": "done"}},
    {"type": "error", "error": {"name": "APIError", "data": {"message": "Forbidden", "statusCode": 403}}},
]


def test_events_are_reduced_to_counts():
    ev = OC.Events()
    for e in SAMPLE:
        ev.feed(json.dumps(e))
    ev.feed("not json")
    assert ev.tool_calls == 2 and ev.failecho_calls == 1 and ev.tool_names == {"bash": 1, "failecho_check_tool_failure": 1}
    assert ev.steps == 1 and (ev.tokens_in, ev.tokens_out) == (7608, 70)
    assert ev.text == ["done"] and ev.errors and "Forbidden" in ev.errors[0]


def test_the_ask_config_carries_the_mcp_server_and_the_blind_one_does_not():
    ask = OC.opencode_config("nvidia", "nvidia/nemotron-3-super-120b-a12b", "https://lab.failecho.com/mcp", "fleet-oc-ask-n")
    blind = OC.opencode_config("nvidia", "nvidia/nemotron-3-super-120b-a12b", None, "fleet-oc-blind-n")
    assert ask["mcp"]["failecho"] == {"type": "remote", "url": "https://lab.failecho.com/mcp", "enabled": True,
                                      "headers": {"X-Reporter-ID": "fleet-oc-ask-n"}}
    assert "mcp" not in blind
    for cfg in (ask, blind):
        # the same model for the side-calls, or OpenCode reaches for a paid default
        assert cfg["model"] == cfg["small_model"] == "nvidia/nvidia/nemotron-3-super-120b-a12b"
        assert cfg["provider"]["nvidia"]["options"] == {"apiKey": "{env:NVIDIA_API_KEY}", "baseURL": "https://integrate.api.nvidia.com/v1"}
        assert cfg["provider"]["nvidia"]["npm"] == "@ai-sdk/openai-compatible"
        assert cfg["share"] == "disabled" and cfg["autoupdate"] is False
        assert cfg["permission"]["external_directory"] == "allow", "a /tmp write must not be auto-rejected mid-task"
    zen = OC.opencode_config("zen", "nemotron-3-ultra-free", None, "x")
    assert zen["model"] == "opencode/nemotron-3-ultra-free" and "npm" not in zen["provider"]["opencode"]
    assert "{env:ZEN_API_KEY}" in json.dumps(zen)


def test_no_key_ever_lands_in_the_config_file():
    cfg = json.dumps(OC.opencode_config("xkiro", "qwen/qwen3.6-27b:free", None, "x"))
    assert "sk-" not in cfg and "{env:XKIRO_API_KEY}" in cfg


def test_agents_md_differs_only_by_the_failecho_paragraph():
    assert OC.AGENTS_MD_FAILECHO.startswith(OC.AGENTS_MD)
    extra = OC.AGENTS_MD_FAILECHO[len(OC.AGENTS_MD):]
    assert "check_tool_failure" in extra
    # a rule, not a suggestion (22 Sep): the polite version got 0.12 calls a run
    assert "Never retry" in extra and "not optional" in extra
    assert "report_recovery_outcome" in extra and "Metadata only" in extra


def test_every_task_ends_in_a_checkable_file():
    for prompt, check in OC.OC_TASKS:
        assert "result.json" in prompt or "result.txt" in prompt
        assert check


def test_the_opencode_twins_share_provider_and_task_and_the_run_is_recorded(tmp_path, monkeypatch):
    by = {p[0]: p for p in F.PERSONAS}
    a, b = by["fleet-oc-ask-n"], by["fleet-oc-blind-n"]
    assert a[1:3] == b[1:3] == ("opencode", "nvidia") and a[3] and not b[3] and a[4] is b[4] is OC.OC_TASKS
    assert ("fleet-oc-ask-n", "fleet-oc-blind-n") in F.TWINS and F.OPENCODE_PERSONAS == {"fleet-oc-ask-n", "fleet-oc-blind-n"}
    assert set(F.OPENCODE_MODELS) <= set(OC.OC_PROVIDERS)

    seen = {}

    def fake(**kw):
        seen.update(kw)
        return {"completed": True, "tool_calls": 3, "failecho_calls": 1, "steps": 8, "tokens_in": 36000, "tokens_out": 600,
                "seconds": 150.0, "answer": "wrote result.json", "error": None, "tool_names": {"bash": 2, "write": 1},
                "exit": 0, "result_head": "{}", "timed_out": False}

    monkeypatch.setattr(F, "run_opencode", fake)
    monkeypatch.setitem(F.PROVIDERS["nvidia"], "key", "k")
    run = F.Run("fleet-oc-ask-n", "opencode", "nvidia", True)
    answer = run.run_opencode(OC.OC_TASKS[0], run_index=0)
    assert answer == "wrote result.json"
    assert seen["asks"] is True and seen["provider"] == "nvidia" and seen["model"] == F.OPENCODE_MODELS["nvidia"]
    assert (run.model_calls, run.tool_calls, run.asks_made, run.tokens_prompt) == (8, 3, 1, 36000)
    assert run.opencode["completed"] and run.failures == []


def test_the_versus_table_has_an_opencode_group(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "REPORT_PATH", str(tmp_path / "fleet.json"))
    monkeypatch.setattr(F, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(F, "LAB_DB", "")

    def run(reporter, asks, done, steps, asks_made):
        return {"at": "2026-09-18T13:00:00+00:00", "reporter": reporter, "path": "opencode", "provider": "nvidia", "asks": asks,
                "tool_calls": 3, "model_calls": steps, "seconds": 150.0, "failures": [],
                "opencode": {"completed": done, "steps": steps, "failecho_calls": asks_made},
                "metrics": {"tokens_prompt": 30000, "tokens_completion": 500, "asks": asks_made, "ask_seconds": 0, "wait_seconds": 0,
                            "completed": done, "calls_first_try": 3, "calls_recovered": 0, "calls_failed": 0}}
    F.write_report({"runs": [run("fleet-oc-ask-n", True, True, 8, 1), run("fleet-oc-blind-n", False, False, 12, 0)]})
    report = json.loads((tmp_path / "fleet.json").read_text())
    (g,) = [g for g in report["versus"] if g["group"] == "opencode"]
    rows = {r["metric"]: r for r in g["rows"]}
    assert rows["runs marked completed (see grading)"]["ask"] == 100.0 and rows["runs marked completed (see grading)"]["blind"] == 0.0
    assert rows["model steps per run"] == {"metric": "model steps per run", "unit": "", "ask": 8.0, "blind": 12.0, "better": "ask"}
    assert rows["FailEcho tool calls per run"]["ask"] == 1.0 and rows["FailEcho tool calls per run"]["blind"] == 0.0
    assert not [g for g in report["versus"] if g["group"] == "provider"], "OpenCode runs stay out of the provider group"
    assert "opencode / ask" in {c["cohort"] for c in report["costs"]}


def test_both_shapes_of_the_advice_line_are_counted():
    """The upstream-fallback line starts "FailEcho (evidence from ...)", not
    "FailEcho: ", and matching only the latter counted zero while the proxy
    was annotating every GitHub 403 (22 Sep)."""
    from failecho_fleet.opencode import Events

    for line in ("FailEcho: try backoff, worked 128/251 (evidence score 0.61).",
                 "FailEcho (evidence from api.github.com): no clear fix yet; ..."):
        ev = Events()
        ev.feed('{"type": "tool", "tool": "packages_github_stars", "state": "done", '
                '"output": "HTTP 403 rate limit exceeded\\n' + line + '"}')
        assert ev.advice_seen == 1, line
    ev = Events()
    ev.feed('{"type": "tool", "tool": "packages_pypi_latest", "state": "done", "output": "{\\"version\\": \\"1.0\\"}"}')
    assert ev.advice_seen == 0


def test_the_opencode_group_splits_on_the_stricter_rules(tmp_path, monkeypatch):
    """The instruction became a rule on 22 Sep; a group average that mixes
    both sides of that change answers nothing."""
    import json

    import failecho_fleet as F

    monkeypatch.setattr(F, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(F, "REPORT_PATH", str(tmp_path / "fleet.json"))
    monkeypatch.setattr(F, "LAB_DB", "")

    def run(at, reporter, asks, asks_made, completed):
        return {"at": at, "reporter": reporter, "path": "opencode", "provider": "nvidia", "asks": asks,
                "tool_calls": 1, "model_calls": 3, "seconds": 20.0, "failures": [],
                "metrics": {"asks": asks_made, "completed": completed, "tokens_prompt": 10,
                            "tokens_completion": 1, "ask_seconds": 0.0, "wait_seconds": 0.0,
                            "calls_first_try": 1, "calls_recovered": 0, "calls_failed": 0,
                            "model_retries_after_skip": 0},
                "opencode": {"completed": completed, "steps": 3}}

    before, after = "2026-09-22T10:00:00", "2026-09-22T19:00:00"
    state = {"runs": [run(before, "fleet-oc-ask-n", True, 0, True),
                      run(before, "fleet-oc-blind-n", False, 0, True),
                      run(after, "fleet-oc-ask-n", True, 2, True),
                      run(after, "fleet-oc-ask-n", True, 4, False),
                      run(after, "fleet-oc-blind-n", False, 0, True)]}
    F.write_report(state)
    versus = {g["group"]: {r["metric"]: r for r in g["rows"]}
              for g in json.loads((tmp_path / "fleet.json").read_text())["versus"]}
    rows = versus["opencode"]
    assert rows["runs since the stricter rules"]["ask"] == 2
    assert rows["FailEcho tool calls per run, since the rules"]["ask"] == 3.0, "2 and 4 calls -> 3.0"
    assert rows["FailEcho tool calls per run"]["ask"] == 2.0, "the all-time average keeps the polite runs"
    assert rows["runs marked completed, since the rules"]["ask"] == 50.0


# -- the hook pair (2026-09-23) -------------------------------------------------


def test_the_hook_pair_runs_the_same_tasks_as_the_mcp_pair():
    """The two arms answer the same question from opposite ends -- ask the
    model to call a tool, or hook every tool it already calls -- so they have
    to be asked the same things."""
    import failecho_fleet as F
    from failecho_fleet.opencode import OC_TASKS

    by_name = {p[0]: p for p in F.PERSONAS}
    for name in F.OCHOOK_PERSONAS:
        assert by_name[name][1] == "opencode"
        assert by_name[name][4] is OC_TASKS, f"{name} must run the MCP pair's tasks"
    assert by_name["fleet-och-ask-n"][3] is True and by_name["fleet-och-blind-n"][3] is False


def test_only_the_asking_twin_gets_the_plugin_and_the_endpoint():
    """The blind twin must be plain OpenCode: no plugin file, and no endpoint
    in its environment. A twin that reports is not a control."""
    from failecho_fleet.opencode import guest_env

    asking = guest_env("nvidia", "k", "fleet-och-ask-n", True, "https://lab.failecho.com")
    blind = guest_env("nvidia", "k", "fleet-och-blind-n", False, "https://lab.failecho.com")
    assert asking["FAILECHO_ENDPOINT"] == "https://lab.failecho.com"
    assert asking["FAILECHO_REPORTER_ID"] == "fleet-och-ask-n"
    assert "FAILECHO_ENDPOINT" not in blind and "FAILECHO_REPORTER_ID" not in blind


def test_the_guest_never_falls_back_to_production():
    """The plugin's default endpoint is failecho.com, and a guest started with
    a minimal environment is exactly how eight rows of test traffic reached
    production on 19 Sep. With no lab to point at, it gets a dead port."""
    from failecho_fleet.opencode import guest_env

    env = guest_env("nvidia", "k", "fleet-och-ask-n", True, None)
    assert env["FAILECHO_ENDPOINT"] == "http://127.0.0.1:9"
    assert "failecho.com" not in json.dumps(env)


def test_the_lab_installs_the_plugin_it_ships():
    """No second copy: the file the fleet puts in the guest is the file in the
    npm package, or the experiment tests something nobody can install."""
    from failecho_fleet.opencode import PLUGIN_FILE

    shipped = Path(__file__).resolve().parents[1] / "opencode-plugin" / "plugin" / "failecho.js"
    assert PLUGIN_FILE == shipped and PLUGIN_FILE.exists()
    assert "tool.execute.after" in PLUGIN_FILE.read_text()


def test_the_hook_group_is_on_the_scoreboard(tmp_path, monkeypatch):
    import failecho_fleet as F

    monkeypatch.setattr(F, "REPORT_PATH", str(tmp_path / "fleet.json"))
    monkeypatch.setattr(F, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(F, "LAB_DB", "")

    def run(reporter, asks, advice_seen):
        return {"at": "2026-09-23T01:00:00", "reporter": reporter, "path": "opencode", "provider": "nvidia",
                "asks": asks, "tool_calls": 9, "model_calls": 5, "seconds": 200.0, "failures": [],
                "opencode": {"completed": True, "tool_calls": 9, "failecho_calls": 0,
                             "advice_seen": advice_seen, "guest_oom_kills": 0, "steps": 5},
                "metrics": {"tokens_prompt": 40000, "tokens_completion": 900, "asks": advice_seen,
                            "ask_seconds": 0, "wait_seconds": 0, "completed": True,
                            "calls_first_try": 9, "calls_recovered": 0, "calls_failed": 0}}

    F.write_report({"runs": [run("fleet-och-ask-n", True, 2), run("fleet-och-blind-n", False, 0)]})
    groups = {g["group"]: g for g in json.loads((tmp_path / "fleet.json").read_text())["versus"]}
    assert "ochook" in groups, "the fourth arm has to be readable next to the other three"
    rows = {r["metric"]: r for r in groups["ochook"]["rows"]}
    assert rows["tool outputs carrying advice, per run"]["ask"] == 2.0
    assert rows["tool outputs carrying advice, per run"]["blind"] == 0.0


# -- tool failures inside OpenCode (2026-09-23) ---------------------------------


def test_a_failing_tool_inside_opencode_is_counted():
    """Until 23 Sep the only failures on an OpenCode run's record were the
    provider's, so "the run met no failure" could not be told apart from "the
    agent met a 403 and ignored the rules". `curl -s` exits 0 on a 429, so a
    network tool's output is read for the status as well."""
    ev = OC.Events()
    for state in (
        {"status": "completed", "output": "HTTP/2 403\nrate limit exceeded", "metadata": {"exit": 0}},
        {"status": "completed", "output": "done", "metadata": {"exit": 2}},
        {"status": "error", "output": "boom"},
        {"status": "completed", "output": '{"version": "2.34.2"}', "metadata": {"exit": 0}},
    ):
        ev.feed(json.dumps({"type": "tool", "tool": "bash", "state": state}))
    assert (ev.tool_calls, ev.tool_failures) == (4, 3)


def test_a_failecho_call_is_not_a_tool_failure():
    ev = OC.Events()
    ev.feed(json.dumps({"type": "tool", "tool": "failecho_check_tool_failure",
                        "state": {"status": "completed", "output": "status: 429 known"}}))
    assert ev.failecho_calls == 1 and ev.tool_failures == 0


def test_the_scoreboard_separates_runs_that_met_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "REPORT_PATH", str(tmp_path / "fleet.json"))
    monkeypatch.setattr(F, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(F, "LAB_DB", "")

    def run(asks, failures, calls):
        return {"at": "2026-09-23T05:00:00", "reporter": "fleet-oc-ask-n" if asks else "fleet-oc-blind-n",
                "path": "opencode", "provider": "nvidia", "asks": asks, "tool_calls": 9, "model_calls": 5,
                "seconds": 200.0, "failures": [],
                "opencode": {"completed": True, "tool_calls": 9, "failecho_calls": calls, "advice_seen": 0,
                             "tool_failures": failures, "guest_oom_kills": 0, "steps": 5},
                "metrics": {"tokens_prompt": 40000, "tokens_completion": 900, "asks": calls,
                            "ask_seconds": 0, "wait_seconds": 0, "completed": True,
                            "calls_first_try": 9, "calls_recovered": 0, "calls_failed": 0}}

    F.write_report({"runs": [run(True, 2, 1), run(True, 0, 0), run(False, 1, 0)]})
    rows = {r["metric"]: r for g in json.loads((tmp_path / "fleet.json").read_text())["versus"]
            if g["group"] == "opencode" for r in g["rows"]}
    assert rows["runs that met a failing tool"]["ask"] == 1
    assert rows["runs that met a failing tool"]["blind"] == 1
    assert rows["FailEcho calls or advice lines, per run that met one"]["ask"] == 1.0


def test_runs_recorded_before_the_counter_existed_are_not_read_as_clean():
    """An old record has no tool_failures field at all. That is "unknown", not
    "met nothing" -- the mistake this counter exists to stop."""
    import inspect

    source = inspect.getsource(F.write_report)
    assert '"tool_failures" in (r.get("opencode") or {})' in source
