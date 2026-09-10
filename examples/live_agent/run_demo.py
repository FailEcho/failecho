"""One command that proves the network effect.

    python examples/live_agent/run_demo.py

What happens:

    Agents A, C, D, E, F each hit the same tool failure with a different
    repository id. None of them has evidence to go on, so each works its own
    playbook -- retry (fails), then refresh_schema (works) -- and reports both
    outcomes. Their messages are textually different; the network's normalizer
    collapses them onto one fingerprint.

    Agent B then hits the same failure with yet another id, asks the network
    first, and is told: refresh_schema, with a confidence derived from what the
    others actually observed. It skips the retry the others wasted a call on.

Every network call goes over MCP, from this external process, using the
official MCP SDK. Nothing here imports the server.

The failure-network server must already be running:

    uv run uvicorn app.main:app --reload
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents import OPERATION, SERVICE, DemoAgent, rule, say  # noqa: E402
from network import MCPNetworkClient  # noqa: E402
from tool_client import IssueTool  # noqa: E402

DEFAULT_NETWORK = "http://127.0.0.1:8000"
DEFAULT_TOOL = "http://127.0.0.1:8765"

#: Independent explorers that build the evidence, then the beneficiary.
#: Five explorers is not arbitrary: the recommendation threshold is 5 effective
#: attempts, so the demo shows evidence crossing it rather than starting past it.
EXPLORERS = [
    ("AGENT A", "demo-agent-a", "123456"),
    ("AGENT C", "demo-agent-c", "234567"),
    ("AGENT D", "demo-agent-d", "345678"),
    ("AGENT E", "demo-agent-e", "456789"),
    ("AGENT F", "demo-agent-f", "567890"),
]
BENEFICIARY = ("AGENT B", "demo-agent-b", "987654")


def http_json(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode())


def network_is_up(base_url: str) -> bool:
    try:
        return http_json(f"{base_url}/health").get("status") == "ok"
    except (urllib.error.URLError, OSError, ValueError):
        return False


class BackgroundToolServer:
    """Runs the demo tool server in a thread, so the demo is one command."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        import uvicorn

        import tool_server

        self._config = uvicorn.Config(
            tool_server.app, host=host, port=port, log_level="warning"
        )
        self._server = uvicorn.Server(self._config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self, timeout: float = 10.0) -> None:
        self._thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._server.started:
                return
            time.sleep(0.05)
        raise RuntimeError("demo tool server did not start")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


async def final_intelligence(network_url: str, tool_url: str) -> None:
    """Ask the network what it now knows -- over MCP, as any agent would."""
    tool = IssueTool(tool_url)
    say()
    rule("=")
    say("WHAT THE NETWORK LEARNED")
    rule("=")

    async with MCPNetworkClient("demo-observer", url=f"{network_url}/mcp") as network:
        intel = await network.check_tool_failure(
            service=SERVICE,
            operation=OPERATION,
            version=tool.schema.version,
            schema_hash=tool.schema.schema_hash,
            error_type="validation_error",
            error_code="422",
            # A repository id no agent in this demo ever used.
            error_message="Repository 111222 rejected field body: "
            'field "body" is no longer accepted, use "content"',
        )

    say()
    say(f"  Fingerprint:            {intel['fingerprint']}")
    say(f"  Known:                  {intel['known']}")
    say(f"  Status:                 {intel['status']}")
    say(f"  Observations (total):   {intel['observations']['total']}")
    say(f"  Independent reporters:  {intel['observations']['unique_reporters']}")
    say(f"  Failure rate (1h):      {intel['failure_rate']['last_1h']}")
    say()
    say("  Recovery evidence:")
    for action in intel["recovery_actions"]:
        say(
            f"    {action['action']:<22}"
            f"{action['successes']}/{action['attempts']} "
            f"({action['success_rate']:.1%})  "
            f"effective {action['effective_successes']}/{action['effective_attempts']}  "
            f"reporters {action['unique_reporters']}  "
            f"confidence {action['confidence']:.4f}"
        )
    recommendation = intel["recommendation"]
    say()
    if recommendation:
        say(f"  Recommendation:         {recommendation['action']} "
            f"(confidence {recommendation['confidence']:.4f}, "
            f"{recommendation['based_on_successes']}/{recommendation['based_on_attempts']} "
            f"from {recommendation['unique_reporters']} reporters)")
    else:
        say("  Recommendation:         none -- evidence below threshold")
    say(f"  Demo data included:     {intel['demo_data_included']}")

    stats = http_json(f"{network_url}/v1/stats")
    say()
    say("  Public stats:")
    say(f"    real observations (all time):  {stats['real_observations_total']}")
    say(f"    demo-agent observations:       {stats['demo_agent_observations']}")
    say(f"    seeded synthetic observations: {stats['synthetic_observations']}")
    say(f"    known fingerprints:            {stats['fingerprints_total']}")
    say(f"    recovery outcomes:             {stats['recovery_outcomes_total']}")
    say(f"    demo mode:                     {stats['demo_mode']}")
    say()
    say(f"  Homepage: {network_url}")


