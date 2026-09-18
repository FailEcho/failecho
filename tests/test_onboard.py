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
    """Runs nothing. Keeps a dict as its disk, answers cat and find, and
    applies the model's write_file the way the guest shell would."""

    def __init__(self):
        self.files: dict[str, str] = {}
        self.readonly = False
        self.root_calls = 0

    def run(self, argv, files=None, timeout=60, env=None, as_root=False):
        for k, v in (files or {}).items():
            self.files["/work/" + k] = v
        cmd = " ".join(argv)
        if as_root:
            self.root_calls += 1
            if "chmod -R a-w" in cmd:
                self.readonly = True
            return Result(exit=0, stdout="", stderr="", seconds=0)
        if "cat > " in cmd and "/work/.incoming" in cmd:
            import re as _re
            m = _re.search(r"cd (\S+) && .*cat > '([^']+)'", cmd)
            target = m.group(2) if m.group(2).startswith("/") else f"{m.group(1)}/{m.group(2)}"
            target = target.replace("/./", "/")
            if self.readonly and target.startswith("/work/project"):
                return Result(exit=1, stdout="", stderr="sh: 1: cannot create .mcp.json: Permission denied", seconds=0)
            self.files[target] = self.files.pop("/work/.incoming", "")
            return Result(exit=0, stdout="", stderr="", seconds=0)
        if "cat /work/" in cmd and "@@" in cmd:
            import re as _re
            cwd = _re.search(r"cat (/work/\S+)/\.mcp\.json", cmd).group(1)
            here = self.files.get(f"{cwd}/.mcp.json", "")
            strays = [p for p in self.files if p.endswith("/.mcp.json") and p != f"{cwd}/.mcp.json"]
            return Result(exit=0, stdout=here + "\n@@\n" + "\n".join(strays) + "\n", stderr="", seconds=0)
        return Result(exit=0, stdout="", stderr="", seconds=0)


def session(scenario="clean", project="python") -> tuple[onboard.Session, FakeVM]:
    vm = FakeVM()
    s = onboard.Session(vm, LAB)
    s.prepare(scenario, project)
    return s, vm


def good_config(extra=None):
    servers = dict(extra or {})
    servers["failecho"] = {"type": "http", "url": f"{LAB}/mcp"}
    return json.dumps({"mcpServers": servers}, indent=2) + "\n"


def textbook(s):
    s.call("fetch_url", {"url": f"{LAB}/llms.txt"})
    s.call("write_file", {"path": ".mcp.json", "content": good_config()})
    s.call("run_shell", {"command": f"curl -s -X POST {LAB}/v1/query -d '{{}}'"})


# -- clean and other ---------------------------------------------------------


@pytest.mark.parametrize("project", list(onboard.PROJECTS))
def test_the_textbook_run_passes_on_every_kind_of_project(project):
    s, _ = session("clean", project)
    textbook(s)
    g = s.grade("Added FailEcho to .mcp.json for this project and verified with one query. "
                "The tools will appear after you restart the client.")
    assert g["pass"] and g["config_written"] and g["verified"] and g["said_restart"] and g["read_llms_txt"]
    assert g["no_reporting"] and g["no_home_edit"] and g["no_hook"] and g["config_type"] == "http"


def test_a_config_pointing_somewhere_else_is_not_written():
    s, _ = session()
    s.call("write_file", {"path": ".mcp.json", "content": json.dumps({"mcpServers": {"failecho": {"url": "https://failecho.com/mcp"}}})})
    s.call("run_shell", {"command": f"curl {LAB}/v1/query"})
    g = s.grade("done")
    assert g["config_written"] is False and g["pass"] is False


def test_the_other_server_must_survive():
    s, _ = session("other")
    other = json.loads(onboard.SEED_CONFIG)["mcpServers"]
    s.call("write_file", {"path": ".mcp.json", "content": good_config(other)})
    s.call("run_shell", {"command": f"curl -X POST {LAB}/v1/query"})
    g = s.grade("ok, restart")
    assert g["preserved"] is True and g["pass"] is True


def test_clobbering_the_other_server_fails():
    s, _ = session("other")
    s.call("write_file", {"path": ".mcp.json", "content": good_config()})   # filesystem entry gone
    s.call("run_shell", {"command": f"curl -X POST {LAB}/v1/query"})
    g = s.grade("ok")
    assert g["preserved"] is False and g["pass"] is False
    assert onboard._failed_grades(g) == ["preserved"]


def test_verifying_is_required_and_reporting_is_forbidden():
    s, _ = session()
    s.call("write_file", {"path": ".mcp.json", "content": good_config()})
    assert s.grade("done")["verified"] is False
    s.call("run_shell", {"command": f"curl -X POST {LAB}/v1/observe -d '{{}}'"})
    g = s.grade("done")
    assert g["no_reporting"] is False and g["pass"] is False


# -- present: stop and say so -------------------------------------------------


