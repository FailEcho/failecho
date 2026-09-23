"""failecho-mcp proxy: FailEcho in front of another MCP server.

It runs between somebody's MCP client and somebody's MCP server, so most of
this is about what it must not do: change a message it has no business with,
hold up the stream, break when FailEcho is down, send anything but a
failure's shape, or hide how the server exited.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE = [sys.executable, str(ROOT / "tests" / "fake_mcp_server.py")]
#: The proxy exists twice, one per package manager; every test runs both.
IMPLS = {"python": [sys.executable, "-m", "failecho_mcp", "proxy"],
         "node": ["node", str(ROOT / "npm-relay" / "bin" / "failecho-mcp.js"), "proxy"]}
PROXY = IMPLS["python"]


@pytest.fixture(autouse=True, params=["python", "node"])
def impl(request, monkeypatch):
    import shutil
    if request.param == "node" and shutil.which("node") is None:
        pytest.skip("node not installed")
    monkeypatch.setattr(sys.modules[__name__], "PROXY", IMPLS[request.param])
    return request.param
ADVICE = {"known": True, "recommendation": {"action": "backoff", "confidence": 0.61},
          "recovery_actions": [{"action": "backoff", "successes": 128, "attempts": 251}]}


class FakeNetwork:
    """A FailEcho that records what it is sent and answers every query."""

    def __init__(self, delay: float = 0.0, answer: dict | None = ADVICE, by_service: dict | None = None):
        self.observed: list[dict] = []
        self.queries: list[dict] = []
        net = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/v1/query":
                    net.queries.append(body)
                    time.sleep(delay)
                    if by_service is not None:
                        out = by_service.get(body.get("service")) or {"known": False, "recommendation": None}
                    else:
                        out = answer or {"known": False, "recommendation": None}
                else:
                    net.observed.append(body)
                    out = {"accepted": True, "fingerprint": "f" * 32}
                data = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


class Session:
    """The proxy as a client's subprocess, spoken to line by line."""

    def __init__(self, endpoint: str, extra_env: dict | None = None, server: list[str] = FAKE,
                 proxy_args: list[str] | None = None):
        env = {**os.environ, "FAILECHO_ENDPOINT": endpoint, "FAILECHO_REPORTER_ID": "proxy-test",
               **(extra_env or {})}
        env.pop("FAILECHO_OPERATOR_TOKEN", None)
        self.proc = subprocess.Popen([*PROXY, *(proxy_args or []), "--", *server],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     cwd=ROOT, env=env)
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for raw in iter(self.proc.stdout.readline, b""):
            self.lines.put(raw)
        self.lines.put(None)

    def send(self, msg) -> None:
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()

    def send_raw(self, raw: bytes) -> None:
        self.proc.stdin.write(raw)
        self.proc.stdin.flush()

    def recv(self, timeout: float = 10.0) -> bytes:
        raw = self.lines.get(timeout=timeout)
        assert raw is not None, "proxy closed its stdout"
        return raw

    def init(self) -> None:
        self.send({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
        assert json.loads(self.recv())["result"]["serverInfo"]["name"] == "fake-server"
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def close(self) -> int:
        try:
            self.proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        return self.proc.wait(timeout=15)


@pytest.fixture
def net():
    n = FakeNetwork()
    yield n
    n.close()


def direct_bytes(messages: list[dict]) -> list[bytes]:
    """What the fake server says with no proxy in the way."""
    p = subprocess.run(FAKE, input="".join(json.dumps(m) + "\n" for m in messages).encode(),
                       capture_output=True, timeout=30)
    return p.stdout.splitlines(keepends=True)


# -- nothing it has no business with changes ------------------------------------------


def test_everything_but_a_failed_call_passes_as_the_same_bytes(net):
    msgs = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "resources/list"},
        {"jsonrpc": "2.0", "id": "p", "method": "prompts/get", "params": {"name": "x"}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "ok", "arguments": {"q": "secret-arg-token"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "no/such/method"},
    ]
    expected = direct_bytes(msgs)
    s = Session(net.url)
    for m in msgs:
        s.send(m)
    got = [s.recv() for _ in expected]
    assert got == expected, "the proxy changed bytes it had no reason to touch"
    assert s.close() == 0


def test_a_server_request_and_the_clients_answer_pass_both_ways(net):
    s = Session(net.url)
    s.init()
    s.send({"jsonrpc": "2.0", "id": 5, "method": "ask_client"})
    req = json.loads(s.recv())
    assert req["method"] == "sampling/createMessage" and req["id"] == "srv-1"
    assert json.loads(s.recv()) == {"jsonrpc": "2.0", "id": 5, "result": {}}
    answer = {"jsonrpc": "2.0", "id": "srv-1", "result": {"role": "assistant", "content": {"type": "text", "text": "hi"}}}
    s.send(answer)
    echoed = json.loads(s.recv())
    assert echoed["params"]["data"]["client_answered"] == answer
    s.close()


def test_a_two_megabyte_result_and_unicode_come_through_whole(net):
    s = Session(net.url)
    s.init()
    s.send({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "big", "arguments": {}}})
    big = json.loads(s.recv())
    assert big["result"]["content"][0]["text"] == "x" * 2_000_000
    s.send({"jsonrpc": "2.0", "id": 7, "method": "resources/list"})
    assert json.loads(s.recv())["result"]["resources"][0]["name"] == "ünïcode"
    s.close()


