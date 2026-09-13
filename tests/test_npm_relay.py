"""The Node relay, exercised as a host would drive it.

It is JavaScript in a Python repository, which is exactly the sort of thing
that rots unnoticed. These run the real binary over stdin and stdout against
the live network, and skip rather than fail where Node is not installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

RELAY = Path(__file__).resolve().parents[1] / "npm-relay" / "bin" / "failecho-mcp.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

INITIALIZE = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "test", "version": "1"}},
}


def drive(messages, env=None, timeout=90):
    """Feed newline-delimited JSON-RPC in, parse whatever comes out."""
    proc = subprocess.run(
        ["node", str(RELAY)],
        input="".join(json.dumps(m) + "\n" for m in messages),
        capture_output=True, text=True, timeout=timeout,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", **(env or {})},
    )
    out = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    return proc, out


def test_it_relays_a_real_session():
    _, out = drive([
        INITIALIZE,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ])
    by_id = {m.get("id"): m for m in out}
    assert "result" in by_id[1], "initialize did not come back"
    names = {t["name"] for t in by_id[2]["result"]["tools"]}
    assert names == {
        "check_tool_failure", "report_tool_failure",
        "report_tool_success", "report_recovery_outcome",
    }


def test_a_notification_produces_no_reply():
    """The endpoint answers 202 with no body; inventing a reply would desync
    the host, which is counting responses against the ids it sent."""
    _, out = drive([{"jsonrpc": "2.0", "method": "notifications/initialized"}])
    assert out == []


def test_an_unreachable_network_answers_instead_of_hanging():
    """A relay that stalls takes the host's whole session with it. Better to
    hand back an error the agent can act on."""
    _, out = drive([INITIALIZE], env={"FAILECHO_URL": "http://127.0.0.1:9"}, timeout=60)
    assert out and out[0]["id"] == 1
    assert out[0]["error"]["code"] == -32000
    assert "unreachable" in out[0]["error"]["message"]


def test_a_bad_line_is_skipped_not_fatal():
    proc = subprocess.run(
        ["node", str(RELAY)],
        input='not json\n{"jsonrpc":"2.0","id":7,"method":"tools/list"}\n',
        capture_output=True, text=True, timeout=90,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    assert proc.returncode == 0
    answered = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    assert [m["id"] for m in answered] == [7]


def test_nothing_but_protocol_reaches_stdout():
    """Anything written to stdout that is not JSON-RPC corrupts the stream.
    The startup line has to go to stderr, and so does every log."""
    proc, out = drive([INITIALIZE])
    assert "relaying to" in proc.stderr, "the banner belongs on stderr"
    for line in proc.stdout.splitlines():
        if line.strip():
            json.loads(line)


def test_the_package_declares_what_it_ships():
    manifest = json.loads((RELAY.parents[1] / "package.json").read_text())
    assert manifest["name"] == "failecho-mcp"
    assert manifest["bin"]["failecho-mcp"] == "bin/failecho-mcp.js"
    assert "dependencies" not in manifest, "zero dependencies is the point"
    assert manifest["license"] == "MIT"