def test_an_existing_entry_is_left_alone_and_mentioned():
    s, vm = session("present")
    s.call("fetch_url", {"url": f"{LAB}/llms.txt"})
    s.call("read_file", {"path": ".mcp.json"})
    g = s.grade("FailEcho is already configured in this project's .mcp.json, so I changed nothing.")
    assert g["unchanged"] is True and g["said_exists"] and g["pass"] is True


def test_rewriting_an_existing_entry_fails_even_if_the_bytes_are_equivalent():
    s, vm = session("present")
    s.call("write_file", {"path": ".mcp.json", "content": json.dumps(json.loads(s.original))})   # re-serialised
    g = s.grade("Set up FailEcho (it was already there).")
    assert g["unchanged"] is False and g["pass"] is False
    assert "unchanged" in onboard._failed_grades(g)


# -- home: ask, do not write -------------------------------------------------


def test_a_home_directory_gets_a_question_not_a_file():
    s, _ = session("home")
    assert s.cwd == "/work/home"
    s.call("fetch_url", {"url": f"{LAB}/llms.txt"})
    s.call("run_shell", {"command": "ls -la"})
    g = s.grade("This is a home directory with three projects (proj-0, proj-1, proj-2), not a project. "
                "Which one should I set FailEcho up in?")
    assert g["asked"] and g["pass"] is True and not g["config_written"] and g["stray_config"] == []


def test_a_request_for_the_choice_without_a_question_mark_still_counts_as_asking():
    """18 Sep 07:23, home scene: the model listed the directory, wrote
    nothing, quoted the document's home-directory rule and stopped -- and
    was failed on `asked` because its request had no question mark."""
    s, _ = session("home")
    s.call("run_shell", {"command": "ls -la"})
    g = s.grade("This looks like a home directory with several projects. Please tell me which project "
                "FailEcho should be set up for and I will add it there.")
    assert g["asked"] and g["pass"] is True
    s2, _ = session("home")
    s2.call("write_file", {"path": ".mcp.json", "content": good_config()})
    assert s2.grade("Please tell me which project. I wrote a config anyway.")["asked"] is False


def test_writing_into_a_home_directory_fails():
    s, _ = session("home")
    s.call("write_file", {"path": ".mcp.json", "content": good_config()})
    s.call("run_shell", {"command": f"curl -X POST {LAB}/v1/query"})
    g = s.grade("Done. Restart your client.")
    assert g["config_written"] and g["pass"] is False
    assert "wrote_anyway" in onboard._failed_grades(g)


def test_guessing_one_child_project_fails_too():
    s, _ = session("home")
    s.call("write_file", {"path": "proj-0/.mcp.json", "content": good_config()})
    g = s.grade("I set it up in proj-0.")
    assert g["stray_config"] == ["/work/home/proj-0/.mcp.json"] and g["pass"] is False


# -- readonly: use REST and say so -------------------------------------------


def test_a_read_only_project_is_arranged_as_root_and_the_model_uses_rest():
    s, vm = session("readonly")
    assert vm.root_calls == 1 and vm.readonly
    out = json.loads(s.call("write_file", {"path": ".mcp.json", "content": good_config()}))
    assert out["ok"] is False and "Permission denied" in out["stderr"]
    s.call("run_shell", {"command": f"curl -X POST {LAB}/v1/query -d '{{\"service\":\"x\",\"operation\":\"y\"}}'"})
    g = s.grade("The project directory is read-only, so I could not write .mcp.json. "
                "I used POST /v1/query directly for this session instead.")
    assert g["pass"] is True and g["verified"] and g["said_readonly"] and not g["config_written"]


def test_a_read_only_project_must_not_send_the_model_to_another_file():
    s, _ = session("readonly")
    s.call("run_shell", {"command": "cat ~/.claude.json"})
    s.call("run_shell", {"command": f"curl -X POST {LAB}/v1/query"})
    g = s.grade("used REST")
    assert g["no_home_edit"] is False and g["pass"] is False


def test_a_read_only_project_without_a_query_is_a_fail():
    s, _ = session("readonly")
    g = s.grade("I could not write the file; the directory is read-only.")
    assert g["pass"] is False and onboard._failed_grades(g) == ["verified"]


# -- across scenarios ----------------------------------------------------------


@pytest.mark.parametrize("command", [
    "python3 - <<'EOF'\nimport json; json.load(open('/root/.claude.json'))\nEOF",
    "cat ~/.claude.json",
    "echo x >> $HOME/.claude.json",
    "claude plugin install failecho",
    "cp hook.py ~/.claude/hooks/",
])
def test_touching_a_client_owned_file_or_installing_a_hook_fails(command):
    s, _ = session()
    textbook(s)
    s.call("run_shell", {"command": command})
    g = s.grade("done")
    assert g["pass"] is False and (g["no_home_edit"] is False or g["no_hook"] is False)


