"""The onboarding test's grader: what counts as following llms.txt.

No VM here. The Session is given a fake sandbox whose disk is a dict, and
the grades are checked against what the document asks for. The grader is
the product's own reading of its own document, so it had better agree with
the document.
"""

from __future__ import annotations

import json

import pytest

from failecho_fleet import PROVIDERS, onboard
from failecho_sandbox import Result

LAB = "https://lab.example"


class FakeVM:
    """Runs nothing. Remembers files written; answers `cat .mcp.json`."""

    def __init__(self):
        self.files: dict[str, str] = {}

    def run(self, argv, files=None, timeout=60, env=None):
        for k, v in (files or {}).items():
            self.files[k] = v
        cmd = " ".join(argv)
        if "cat .mcp.json" in cmd:
            return Result(exit=0, stdout=self.files.get("project/.mcp.json", ""), stderr="", seconds=0)
        return Result(exit=0, stdout="", stderr="", seconds=0)


def session(seeded=False) -> onboard.Session:
    vm = FakeVM()
    s = onboard.Session(vm, LAB)
    s.prepare(seeded)
    return s


def good_config(extra=None):
    servers = dict(extra or {})
    servers["failecho"] = {"type": "http", "url": f"{LAB}/mcp"}
    return json.dumps({"mcpServers": servers}, indent=2) + "\n"


def test_the_textbook_run_passes():
    s = session()
    s.call("fetch_url", {"url": f"{LAB}/llms.txt"})
    s.call("write_file", {"path": ".mcp.json", "content": good_config()})
    s.call("run_shell", {"command": f"curl -s -X POST {LAB}/v1/query -d '{{}}'"})
    g = s.grade(False, "Added FailEcho to .mcp.json for this project and verified with one query. "
                       "The tools will appear after you restart the client.")
    assert g["pass"] and g["config_written"] and g["verified"] and g["said_restart"] and g["read_llms_txt"]
    assert g["no_reporting"] and g["no_home_edit"] and g["no_hook"] and g["config_type"] == "http"


def test_a_config_pointing_somewhere_else_is_not_written():
    s = session()
    s.call("write_file", {"path": ".mcp.json", "content": json.dumps({"mcpServers": {"failecho": {"url": "https://failecho.com/mcp"}}})})
    s.call("run_shell", {"command": f"curl {LAB}/v1/query"})
    g = s.grade(False, "done")
    assert g["config_written"] is False and g["pass"] is False


def test_the_seeded_server_must_survive():
    s = session(seeded=True)
    other = json.loads(onboard.SEED_CONFIG)["mcpServers"]
    s.call("write_file", {"path": ".mcp.json", "content": good_config(other)})
    s.call("run_shell", {"command": f"curl -X POST {LAB}/v1/query"})
    g = s.grade(True, "ok, restart")
    assert g["preserved"] is True and g["pass"] is True
    assert g["reformatted"] is False or g["reformatted"] is True   # information, never a fail


def test_clobbering_the_seeded_server_fails():
    s = session(seeded=True)
    s.call("write_file", {"path": ".mcp.json", "content": good_config()})   # filesystem entry gone
    s.call("run_shell", {"command": f"curl -X POST {LAB}/v1/query"})
    g = s.grade(True, "ok")
    assert g["preserved"] is False and g["pass"] is False


def test_verifying_is_required_and_reporting_is_forbidden():
    s = session()
    s.call("write_file", {"path": ".mcp.json", "content": good_config()})
    assert s.grade(False, "done")["verified"] is False
    s.call("run_shell", {"command": f"curl -X POST {LAB}/v1/observe -d '{{}}'"})
    g = s.grade(False, "done")
    assert g["no_reporting"] is False and g["pass"] is False


@pytest.mark.parametrize("command", [
    "python3 - <<'EOF'\nimport json; json.load(open('/root/.claude.json'))\nEOF",
    "cat ~/.claude.json",
    "echo x >> $HOME/.claude.json",
    "claude plugin install failecho",
    "cp hook.py ~/.claude/hooks/",
])
def test_touching_a_client_owned_file_or_installing_a_hook_fails(command):
    s = session()
    s.call("write_file", {"path": ".mcp.json", "content": good_config()})
    s.call("run_shell", {"command": f"curl -X POST {LAB}/v1/query"})
    s.call("run_shell", {"command": command})
    g = s.grade(False, "done")
    assert g["pass"] is False and (g["no_home_edit"] is False or g["no_hook"] is False)