def test_a_line_that_is_not_json_is_forwarded_not_dropped(net):
    s = Session(net.url)
    s.init()
    s.send_raw(b"this is not json\n")
    assert json.loads(s.recv())["error"]["code"] == -32700, "the server, not the proxy, answers bad input"
    s.send({"jsonrpc": "2.0", "id": 8, "method": "tools/list"})
    assert len(json.loads(s.recv())["result"]["tools"]) == 5
    s.close()


# -- the one message it annotates ---------------------------------------------------------


def test_an_iserror_result_gets_one_line_of_advice_and_keeps_everything_else(net):
    s = Session(net.url)
    s.init()
    s.send({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "fails", "arguments": {}}})
    r = json.loads(s.recv())["result"]
    assert r["isError"] is True
    assert r["content"][0] == {"type": "text", "text": "429 rate limit exceeded secret-error-token"}
    assert r["content"][1] == {"type": "text", "text": "FailEcho: try backoff, worked 128/251 (evidence score 0.61)."}
    assert len(r["content"]) == 2
    s.close()


def test_a_jsonrpc_error_gets_the_line_on_its_message_and_keeps_its_code(net):
    s = Session(net.url)
    s.init()
    s.send({"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "rpc_error", "arguments": {}}})
    e = json.loads(s.recv())["error"]
    assert e["code"] == -32603
    assert e["message"].splitlines() == ["upstream timeout", "FailEcho: try backoff, worked 128/251 (evidence score 0.61)."]
    s.close()


def test_no_evidence_means_the_error_goes_through_unchanged():
    n = FakeNetwork(answer=None)
    try:
        expected = direct_bytes([{"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "fails", "arguments": {}}}])
        s = Session(n.url)
        s.init()
        s.send({"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "fails", "arguments": {}}})
        assert s.recv() == expected[0]
        s.close()
    finally:
        n.close()


def test_advise_off_reports_but_never_annotates(net):
    s = Session(net.url, {"FAILECHO_ADVISE": "0"})
    s.init()
    s.send({"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {"name": "fails", "arguments": {}}})
    assert len(json.loads(s.recv())["result"]["content"]) == 1
    s.close()
    assert any(o["outcome"] == "failure" for o in net.observed) and net.queries == []


# -- what it sends ------------------------------------------------------------------------


def test_reports_carry_the_shape_only(net):
    s = Session(net.url)
    s.init()
    for i, name in enumerate(("ok", "fails", "rpc_error")):
        s.send({"jsonrpc": "2.0", "id": 20 + i, "method": "tools/call",
                "params": {"name": name, "arguments": {"q": "secret-arg-token"}}})
        s.recv()
    s.close()
    sent = json.dumps(net.observed + net.queries)
    for secret in ("secret-arg-token", "secret-result-token", "secret-error-token", "rate limit exceeded"):
        assert secret not in sent, f"{secret} left the machine"
    by_op = {o["operation"]: o for o in net.observed}
    assert by_op["ok"]["outcome"] == "success" and by_op["ok"]["service"] == "fake-server"
    assert by_op["fails"] == {**by_op["fails"], "outcome": "failure", "error_type": "rate_limit", "error_code": "429"}
    allowed = {"service", "operation", "outcome", "error_type", "error_code", "latency_ms", "mutates"}
    assert all(set(o) <= allowed for o in net.observed), [set(o) - allowed for o in net.observed]


def test_disabled_is_a_plain_pipe(net):
    s = Session(net.url, {"FAILECHO_DISABLED": "1"})
    s.init()
    s.send({"jsonrpc": "2.0", "id": 30, "method": "tools/call", "params": {"name": "fails", "arguments": {}}})
    assert len(json.loads(s.recv())["result"]["content"]) == 1
    s.close()
    assert net.observed == [] and net.queries == []


# -- it never holds up the stream or fails with FailEcho -------------------------------------


def test_failecho_down_costs_nothing_and_changes_nothing():
    expected = direct_bytes([{"jsonrpc": "2.0", "id": 40, "method": "tools/call", "params": {"name": "fails", "arguments": {}}}])
    s = Session("http://127.0.0.1:9")
    s.init()
    started = time.monotonic()
    s.send({"jsonrpc": "2.0", "id": 40, "method": "tools/call", "params": {"name": "fails", "arguments": {}}})
    assert s.recv() == expected[0]
    assert time.monotonic() - started < 1.0
    assert s.close() == 0


def test_a_slow_network_holds_only_the_failed_response():
    n = FakeNetwork(delay=10.0)
    try:
        s = Session(n.url)
        s.init()
        started = time.monotonic()
        s.send({"jsonrpc": "2.0", "id": 50, "method": "tools/call", "params": {"name": "fails", "arguments": {}}})
        s.send({"jsonrpc": "2.0", "id": 51, "method": "tools/list"})
        first = json.loads(s.recv())
        assert first["id"] == 51, "an unrelated response waited behind advice"
        second = json.loads(s.recv())
        assert second["id"] == 50 and len(second["result"]["content"]) == 1
        assert time.monotonic() - started < 5.0, "advice waited past its budget"
        s.close()
    finally:
        n.close()


def test_concurrent_calls_are_matched_to_their_own_responses(net):
    s = Session(net.url)
    s.init()
    s.send({"jsonrpc": "2.0", "id": "a", "method": "tools/call", "params": {"name": "slow", "arguments": {"seconds": 0.5}}})
    s.send({"jsonrpc": "2.0", "id": "b", "method": "tools/call", "params": {"name": "fails", "arguments": {}}})
    got = {json.loads(s.recv())["id"]: None for _ in range(2)}
    assert set(got) == {"a", "b"}
    s.close()
    assert {o["operation"]: o["outcome"] for o in net.observed} == {"slow": "success", "fails": "failure"}


# -- lifetime ---------------------------------------------------------------------------------


def test_the_servers_exit_code_is_the_proxys(net):
    s = Session(net.url)
    s.init()
    s.send({"jsonrpc": "2.0", "id": 60, "method": "exit_now", "params": {"code": 3}})
    assert s.proc.wait(timeout=15) == 3


def test_a_server_killed_by_a_signal_exits_the_way_a_shell_reports_it(net):
    import signal
    s = Session(net.url)
    s.init()
    s.proc.send_signal(signal.SIGTERM)
    assert s.proc.wait(timeout=15) == 128 + signal.SIGTERM


def test_a_command_that_does_not_exist_says_so():
    p = subprocess.run([*PROXY, "--", "no-such-mcp-server-xyz"],
                       capture_output=True, timeout=30, cwd=ROOT,
                       env={**os.environ, "FAILECHO_ENDPOINT": "http://127.0.0.1:9"})
    assert p.returncode == 127 and b"cannot start" in p.stderr


def test_client_closing_stdin_shuts_the_server_down(net):
    s = Session(net.url)
    s.init()
    assert s.close() == 0


# -- recovery, inferred from a repeated call ----------------------------------------------------


def test_a_repeat_that_works_after_a_transient_failure_is_reported_as_a_retry(net):
    s = Session(net.url)
    s.init()
    call = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "flaky", "arguments": {"q": "secret-arg-token"}}}
    s.send({**call, "id": 70}); assert json.loads(s.recv())["result"].get("isError") is True
    s.send({**call, "id": 71}); assert "isError" not in json.loads(s.recv())["result"]
    s.close()
    outcomes = [o for o in net.observed if "action" in o]
    assert len(outcomes) == 1
    o = outcomes[0]
    # an outcome is filed on the failure's fingerprint, which the server
    # returned for the report; nothing else about the call goes with it
    assert (o["fingerprint"], o["action"], o["successful"]) == ("f" * 32, "retry", True)
    failure = next(x for x in net.observed if x.get("operation") == "flaky" and x.get("outcome") == "failure")
    assert (failure["error_type"], failure["error_code"]) == ("server_error", "503")
    assert "secret-arg-token" not in json.dumps(net.observed + net.queries)


def test_different_arguments_are_a_different_call_not_a_retry(net):
    s = Session(net.url)
    s.init()
    s.send({"jsonrpc": "2.0", "id": 72, "method": "tools/call", "params": {"name": "flaky", "arguments": {"q": 1}}})
    s.recv()
    s.send({"jsonrpc": "2.0", "id": 73, "method": "tools/call", "params": {"name": "flaky", "arguments": {"q": 2}}})
    s.recv()
    s.close()
    assert [o for o in net.observed if "action" in o] == []


def test_a_repeat_that_fails_again_is_a_retry_that_did_not_work(net):
    s = Session(net.url)
    s.init()
    for i in range(2):
        s.send({"jsonrpc": "2.0", "id": 74 + i, "method": "tools/call", "params": {"name": "rpc_error", "arguments": {}}})
        s.recv()
    s.close()
    # "upstream timeout" is transient, so the second failure is a retry that did not work
    outcomes = [o for o in net.observed if "action" in o]
    assert [(o["action"], o["successful"]) for o in outcomes] == [("retry", False)]


# -- a remote Streamable HTTP server ----------------------------------------------------------


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(params=["json", "sse"], scope="module")
def http_server(request):
    """One fake remote server per transport for the whole module: it is
    stateless, and starting uvicorn cost 0.9 s for every test that used it."""
    port = _free_port()
    proc = subprocess.Popen([sys.executable, str(ROOT / "tests" / "fake_http_mcp_server.py"), str(port), request.param, "tok"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import socket
    # up to 30 s: uvicorn's start is slow when the machine is busy, and a
    # half-started server made this test flaky in the full suite (20 Sep)
    for _ in range(300):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.terminate()
        pytest.fail(f"the test HTTP server never came up on {port}")
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    proc.wait(timeout=10)


def _sdk_session(url: str, endpoint: str, headers: list[str]):
    """Drive the proxy with the official SDK client, env passed explicitly
    (the SDK does not inherit ours: see CLAUDE.md, 19 Sep)."""
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    args = list(PROXY[1:])
    for h in headers:
        args += ["--header", h]
    args += ["--", url]
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(ROOT), "FAILECHO_ENDPOINT": endpoint,
           "FAILECHO_REPORTER_ID": "proxy-test"}

    async def go():
        out = {}
        params = StdioServerParameters(command=PROXY[0], args=args, env=env, cwd=str(ROOT))
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                with anyio.fail_after(20):
                    init = await s.initialize()
                    out["server"] = init.server_info.name
                    out["tools"] = sorted(t.name for t in (await s.list_tools()).tools)
                    ok = await s.call_tool("ok", {"q": "secret-arg-token"})
                    out["ok"] = [c.text for c in ok.content]
                    bad = await s.call_tool("fails", {})
                    out["fails"] = (bad.is_error, [c.text for c in bad.content])
        return out

    return anyio.run(go)


def test_a_remote_server_through_the_proxy_with_the_official_client(http_server, net):
    out = _sdk_session(http_server, net.url, ["Authorization: Bearer tok"])
    assert out["server"] == "fake-http-server" and out["tools"] == ["fails", "ok"]
    assert out["ok"] == ["ok secret-arg-token"]
    is_error, texts = out["fails"]
    # the SDK server hides the exception's text ("Error executing tool
    # fails"); what matters is that the error arrives with the line added
    assert is_error and len(texts) == 2
    assert texts[-1] == "FailEcho: try backoff, worked 128/251 (evidence score 0.61)."
    time.sleep(0.5)
    assert {(o["service"], o["operation"], o["outcome"]) for o in net.observed if "outcome" in o} == {
        ("fake-http-server", "ok", "success"), ("fake-http-server", "fails", "failure")}
    assert "secret-arg-token" not in json.dumps(net.observed + net.queries)
    assert "tok" not in json.dumps(net.observed + net.queries).replace("token", "")


def test_a_refused_remote_answers_the_client_instead_of_hanging(http_server, net):
    """No token: the server says 401. The client must get an error on its
    request, not wait forever for an answer."""
    started = time.monotonic()
    with pytest.raises(Exception) as info:
        _sdk_session(http_server, net.url, [])
    assert time.monotonic() - started < 10, "the client waited for an answer that never came"
    assert "401" in repr(info.value) or "401" in str(getattr(info.value, "exceptions", "")) or "HTTP 401" in str(info.getrepr())


def test_an_unreachable_remote_fails_fast_with_a_message():
    # port 9 (discard) is reliably closed here; a "free" port can be taken by
    # the next test's server before the proxy gets there, and then the call
    # succeeds instead of failing (flaky in the full suite, 20 Sep)
    s = Session("http://127.0.0.1:9", server=["http://127.0.0.1:9/mcp"])
    started = time.monotonic()
    s.send({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
    err = json.loads(s.recv())
    assert err["id"] == 0 and "cannot reach" in err["error"]["message"]
    # the promise is an answer rather than a hang; the bound is generous
    # because this runs on a loaded host (5 s flapped in the full suite)
    assert time.monotonic() - started < 20
    assert s.close() == 0


def test_both_proxies_classify_and_advise_exactly_alike(impl):
    """Evidence from the two languages must land on the same fingerprints,
    and a model must read the same line whichever package it came from."""
    import shutil
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    from failecho_autoreport import FailEcho, classify
    samples = ["429 rate limit exceeded", "HTTP 403 Forbidden: API rate limit exceeded", "403 Forbidden",
               "deadline exceeded", "ETIMEDOUT", "HTTP 404 Not Found", "422 validation failed: field required",
               "ECONNREFUSED 127.0.0.1", "503 service unavailable", "upstream timeout", "-32602 Tool x not found",
               "MCP error -32602: Input validation error", "Error executing tool fails", "something odd"]
    answers = [
        {"recommendation": None, "recovery_actions": [],
         "service_evidence": {"recovery_actions": [{"action": "backoff", "successes": 104, "attempts": 2330},
                                                   {"action": "wait_until_reset", "successes": 2, "attempts": 11}]}},
        {"recommendation": {"action": "skip", "confidence": 0.5, "scope": "service"},
         "service_evidence": {"recovery_actions": [{"action": "backoff", "successes": 1, "attempts": 90}]}},
        {"recommendation": {"action": "backoff", "confidence": 0.6139},
         "recovery_actions": [{"action": "backoff", "successes": 128, "attempts": 251}]},
        {"recommendation": {"action": "skip", "confidence": 0.7}},
        {"recommendation": None, "recovery_actions": [{"action": "retry", "successes": 49, "attempts": 98},
                                                      {"action": "backoff", "successes": 128, "attempts": 251}]},
        {"known": False, "recommendation": None}]
    script = ("const p=require(process.argv[1]);const d=JSON.parse(require('fs').readFileSync(0,'utf8'));"
              "process.stdout.write(JSON.stringify({c:d.s.map(t=>p.classify(t)),a:d.a.map(x=>p.adviceText(x))}))")
    out = subprocess.run(["node", "-e", script, str(ROOT / "npm-relay" / "bin" / "proxy.js")],
                         input=json.dumps({"s": samples, "a": answers}).encode(), capture_output=True, timeout=30)
    node = json.loads(out.stdout)
    for text, (et, code) in zip(samples, node["c"]):
        assert classify(RuntimeError(text)) == (et, code), text
    assert node["a"] == [FailEcho.advice_text(a) for a in answers]


# -- an MCP server that wraps an API: evidence under the API's host -----------------------------


POOLED = {"known": False, "recommendation": None, "recovery_actions": [],
          "service_evidence": {"recovery_actions": [{"action": "backoff", "successes": 104, "attempts": 2330}]}}


def test_no_advice_under_the_server_name_asks_the_wrapped_host_and_says_so():
    n = FakeNetwork(by_service={"api.example.com": POOLED})
    try:
        s = Session(n.url, proxy_args=["--upstream", "fai*=api.example.com"])
        s.init()
        s.send({"jsonrpc": "2.0", "id": 80, "method": "tools/call", "params": {"name": "fails", "arguments": {}}})
        r = json.loads(s.recv())["result"]
        assert r["content"][-1]["text"] == ("FailEcho (evidence from api.example.com): no clear fix yet; on this "
                                            "service's other operations, agents tried backoff worked 104/2330.")
        s.close()
        assert [q["service"] for q in n.queries] == ["fake-server", "api.example.com"]
        assert {o["service"] for o in n.observed if "outcome" in o} == {"fake-server"}, "reports keep the server's name"
    finally:
        n.close()


def test_advice_under_the_server_name_wins_and_the_host_is_not_asked():
    n = FakeNetwork(by_service={"fake-server": ADVICE, "api.example.com": POOLED})
    try:
        s = Session(n.url, proxy_args=["--upstream", "api.example.com"])
        s.init()
        s.send({"jsonrpc": "2.0", "id": 81, "method": "tools/call", "params": {"name": "fails", "arguments": {}}})
        assert json.loads(s.recv())["result"]["content"][-1]["text"].startswith("FailEcho: try backoff")
        s.close()
        assert [q["service"] for q in n.queries] == ["fake-server"]
    finally:
        n.close()


def test_a_tool_no_pattern_covers_is_not_sent_to_any_host():
    n = FakeNetwork(by_service={"api.example.com": POOLED})
    try:
        s = Session(n.url, proxy_args=["--upstream", "github_*=api.example.com"])
        s.init()
        s.send({"jsonrpc": "2.0", "id": 82, "method": "tools/call", "params": {"name": "fails", "arguments": {}}})
        assert len(json.loads(s.recv())["result"]["content"]) == 1
        s.close()
        assert [q["service"] for q in n.queries] == ["fake-server"]
    finally:
        n.close()


def test_the_proxy_never_sends_error_text_even_with_send_errors_on(net):
    """FAILECHO_SEND_ERRORS is the wrapper's switch, for an author's own
    errors. Through the proxy the text belongs to somebody else's tool."""
    s = Session(net.url, {"FAILECHO_SEND_ERRORS": "1"})
    s.init()
    s.send({"jsonrpc": "2.0", "id": 90, "method": "tools/call", "params": {"name": "fails", "arguments": {}}})
    s.recv()
    s.close()
    sent = json.dumps(net.observed)
    assert "error_message" not in sent and "secret-error-token" not in sent, sent[:300]


def test_the_advice_line_carries_what_the_server_qualified_it_with(impl):
    """A decaying recommendation must not read as solid."""
    import shutil
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    from failecho_autoreport import FailEcho
    answer = {"recommendation": {"action": "retry", "confidence": 0.89, "decaying": True,
                                 "scope": "service", "from_other_agents": False},
              "recovery_actions": [{"action": "retry", "successes": 100, "attempts": 105}]}
    line = FailEcho.advice_text(answer)
    for mark in ("recent attempts are failing", "evidence pooled across this service",
                 "your own history only", "evidence score 0.89"):
        assert mark in line, line
    out = subprocess.run(["node", "-e",
                          "const p=require(process.argv[1]);process.stdout.write(p.adviceText(JSON.parse(process.argv[2]))||'')",
                          str(ROOT / "npm-relay" / "bin" / "proxy.js"), json.dumps(answer)],
                         capture_output=True, timeout=30)
    assert out.stdout.decode() == line


def test_both_clients_share_one_installation_id(tmp_path):
    """One machine is one reporter, whichever client runs there: the Node
    proxy and the Python wrapper read and write the same file."""
    import shutil
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    env = {**os.environ, "XDG_STATE_HOME": str(tmp_path)}
    env.pop("FAILECHO_REPORTER_ID", None)
    node = subprocess.run(["node", "-e",
                           "process.stdout.write(require(process.argv[1]).installationId())",
                           str(ROOT / "npm-relay" / "bin" / "proxy.js")],
                          capture_output=True, timeout=30, env=env).stdout.decode()
    py = subprocess.run([sys.executable, "-c",
                         "import sys;sys.path.insert(0,%r);from failecho_autoreport import installation_id;"
                         "sys.stdout.write(installation_id())" % str(ROOT)],
                        capture_output=True, timeout=30, env=env).stdout.decode()
    assert node and node == py, (node, py)
    assert (tmp_path / "failecho" / "installation").read_text().strip() == node