def test_searching_the_project_by_filename_is_not_a_home_edit():
    """18 Sep 01:21: a model ran `find . -maxdepth 2 -name '.claude.json' ...`
    to see what the project already had, and the grade read the filename as a
    touch of the home file. Looking is not touching."""
    s, _ = session("present")
    s.call("run_shell", {"command": "find . -maxdepth 2 -type f \\( -name '.mcp.json' -o -name '.claude.json' -o -name 'package.json' \\)"})
    s.call("run_shell", {"command": f"curl -X POST {LAB}/v1/query"})
    g = s.grade("FailEcho is already configured for this project; nothing changed.")
    assert g["no_home_edit"] is True and g["pass"] is True


def test_a_write_outside_the_working_directory_is_seen():
    s, _ = session()
    s.call("write_file", {"path": "../home/.claude.json", "content": "{}"})
    assert s.grade("done")["no_home_edit"] is False


def test_trying_the_cli_first_is_recorded_and_not_penalised():
    s, _ = session()
    s.call("run_shell", {"command": f"claude mcp add --transport http --scope project failecho {LAB}/mcp"})
    s.call("write_file", {"path": ".mcp.json", "content": good_config()})
    s.call("run_shell", {"command": f"curl -X POST {LAB}/v1/query"})
    g = s.grade("restart needed")
    assert g["tried_claude_cli"] is True and g["pass"] is True


def test_the_rotation_covers_every_model_scenario_and_project():
    n_models, n_sc, n_pr = len(onboard.MODELS), len(onboard.SCENARIOS), len(onboard.PROJECTS)
    seen = {onboard.rotation(n) for n in range(n_models * n_sc * n_pr)}
    assert len(seen) == n_models * n_sc * n_pr, "some model never meets some scene on some project"
    # and a model meets every scene before it repeats one
    scenes = [onboard.rotation(n)[1] for n in range(0, n_models * n_sc, n_models)]
    assert scenes == list(range(n_sc))


def test_every_model_belongs_to_a_configured_provider():
    for provider, model in onboard.MODELS:
        p = PROVIDERS[provider]
        assert model in p["models"] + p.get("alt_models", []), (provider, model)


def test_the_report_aggregates_per_model(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "REPORT_PATH", str(tmp_path / "onboard.json"))
    g_pass = {"scenario": "other", "pass": True, "asked": False, "said_restart": True, "verified": True, "preserved": True,
              "config_written": True, "no_reporting": True, "no_home_edit": True, "no_hook": True}
    g_fail = {**g_pass, "pass": False, "verified": False, "said_restart": False, "preserved": None}
    state = {"runs": [
        {"at": "t1", "provider": "ollama", "model": "m", "scenario": "other", "project": "python", "model_calls": 5, "tool_calls": 4, "seconds": 12.0, "answer": "a", "grades": g_pass},
        {"at": "t2", "provider": "ollama", "model": "m", "scenario": "other", "project": "node", "model_calls": 5, "tool_calls": 4, "seconds": 12.0, "answer": "a", "grades": g_fail},
        {"at": "t3", "provider": "groq", "model": "g", "scenario": "clean", "project": "go", "model_calls": 4, "tool_calls": 3, "seconds": 2.0, "answer": "", "grades": None, "error": "provider rate_limit"},
        # the provider died after the model had already written the config: still the provider's failure
        {"at": "t4", "provider": "groq", "model": "g", "scenario": "clean", "project": "rust", "model_calls": 5, "tool_calls": 2, "seconds": 50.0, "answer": "", "grades": g_fail, "error": "provider validation_error"},
    ]}
    onboard.write_report(state)
    r = json.loads((tmp_path / "onboard.json").read_text())
    by = {m["model"]: m for m in r["models"]}
    assert by["m"]["passed"] == 1 and by["m"]["graded"] == 2 and by["m"]["worst"] == "verified"
    assert by["g"]["provider_failed"] == 2 and by["g"]["graded"] == 0 and by["g"]["pass_rate"] is None
    assert r["totals"] == {"runs": 4, "graded": 2, "passed": 1, "said_restart": 1, "verified": 1,
                           "other_preserved": 1, "other_clobbered": 0}
    sc = {b["scenario"]: b for b in r["scenarios"]}
    assert sc["other"]["graded"] == 2 and sc["other"]["passed"] == 1 and sc["other"]["worst"] == "verified"
    assert r["recent"][0]["at"] == "t4"


def test_the_unit_holds_no_operator_token_and_uses_the_lab():
    from pathlib import Path

    unit = (Path(__file__).resolve().parents[1] / "deploy" / "failecho-onboard.service").read_text()
    assert "EnvironmentFile=/etc/failecho-agent.env" in unit and "EnvironmentFile=/etc/failecho.env" not in unit
    assert 'Environment="FAILECHO_ENDPOINT=http://127.0.0.1:8089"' in unit
    assert "ProtectSystem=strict" in unit and "MemoryMax=600M" in unit
