"""The OpenCode personas: a real agent product driven headless in the VM,
with FailEcho's MCP server in its config (ask) or without (blind)."""

from __future__ import annotations

import json

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
    assert "check_tool_failure" in extra and "BEFORE retrying" in extra and "metadata only" in extra


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
