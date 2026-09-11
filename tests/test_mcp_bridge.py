"""The stdio relay: a local MCP server that forwards to the shared network.

These run against a real FailEcho server on a real port. The relay's whole
job is the network hop, so an in-process shortcut would test nothing.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
from pathlib import Path

import anyio
import pytest
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

import failecho_mcp
from failecho_mcp import DEFAULT_URL, REPORTER_KIND_HEADER, Relay

ROOT = Path(__file__).resolve().parents[1]
TOOLS = {
    "check_tool_failure",
    "report_tool_failure",
    "report_tool_success",
    "report_recovery_outcome",
}
FAILURE = {
    "service": "relay-test-api",
    "operation": "create_widget",
    "error_type": "not_found",
    "error_code": "404",
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def upstream():
    """A real FailEcho server with its own database, reachable over HTTP."""
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    workdir = Path(tempfile.mkdtemp(prefix="fin-relay-"))
    env = {
        **os.environ,
        "FIN_DATABASE_URL": f"sqlite+aiosqlite:///{workdir / 'upstream.db'}",
        "FIN_PUBLIC_URL": base,
        "FIN_DASHBOARD_CACHE_SECONDS": "0",
        "FIN_REPORTER_SALT": "relay-test-salt",
    }
    log = (workdir / "upstream.log").open("w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port),
         "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                urllib.request.urlopen(f"{base}/health", timeout=1)
                break
            except OSError:
                if time.monotonic() > deadline or proc.poll() is not None:
                    log.flush()
                    raise RuntimeError(
                        "upstream FailEcho did not start:\n"
                        + (workdir / "upstream.log").read_text()
                    ) from None
                time.sleep(0.2)
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        log.close()


def through(relay: Relay, action):
    """Run ``action(session)`` against the relay, as a local MCP client would."""

    async def run():
        async with InMemoryTransport(relay.build_server()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await action(session)

    return anyio.run(run)


def direct(base: str, action):
    """Run ``action(session)`` against the network itself, over HTTP."""

    async def run():
        async with streamable_http_client(f"{base}/mcp") as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await action(session)

    return anyio.run(run)


def warmed(url: str, **kwargs) -> Relay:
    relay = Relay(url, **kwargs)
    assert anyio.run(relay.warm), "relay could not reach the test server"
    return relay


def stats(base: str) -> dict:
    with urllib.request.urlopen(f"{base}/v1/stats", timeout=5) as response:
        return json.load(response)


# ---------------------------------------------------------------------------
# parity: the relay adds nothing and hides nothing
# ---------------------------------------------------------------------------


def test_relay_serves_exactly_the_tools_the_network_serves(upstream):
    relay = warmed(f"{upstream}/mcp")

    async def tools(session):
        return [tool.model_dump() for tool in (await session.list_tools()).tools]

    relayed = through(relay, tools)
    assert {tool["name"] for tool in relayed} == TOOLS
    # Names, titles, descriptions, input and output schemas, annotations.
    assert relayed == direct(upstream, tools)


def test_relay_presents_itself_as_the_network(upstream):
    """A host sees FailEcho's own name and instructions, not a wrapper's."""
    relay = warmed(f"{upstream}/mcp")

    async def identity(session):
        result = session.initialize_result
        return result.server_info.name, result.server_info.version, result.instructions

    relayed = through(relay, identity)
    assert relayed == direct(upstream, identity)
    assert relayed[0] == "failecho"


# ---------------------------------------------------------------------------
# the property the relay exists for: nothing stays local
# ---------------------------------------------------------------------------


def test_relayed_reports_land_in_the_shared_network(upstream):
    relay = warmed(f"{upstream}/mcp")
    before = stats(upstream)["real_observations_total"]

    async def report(session):
        return await session.call_tool(
            "report_tool_failure",
            {**FAILURE, "error_message": "Widget 918272 was not found"},
        )

    reported = through(relay, report)
    assert not reported.is_error, reported.content

    # A different agent, connected straight to the network, sees it -- and a
    # different widget id collapses to the same fingerprint.
    async def check(session):
        result = await session.call_tool(
            "check_tool_failure",
            {**FAILURE, "error_message": "Widget 555812 was not found"},
        )
        return result.structured_content

    seen = direct(upstream, check)
    assert seen["known"] is True
    assert seen["observations"]["total"] >= 1
    assert seen["fingerprint"] == reported.structured_content["fingerprint"]
    assert stats(upstream)["real_observations_total"] == before + 1


def test_the_demo_label_travels_through_the_relay(upstream):
    """FAILECHO_REPORTER_KIND=demo keeps relayed demo traffic out of adoption."""
    before = stats(upstream)
    relay = warmed(f"{upstream}/mcp", reporter_kind="demo")

    through(
        relay,
        lambda session: session.call_tool(
            "report_tool_failure", {**FAILURE, "operation": "demo_widget"}
        ),
    )

    after = stats(upstream)
    assert after["demo_agent_observations"] == before["demo_agent_observations"] + 1
    assert after["real_observations_total"] == before["real_observations_total"]


def test_the_relay_never_loads_a_database():
    """It stores nothing because it cannot: the storage stack is never imported."""
    probe = (
        "import sys, failecho_mcp; "
        "print(sorted(m for m in ('app', 'sqlalchemy', 'aiosqlite') if m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


# ---------------------------------------------------------------------------
# failure: an outage must not become a second failure
# ---------------------------------------------------------------------------


def test_an_unreachable_network_is_an_answer_not_a_hang():
    relay = Relay(f"http://127.0.0.1:{_free_port()}/mcp")

    started = time.monotonic()
    assert anyio.run(relay.warm) is False
    assert time.monotonic() - started < 5

    async def call(session):
        return await session.call_tool(
            "check_tool_failure", {"service": "x", "operation": "y"}
        )

    result = through(relay, call)
    assert result.is_error is True
    text = result.content[0].text
    assert "unreachable" in text
    assert "Nothing was recorded" in text
    # The root cause, not the task-group wrapper around it.
    assert "TaskGroup" not in text

    async def list_tools(session):
        try:
            await session.list_tools()
        except MCPError as exc:
            return str(exc)
        return None

    assert "unreachable" in (through(relay, list_tools) or "")


# ---------------------------------------------------------------------------
# the shape hosts actually run
# ---------------------------------------------------------------------------


def test_stdio_process_end_to_end(upstream):
    """A child process speaking MCP on stdin/stdout, as Glama's runner starts it."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "failecho_mcp"],
        env={**os.environ, "FAILECHO_URL": f"{upstream}/mcp"},
        cwd=str(ROOT),
    )

    async def run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                tools = await session.list_tools()
                return init.server_info.name, {tool.name for tool in tools.tools}

    name, names = anyio.run(run)
    assert name == "failecho"
    assert names == TOOLS


def test_packaging_exposes_the_relay_command():
    """What uvx and Glama's runner invoke: `failecho-mcp`, from an installed package."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["scripts"]["failecho-mcp"] == "failecho_mcp:main"
    packages = project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert "failecho_mcp" in packages
    assert project["project"]["version"] == failecho_mcp.__version__


def test_default_is_the_network_declared_to_registries():
    """The relay's default and server.json's remote are one URL, not two."""
    manifest = json.loads((ROOT / "server.json").read_text())
    assert DEFAULT_URL in {remote["url"] for remote in manifest["remotes"]}


def test_relay_identifies_itself_and_mirrors_the_server_label():
    from app.core.config import REPORTER_KIND_HEADER as SERVER_HEADER

    assert REPORTER_KIND_HEADER == SERVER_HEADER

    plain = Relay("http://example.invalid/mcp")
    assert plain.headers["User-Agent"] == f"failecho-mcp/{failecho_mcp.__version__}"
    assert REPORTER_KIND_HEADER not in plain.headers

    demo = Relay("http://example.invalid/mcp", reporter_kind="demo")
    assert demo.headers[REPORTER_KIND_HEADER] == "demo"