async def run(network_url: str, tool_url: str) -> int:
    mcp_url = f"{network_url}/mcp"

    say()
    rule("=")
    say("AGENT FAILURE INTELLIGENCE NETWORK -- LIVE AGENT DEMO")
    rule("=")
    say()
    say(f"  network (MCP):  {mcp_url}")
    say(f"  demo tool:      {tool_url}")

    async with MCPNetworkClient("demo-observer", url=mcp_url) as probe:
        tools = await probe.list_tools()
        say(f"  MCP server:     {probe.server_info.name} {probe.server_info.version}")
        say(f"  MCP tools:      {', '.join(tools)}")

    say()
    say("Phase 1 -- five independent agents meet an unknown failure.")
    say("Each works its own playbook and reports what happened.")

    for name, reporter_id, repository in EXPLORERS:
        await DemoAgent(
            name,
            reporter_id,
            repository,
            network_url=mcp_url,
            tool_url=tool_url,
            trust_network=False,
        ).run()

    say()
    rule("=")
    say("Phase 2 -- a sixth agent meets the same failure, and asks first.")
    rule("=")

    name, reporter_id, repository = BENEFICIARY
    beneficiary = await DemoAgent(
        name,
        reporter_id,
        repository,
        network_url=mcp_url,
        tool_url=tool_url,
        trust_network=True,
    ).run()

    await final_intelligence(network_url, tool_url)

    say()
    rule("=")
    if beneficiary.used_network_recommendation and beneficiary.recovered:
        say("RESULT: Agent B recovered using evidence it never generated itself.")
        say("        It never met Agent A. It only met the network.")
        rule("=")
        return 0
    say("RESULT: Agent B did not act on network evidence -- see output above.")
    rule("=")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network-url", default=DEFAULT_NETWORK)
    parser.add_argument("--tool-url", default=DEFAULT_TOOL)
    parser.add_argument(
        "--no-tool-server",
        action="store_true",
        help="do not start the demo tool server (it is already running)",
    )
    args = parser.parse_args()

    if not network_is_up(args.network_url):
        say(f"The failure network is not reachable at {args.network_url}.")
        say()
        say("Start it first:")
        say("    uv run uvicorn app.main:app --reload")
        say("or:")
        say("    .venv/bin/python -m uvicorn app.main:app --reload")
        return 1

    server: BackgroundToolServer | None = None
    if not args.no_tool_server:
        server = BackgroundToolServer(
            host=args.tool_url.split("//")[-1].split(":")[0],
            port=int(args.tool_url.rsplit(":", 1)[-1]),
        )
        server.start()

    try:
        return asyncio.run(run(args.network_url, args.tool_url))
    finally:
        if server is not None:
            server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