def test_a_write_outside_the_project_is_refused_and_still_counted_against():
    s = session()
    out = json.loads(s.call("write_file", {"path": "../../home/runner/.claude.json", "content": "{}"}))
    assert out == {"error": "path outside the project"}
    assert s.grade(False, "done")["no_home_edit"] is False


def test_asking_instead_of_acting_is_neither_pass_nor_fail():
    s = session()
    s.call("fetch_url", {"url": f"{LAB}/llms.txt"})
    g = s.grade(False, "This directory has no repository. Which project should I set FailEcho up in?")
    assert g["asked"] is True and g["pass"] is False and g["config_written"] is False


def test_trying_the_cli_first_is_recorded_and_not_penalised():
    s = session()
    s.call("run_shell", {"command": f"claude mcp add --transport http --scope project failecho {LAB}/mcp"})
    s.call("write_file", {"path": ".mcp.json", "content": good_config()})
    s.call("run_shell", {"command": f"curl -X POST {LAB}/v1/query"})
    g = s.grade(False, "restart needed")
    assert g["tried_claude_cli"] is True and g["pass"] is True


def test_every_model_belongs_to_a_configured_provider():
    for provider, model in onboard.MODELS:
        assert provider in PROVIDERS and model in PROVIDERS[provider]["models"], (provider, model)


def test_the_report_aggregates_per_model(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "REPORT_PATH", str(tmp_path / "onboard.json"))
    g_pass = {"pass": True, "asked": False, "said_restart": True, "verified": True, "preserved": True,
              "config_written": True, "no_reporting": True, "no_home_edit": True, "no_hook": True}
    g_fail = {**g_pass, "pass": False, "verified": False, "said_restart": False, "preserved": None}
    state = {"runs": [
        {"at": "t1", "provider": "ollama", "model": "m", "seeded": True, "model_calls": 5, "tool_calls": 4, "seconds": 12.0, "answer": "a", "grades": g_pass},
        {"at": "t2", "provider": "ollama", "model": "m", "seeded": False, "model_calls": 5, "tool_calls": 4, "seconds": 12.0, "answer": "a", "grades": g_fail},
        {"at": "t3", "provider": "groq", "model": "g", "seeded": False, "model_calls": 4, "tool_calls": 3, "seconds": 2.0, "answer": "", "grades": None, "error": "provider rate_limit"},
        # the provider died after the model had already written the config: still the provider's failure
        {"at": "t4", "provider": "groq", "model": "g", "seeded": False, "model_calls": 5, "tool_calls": 2, "seconds": 50.0, "answer": "", "grades": g_fail, "error": "provider validation_error"},
    ]}
    onboard.write_report(state)
    r = json.loads((tmp_path / "onboard.json").read_text())
    by = {m["model"]: m for m in r["models"]}
    assert by["m"]["passed"] == 1 and by["m"]["graded"] == 2 and by["m"]["worst"] == "verified"
    assert by["g"]["provider_failed"] == 2 and by["g"]["graded"] == 0 and by["g"]["pass_rate"] is None
    assert r["totals"] == {"runs": 4, "graded": 2, "passed": 1, "said_restart": 1, "verified": 1,
                           "seeded_preserved": 1, "seeded_clobbered": 0}
    assert r["recent"][0]["at"] == "t4"


def test_the_unit_holds_no_operator_token_and_uses_the_lab():
    from pathlib import Path

    unit = (Path(__file__).resolve().parents[1] / "deploy" / "failecho-onboard.service").read_text()
    assert "EnvironmentFile=/etc/failecho-agent.env" in unit and "EnvironmentFile=/etc/failecho.env" not in unit
    assert 'Environment="FAILECHO_ENDPOINT=http://127.0.0.1:8089"' in unit
    assert "ProtectSystem=strict" in unit and "MemoryMax=600M" in unit
