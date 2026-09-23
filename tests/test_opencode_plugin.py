"""The OpenCode plugin, driven the way OpenCode drives it.

OpenCode fires `tool.execute.before` and `tool.execute.after` around every
tool it runs -- `bash`, `webfetch` and every MCP tool -- and a plugin may
mutate the result in place. That is the whole reason this exists: the MCP
path asks the model to choose to call FailEcho, and over 32 lab runs it chose
to 0.19 times per run. A hook does not ask.

Nothing here talks to production. A throwaway HTTP server stands in for the
network and records exactly what the plugin sent, which is the part that has
to stay narrow: a host, an operation, a class, a code, a latency, and nothing
else -- no command, no arguments, no output, no URL beyond its host.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "opencode-plugin" / "plugin" / "failecho.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


class Network:
    """Stands in for failecho.com and keeps what arrived."""

    def __init__(self, answer=None):
        self.posts: list[tuple[str, dict, dict]] = []
        self.answer = answer or {"known": False, "status": "INSUFFICIENT_DATA",
                                 "recommendation": None, "recovery_actions": [],
                                 "fingerprint": "abc123", "observations": {"total": 0}}
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or "{}")
                recorder.posts.append((self.path, body, dict(self.headers)))
                payload = json.dumps(
                    {"accepted": True, "fingerprint": "abc123"} if self.path != "/v1/query"
                    else recorder.answer
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.shutdown()

    def sent_to(self, path: str) -> list[dict]:
        return [body for where, body, _ in self.posts if where == path]


@pytest.fixture
def network():
    net = Network()
    yield net
    net.stop()


def drive(calls, endpoint, env=None, answer_wait_ms=400):
    """Run the plugin's hooks over a list of (tool, args, result) calls.

    Returns the results after the plugin has had its way with them, so a test
    can assert on what the model would have seen.
    """
    script = textwrap.dedent(f"""
        import plugin from {json.dumps(str(PLUGIN))};
        const hooks = await plugin({{}});
        const calls = {json.dumps(calls)};
        const out = [];
        for (const call of calls) {{
            const input = {{ tool: call.tool, callID: call.callID || call.tool, args: call.args }};
            if (hooks["tool.execute.before"]) await hooks["tool.execute.before"](input, {{ args: call.args }});
            const result = call.result;
            if (hooks["tool.execute.after"]) await hooks["tool.execute.after"](input, result);
            out.push(result);
        }}
        // reports are fire-and-forget; give them a moment to land
        await new Promise((r) => setTimeout(r, {answer_wait_ms}));
        console.log(JSON.stringify(out));
    """)
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:" + str(Path.home() / ".nvm/versions/node/v22.23.2/bin"),
             "FAILECHO_ENDPOINT": endpoint, **(env or {})},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


CURL_429 = {
    "tool": "bash",
    "args": {"command": "curl -s https://api.github.com/repos/foo/bar/issues"},
    "result": {"title": "curl", "output": "HTTP/2 429\nx-ratelimit-remaining: 0\nrate limit exceeded",
               "metadata": {"exit": 22}},
}


# -- what it reports -----------------------------------------------------------


def test_a_failed_shell_call_is_reported_as_its_shape(network):
    drive([CURL_429], network.endpoint)
    observed = network.sent_to("/v1/observe")
    assert observed, "nothing was reported"
    body = observed[0]
    assert body["service"] == "api.github.com"
    assert body["operation"] == "GET /repos"
    assert body["outcome"] == "failure"
    assert body["error_type"] == "rate_limit"
    assert body["error_code"] == "429"
    assert set(body) <= {"service", "operation", "outcome", "error_type", "error_code", "latency_ms"}


def test_nothing_from_the_command_or_the_output_is_ever_sent(network):
    drive([{
        "tool": "bash",
        "args": {"command": "curl -H 'Authorization: Bearer sk-secret-token' "
                            "https://api.github.com/repos/acme/private/issues?state=open"},
        "result": {"output": "403 Forbidden: bad credentials for user hunter2", "metadata": {"exit": 1}},
    }], network.endpoint)
    flat = json.dumps(network.posts)
    for forbidden in ("sk-secret-token", "Authorization", "hunter2", "acme/private",
                      "state=open", "curl", "Bearer"):
        assert forbidden not in flat, f"{forbidden} leaked: {flat}"
    assert "api.github.com" in flat, "the host is the point of the report"


def test_a_successful_call_is_reported_too(network):
    drive([{
        "tool": "bash",
        "args": {"command": "curl -s https://pypi.org/pypi/requests/json"},
        "result": {"output": '{"info": {"version": "2.34.2"}}', "metadata": {"exit": 0}},
    }], network.endpoint)
    body = network.sent_to("/v1/observe")[0]
    assert (body["service"], body["outcome"]) == ("pypi.org", "success")
    assert "error_type" not in body


def test_a_local_command_is_nobody_elses_business(network):
    drive([
        {"tool": "bash", "args": {"command": "pytest -q"}, "result": {"output": "2 failed", "metadata": {"exit": 1}}},
        {"tool": "read", "args": {"path": "/etc/passwd"}, "result": {"output": "no such file", "metadata": {"error": "ENOENT"}}},
        {"tool": "grep", "args": {"pattern": "todo"}, "result": {"output": "no matches", "metadata": {"exit": 1}}},
    ], network.endpoint)
    assert network.posts == [], f"local failures must not be reported: {network.posts}"


def test_an_mcp_tool_is_named_the_way_the_proxy_names_it(network):
    drive([{
        "tool": "github_create_issue",
        "args": {"repo": "acme/widgets", "title": "x"},
        "result": {"output": "Error: 403 rate limit exceeded", "metadata": {"error": "403"}},
    }], network.endpoint)
    body = network.sent_to("/v1/observe")[0]
    assert (body["service"], body["operation"]) == ("github", "create_issue")
    assert body["error_type"] == "rate_limit"


def test_a_webfetch_failure_is_reported_against_its_host(network):
    drive([{
        "tool": "webfetch",
        "args": {"url": "https://registry.npmjs.org/express/latest"},
        "result": {"output": "503 Service Unavailable", "metadata": {"error": "503"}},
    }], network.endpoint)
    body = network.sent_to("/v1/observe")[0]
    assert (body["service"], body["operation"], body["error_type"]) == (
        "registry.npmjs.org", "GET /express", "server_error")


# -- what the model sees -------------------------------------------------------


def test_the_advice_lands_in_the_output_the_model_is_already_reading():
    net = Network(answer={
        "known": True, "status": "DEGRADED", "fingerprint": "abc123",
        "recommendation": {"action": "wait_until_reset", "confidence": 0.82, "from_other_agents": True},
        "recovery_actions": [{"action": "wait_until_reset", "attempts": 40, "successes": 33},
                             {"action": "backoff", "attempts": 120, "successes": 14}],
        "failure_rate": {"last_5m": 0.44},
    })
    try:
        results = drive([CURL_429], net.endpoint)
    finally:
        net.stop()
    output = results[0]["output"]
    assert "rate limit exceeded" in output, "the tool's own output must survive"
    assert "FailEcho: try wait_until_reset (worked 33/40)" in output
    assert output.index("FailEcho") > output.index("rate limit"), "appended, never replacing"


def test_an_empty_network_says_nothing_at_all(network):
    results = drive([CURL_429], network.endpoint)
    assert "FailEcho" not in results[0]["output"], "no evidence, no line"


def test_advice_can_be_turned_off_while_reporting_stays_on():
    net = Network(answer={"known": True, "status": "DEGRADED", "fingerprint": "abc123",
                          "recommendation": {"action": "backoff", "confidence": 0.7},
                          "recovery_actions": [{"action": "backoff", "attempts": 10, "successes": 8}]})
    try:
        results = drive([CURL_429], net.endpoint, env={"FAILECHO_ADVISE": "0"})
        assert "FailEcho" not in results[0]["output"]
        assert net.sent_to("/v1/observe"), "reporting is a separate switch"
        assert net.sent_to("/v1/query") == [], "no advice asked for"
    finally:
        net.stop()


def test_disabled_means_nothing_happens(network):
    results = drive([CURL_429], network.endpoint, env={"FAILECHO_DISABLED": "1"})
    assert network.posts == []
    assert "FailEcho" not in results[0]["output"]


# -- the loop the network is actually built on ---------------------------------


def test_a_failure_then_a_success_is_reported_as_a_recovery(network):
    drive([
        CURL_429,
        {"tool": "bash", "callID": "2",
         "args": {"command": "curl -s https://api.github.com/repos/foo/bar/issues"},
         "result": {"output": "HTTP/2 200", "metadata": {"exit": 0}}},
    ], network.endpoint)
    outcomes = network.sent_to("/v1/outcome")
    assert outcomes, "the sequence that proves what fixed it is the point"
    assert outcomes[0]["fingerprint"] == "abc123" and outcomes[0]["successful"] is True


def test_a_success_on_its_own_is_not_a_recovery(network):
    drive([{
        "tool": "bash",
        "args": {"command": "curl -s https://api.github.com/repos/foo/bar"},
        "result": {"output": "HTTP/2 200", "metadata": {"exit": 0}},
    }], network.endpoint)
    assert network.sent_to("/v1/outcome") == []


# -- the contract every FailEcho integration has to keep -----------------------


def test_the_plugin_never_breaks_the_tool_it_watches():
    """No network at all: the result has to come back untouched, and quickly."""
    results = drive([CURL_429], "http://127.0.0.1:9", answer_wait_ms=50)
    assert results[0]["output"].startswith("HTTP/2 429")
    assert "FailEcho" not in results[0]["output"]


def test_a_result_with_no_output_field_is_survivable():
    results = drive([{"tool": "bash", "args": {"command": "curl https://api.github.com/x"},
                      "result": {"metadata": {"exit": 7}}}], "http://127.0.0.1:9", answer_wait_ms=50)
    assert results[0]["metadata"]["exit"] == 7


def test_the_three_implementations_share_one_error_table():
    """Evidence reported from OpenCode, from the wrapper and from the MCP
    proxy has to land on the same fingerprint, which means the same classes in
    the same order."""
    import re

    plugin = PLUGIN.read_text()
    relay = (PLUGIN.parents[2] / "npm-relay" / "bin" / "proxy.js").read_text()

    def classes(source: str) -> list[str]:
        block = re.search(r"ERROR_CLASSES = \[(.*?)\n\]", source, re.S).group(1)
        return re.findall(r'\["(\w+)"', block)

    assert classes(plugin) == classes(relay), "the classification tables have drifted"
