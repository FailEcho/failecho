"""The Claude Code hook: automatic, private, and never in the way.

Naming and classification are tested directly. Everything else runs the hook
the way Claude Code does -- a separate process, an event on stdin -- against a
real FailEcho server, because the hook's whole job is the network round trip.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from tests.live_server import free_port, running_failecho

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "plugin" / "hooks" / "failecho_hook.py"

_spec = importlib.util.spec_from_file_location("claude_code_hook", HOOK)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)

OPERATOR_TOKEN = "hook-operator-token"
GITHUB = "@modelcontextprotocol/server-github"
SERVERS = {
    "gh": {"type": "stdio", "command": "npx", "args": ["-y", GITHUB]},
    "linear": {"type": "http", "url": "https://mcp.linear.app/mcp"},
    "mine": {"type": "stdio", "command": "python", "args": ["/home/me/server.py"]},
}


# ---------------------------------------------------------------------------
# naming
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://api.githubcopilot.com/mcp/", "api.githubcopilot.com"),
        ("https://mcp.linear.app/sse", "mcp.linear.app"),
        ("http://localhost:3000/mcp", None),
        ("http://127.0.0.1:8000/mcp", None),
        ("http://10.0.0.5/mcp", None),
        ("https://mcp.corp.internal/mcp", None),
        ("http://intranet/mcp", None),
    ],
)
def test_remote_servers_are_named_by_public_host_only(url, expected):
    assert hook.public_host(url) == expected


@pytest.mark.parametrize(
    "command, args, expected",
    [
        ("npx", ["-y", GITHUB], GITHUB),
        ("npx", ["-y", "@playwright/mcp@latest"], "@playwright/mcp"),
        ("/usr/bin/npx", ["mcp-remote@0.1.2", "https://example.com"], "mcp-remote"),
        ("uvx", ["mcp-server-fetch"], "mcp-server-fetch"),
        ("uvx", ["--from", "failecho-server", "failecho-mcp"], "failecho-server"),
        ("uvx", ["--python", "3.12", "mcp-server-time==1.0"], "mcp-server-time"),
        ("pnpm", ["dlx", "some-mcp"], "some-mcp"),
        ("node", ["/home/me/server.js"], None),
        ("python", ["server.py"], None),
        ("npx", ["./local-server"], None),
        ("uvx", ["--from", "git+https://github.com/me/private", "srv"], None),
    ],
)
def test_local_servers_are_named_by_public_package_only(command, args, expected):
    assert hook.package_name(command, args) == expected


def test_tool_names_split_into_server_and_tool():
    assert hook.split_tool_name("mcp__gh__create_issue") == ("gh", "create_issue")
    assert hook.split_tool_name("mcp__plugin_x_db__query") == ("plugin_x_db", "query")
    assert hook.split_tool_name("Bash") is None
    assert hook.split_tool_name("mcp__broken") is None
    assert hook.is_failecho("failecho")


@pytest.mark.parametrize(
    "error, expected",
    [
        ("Upstream returned HTTP 503: service unavailable", ("server_error", "503")),
        ("Request timed out after 30s", ("timeout", None)),
        ("429 Too Many Requests", ("rate_limit", "429")),
        ("HTTP 401 Unauthorized", ("auth_error", "401")),
        ("Resource not found", ("not_found", None)),
        ("MCP error -32602: Invalid params", ("validation_error", "-32602")),
        ("connect ECONNREFUSED 127.0.0.1:443", ("connection_error", None)),
        ("Error executing tool always_fails", ("tool_error", None)),
    ],
)
def test_errors_are_classified_locally(error, expected):
    assert hook.classify(error) == expected


# ---------------------------------------------------------------------------
# privacy, checked on the exact bodies the hook would send
# ---------------------------------------------------------------------------


class Recorder:
    """Stands in for the network and remembers every body."""

    def __init__(self):
        self.bodies: list[tuple[str, dict]] = []

    def post(self, path, body):
        self.bodies.append((path, body))
        return {"known": False, "fingerprint": "f" * 32}


def configure(tmp_path: Path, monkeypatch) -> Path:
    """A fake Claude Code config and a private state dir for one test."""
    config_dir = tmp_path / "claude"
    project = tmp_path / "project"
    config_dir.mkdir()
    project.mkdir()
    (config_dir / ".claude.json").write_text(
        json.dumps({"projects": {str(project): {"mcpServers": SERVERS}}})
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    for name in ("FAILECHO_HOOK_SEND_ERRORS", "FAILECHO_HOOK_SERVICE_NAMES",
                 "FAILECHO_OPERATOR_TOKEN", "FAILECHO_DISABLED"):
        monkeypatch.delenv(name, raising=False)
    return project


def failure(tool="mcp__gh__create_issue", error="HTTP 422: Validation Failed",
            tool_input=None, session="session-1", **extra) -> dict:
    return {
        "session_id": session,
        "transcript_path": "/home/me/.claude/projects/secret-project/t.jsonl",
        "cwd": "/home/me/secret-project",
        "hook_event_name": "PostToolUseFailure",
        "tool_name": tool,
        "tool_input": tool_input if tool_input is not None else {"title": "Q3 layoffs plan"},
        "tool_use_id": "toolu_1",
        "error": error,
        "is_interrupt": False,
        "duration_ms": 120,
        **extra,
    }


def success(tool="mcp__gh__create_issue", tool_input=None, session="session-1") -> dict:
    return {
        "session_id": session,
        "cwd": "/home/me/secret-project",
        "hook_event_name": "PostToolUse",
        "tool_name": tool,
        "tool_input": tool_input if tool_input is not None else {"title": "Q3 layoffs plan"},
        "tool_response": "{\"secret\": \"customer list\"}",
        "tool_use_id": "toolu_2",
        "duration_ms": 80,
    }


def test_only_metadata_ever_leaves_the_machine(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    recorder = Recorder()
    hook.handle(failure(error="HTTP 422: title 'Q3 layoffs plan' is invalid"), recorder)
    hook.handle(success(), recorder)

    sent = json.dumps(recorder.bodies)
    for private in ("Q3 layoffs plan", "customer list", "secret-project",
                    "session-1", "toolu_", "title"):
        assert private not in sent, private
    assert {path for path, _ in recorder.bodies} == {"/v1/query", "/v1/observe", "/v1/outcome"}
    first = recorder.bodies[0][1]
    assert first == {"service": GITHUB, "operation": "create_issue",
                     "error_type": "validation_error", "error_code": "422"}


def test_error_text_is_sent_only_when_asked(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    monkeypatch.setenv("FAILECHO_HOOK_SEND_ERRORS", "1")
    recorder = Recorder()
    hook.handle(failure(error="HTTP 422: Validation Failed"), recorder)
    assert recorder.bodies[0][1]["error_message"] == "HTTP 422: Validation Failed"


def test_unnamed_servers_and_failecho_itself_are_never_reported(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    recorder = Recorder()
    hook.handle(failure(tool="mcp__mine__run"), recorder)
    hook.handle(failure(tool="mcp__failecho__check_tool_failure"), recorder)
    hook.handle(failure(tool="mcp__unknown__thing"), recorder)
    hook.handle(failure(is_interrupt=True), recorder)
    hook.handle(failure(tool="Bash"), recorder)
    assert recorder.bodies == []


def test_an_explicit_name_makes_a_private_server_reportable(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    monkeypatch.setenv("FAILECHO_HOOK_SERVICE_NAMES", json.dumps({"mine": "acme-mcp"}))
    recorder = Recorder()
    hook.handle(failure(tool="mcp__mine__run"), recorder)
    assert recorder.bodies[0][1]["service"] == "acme-mcp"


# ---------------------------------------------------------------------------
# against a real server, run exactly as Claude Code runs it
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def server():
    with running_failecho(
        FIN_FIRST_PARTY_TOKEN=OPERATOR_TOKEN,
        FIN_RATE_LIMIT_WRITES_PER_MINUTE="10000",
    ) as live:
        yield live.base


@pytest.fixture()
def run_hook(tmp_path, server):
    """Run the hook as a subprocess with an event on stdin."""
    config_dir = tmp_path / "claude"
    project = tmp_path / "project"
    config_dir.mkdir()
    project.mkdir()
    (config_dir / ".claude.json").write_text(
        json.dumps({"projects": {str(project): {"mcpServers": SERVERS}}})
    )
    base_env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path / "home"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "CLAUDE_CONFIG_DIR": str(config_dir),
        "CLAUDE_PROJECT_DIR": str(project),
        "FAILECHO_ENDPOINT": server,
    }

    def run(event, **env) -> tuple[int, str, float]:
        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=event if isinstance(event, str) else json.dumps(event),
            env={**base_env, **env},
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode, result.stdout.strip(), time.monotonic() - started

    return run


def get(base: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base}{path}", timeout=5) as response:
        return json.load(response)


def post(base: str, path: str, body: dict, reporter: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if reporter:
        headers["X-Reporter-ID"] = reporter
    request = urllib.request.Request(
        f"{base}{path}", data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def ask(base: str, operation: str, error_type: str, error_code: str | None) -> dict:
    return post(base, "/v1/query", {"service": GITHUB, "operation": operation,
                                    "error_type": error_type, "error_code": error_code})


def test_a_failure_is_reported_as_metadata_only(server, run_hook):
    before = get(server, "/v1/stats")
    code, out, _ = run_hook(failure(tool="mcp__gh__merge_pr", error="HTTP 422: bad title"))
    assert code == 0 and out == ""

    after = get(server, "/v1/stats")
    assert after["real_observations_total"] == before["real_observations_total"] + 1
    known = ask(server, "merge_pr", "validation_error", "422")
    assert known["known"] is True
    assert known["normalized_error"] is None  # the error text never left


def test_known_evidence_reaches_claude_as_context(server, run_hook):
    call = {"service": GITHUB, "operation": "list_issues",
            "error_type": "rate_limit", "error_code": "429"}
    for n in range(6):
        fingerprint = post(server, "/v1/observe", {**call, "outcome": "failure"},
                           reporter=f"other-agent-{n}")["fingerprint"]
        post(server, "/v1/outcome", {"fingerprint": fingerprint, "action": "wait",
                                     "successful": True}, reporter=f"other-agent-{n}")

    code, out, _ = run_hook(failure(tool="mcp__gh__list_issues",
                                    error="429 Too Many Requests"))
    assert code == 0
    output = json.loads(out)["hookSpecificOutput"]
    assert output["hookEventName"] == "PostToolUseFailure"
    assert "FailEcho" in output["additionalContext"]
    assert "wait" in output["additionalContext"]


def test_an_unknown_failure_adds_no_context(server, run_hook):
    code, out, _ = run_hook(failure(tool="mcp__gh__never_seen", error="HTTP 404"))
    assert code == 0 and out == ""


def test_a_retry_that_works_is_reported_as_recovery(server, run_hook):
    run_hook(failure(tool="mcp__gh__get_file", error="HTTP 503", session="retry-ok"))
    run_hook(success(tool="mcp__gh__get_file", session="retry-ok"))

    actions = ask(server, "get_file", "server_error", "503")["recovery_actions"]
    assert [(a["action"], a["attempts"], a["successes"]) for a in actions] == [("retry", 1, 1)]


def test_changed_arguments_are_reported_as_their_own_recovery(server, run_hook):
    run_hook(failure(tool="mcp__gh__search", error="HTTP 422", tool_input={"q": "a"},
                     session="adjust"))
    run_hook(success(tool="mcp__gh__search", tool_input={"q": "b"}, session="adjust"))

    actions = ask(server, "search", "validation_error", "422")["recovery_actions"]
    assert [a["action"] for a in actions] == ["adjust_arguments"]


def test_a_retry_that_fails_again_is_a_failed_recovery(server, run_hook):
    run_hook(failure(tool="mcp__gh__push", error="HTTP 503", session="retry-bad"))
    run_hook(failure(tool="mcp__gh__push", error="HTTP 503", session="retry-bad"))

    actions = ask(server, "push", "server_error", "503")["recovery_actions"]
    assert [(a["action"], a["attempts"], a["successes"]) for a in actions] == [("retry", 1, 0)]


def test_successes_are_counted(server, run_hook):
    before = get(server, "/v1/stats")["real_successes_24h"]
    run_hook(success(tool="mcp__linear__create_issue"))
    assert get(server, "/v1/stats")["real_successes_24h"] == before + 1


def test_the_operator_token_labels_reports_first_party(server, run_hook):
    before = get(server, "/v1/stats")
    run_hook(failure(tool="mcp__gh__fork", error="HTTP 500"),
             FAILECHO_OPERATOR_TOKEN=OPERATOR_TOKEN)
    after = get(server, "/v1/stats")
    assert after["first_party_observations"] == before["first_party_observations"] + 1
    assert after["real_observations_total"] == before["real_observations_total"]


def test_disabled_means_nothing_is_sent(server, run_hook):
    before = get(server, "/v1/stats")["observations_total"]
    code, out, _ = run_hook(failure(tool="mcp__gh__close"), FAILECHO_DISABLED="1")
    assert (code, out) == (0, "")
    assert get(server, "/v1/stats")["observations_total"] == before


def test_a_dead_network_never_slows_claude_down(run_hook):
    code, out, elapsed = run_hook(
        failure(), FAILECHO_ENDPOINT=f"http://127.0.0.1:{free_port()}"
    )
    assert (code, out) == (0, "")
    assert elapsed < 3


def test_garbage_input_is_ignored(run_hook):
    for garbage in ("not json", "[]", "", "{\"hook_event_name\": \"Stop\"}"):
        code, out, _ = run_hook(garbage)
        assert (code, out) == (0, "")


def test_the_note_for_claude_is_factual_and_only_appears_with_evidence():
    assert hook.context_note("svc", "op", {"known": False}) is None
    assert hook.context_note("svc", "op", None) is None

    note = hook.context_note("svc", "op", {
        "known": True,
        "status": "INSUFFICIENT_DATA",
        "observations": {"total": 6, "last_1h": 6, "unique_reporters": 6},
        "evidence_sources": ["agent"],
        "recommendation": {"action": "wait", "based_on_successes": 6,
                           "based_on_attempts": 6, "confidence": 0.61},
    })
    assert "wait (6/6 attempts, confidence 0.61)" in note
    assert "INSUFFICIENT_DATA" not in note  # says nothing; would read as a contradiction
    assert "evidence from: agent" in note

    degraded = hook.context_note("svc", "op", {"known": True, "status": "MAJOR",
                                               "observations": {}, "evidence_sources": []})
    assert "Current status: MAJOR." in degraded
