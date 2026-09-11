"""FailEcho over stdio: a local MCP server that relays to the shared network.

Some MCP hosts can only start a local process and talk to it over
stdin/stdout -- Glama's hosting runner is one, and plenty of desktop clients
prefer it. FailEcho is one shared network at one URL, so this package does
not start a second FailEcho. It has no database and stores nothing. It relays
``tools/list`` and ``tools/call`` to the remote server and hands the answers
back unchanged, so a stdio client and an HTTP client see the same tools, the
same descriptions and the same evidence.

    failecho-mcp                                           # the public network
    FAILECHO_URL=http://localhost:8000/mcp failecho-mcp    # your own server

Kept apart from ``app`` on purpose: it imports only the MCP SDK and the
standard library, so running it never pulls in the server's database stack,
and it can move into its own distribution without edits.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.exceptions import MCPError

__version__ = "0.1.0"

#: The shared network. A client's default has to be the real network: a relay
#: that defaulted to localhost would start an empty, private FailEcho in every
#: container, which is the one outcome this package exists to prevent.
#: Must match the remote declared in server.json -- a test holds them together.
DEFAULT_URL = "https://failecho.com/mcp"

#: Seconds to wait for the network while starting. Hosts give a server about
#: a minute to answer ``initialize``; this leaves most of that to spare.
STARTUP_TIMEOUT_SECONDS = 15.0

#: Label a demo agent sends so its traffic stays out of the adoption numbers.
#: Mirrors app.core.config.REPORTER_KIND_HEADER without importing the server.
REPORTER_KIND_HEADER = "X-Reporter-Kind"

#: JSON-RPC "Internal error".
_INTERNAL_ERROR = -32603

#: Used only if the network was unreachable at startup. The live instructions
#: come from the server itself, so the two cannot drift while it is up.
FALLBACK_INSTRUCTIONS = (
    "FailEcho is a shared failure-intelligence network for AI agents. When a "
    "tool call fails, call check_tool_failure before retrying to see whether "
    "other agents hit the same failure and which recovery worked for them. "
    "Report failures, successes and recovery outcomes so the network stays "
    "useful. Send failure metadata only -- never prompts, tool arguments, "
    "tool results, request or response bodies, or credentials."
)

log = logging.getLogger("failecho_mcp")


def _describe(exc: BaseException) -> str:
    """The root cause, not the task-group wrapper around it.

    Transport failures surface as "unhandled errors in a TaskGroup", which
    tells an agent -- or whoever reads the log -- nothing about what broke.
    """
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


class Relay:
    """Forwards MCP tool traffic from a local client to the FailEcho network."""

    def __init__(self, url: str = DEFAULT_URL, reporter_kind: str | None = None) -> None:
        self.url = url
        # Identifies relayed traffic in the server's logs, so adoption through
        # this path can be counted honestly rather than guessed.
        self.headers = {"User-Agent": f"failecho-mcp/{__version__}"}
        if reporter_kind:
            self.headers[REPORTER_KIND_HEADER] = reporter_kind
        self._tools: list[types.Tool] | None = None
        self._init: types.InitializeResult | None = None

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[ClientSession]:
        """One short-lived upstream session per operation.

        The remote server is stateless, so there is nothing to keep alive
        between calls, and a fresh connection can never have gone stale.
        """
        async with create_mcp_http_client(headers=self.headers) as http:
            async with streamable_http_client(self.url, http_client=http) as (read, write):
                async with ClientSession(read, write) as session:
                    self._init = await session.initialize()
                    yield session

    async def _fetch_tools(self) -> list[types.Tool]:
        async with self._session() as session:
            result = await session.list_tools()
        self._tools = list(result.tools)
        return self._tools

    async def warm(self, timeout: float = STARTUP_TIMEOUT_SECONDS) -> bool:
        """Fetch the network's instructions and tools before serving.

        Failure is not fatal. The relay still starts and ``tools/list``
        retries, so a host that cannot reach the network right now gets a
        clear error on its first call instead of a server that never answers.
        """
        try:
            with anyio.fail_after(timeout):
                await self._fetch_tools()
            return True
        except Exception as exc:  # noqa: BLE001 - any failure here means "offline"
            log.warning("FailEcho network unreachable at %s: %s", self.url, _describe(exc))
            return False

    async def list_tools(
        self, ctx: Any, params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        if self._tools is None:
            try:
                await self._fetch_tools()
            except Exception as exc:  # noqa: BLE001
                raise MCPError(
                    _INTERNAL_ERROR, f"FailEcho network unreachable at {self.url}: {_describe(exc)}"
                ) from exc
        return types.ListToolsResult(tools=self._tools)

    async def call_tool(
        self, ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        try:
            async with self._session() as session:
                return await session.call_tool(params.name, params.arguments or {})
        except Exception as exc:  # noqa: BLE001
            # An agent calls FailEcho while it is already handling a failure.
            # An unreachable network must not become a second one: say so
            # plainly and let the agent fall back to its own retry policy.
            log.warning("tool call %s failed to reach %s: %s", params.name, self.url, _describe(exc))
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=(
                            f"FailEcho network unreachable at {self.url}: {_describe(exc)}. "
                            "Nothing was recorded and no evidence was returned; "
                            "fall back to your own retry policy."
                        ),
                    )
                ],
                is_error=True,
            )

    def build_server(self) -> Server:
        """A local MCP server that presents itself exactly as the network does."""
        info = self._init.server_info if self._init else None
        return Server(
            getattr(info, "name", None) or "failecho",
            version=getattr(info, "version", None) or __version__,
            title=getattr(info, "title", None) or "FailEcho",
            instructions=(self._init.instructions if self._init else None)
            or FALLBACK_INSTRUCTIONS,
            website_url=getattr(info, "website_url", None),
            on_list_tools=self.list_tools,
            on_call_tool=self.call_tool,
        )


async def serve(url: str = DEFAULT_URL, reporter_kind: str | None = None) -> None:
    relay = Relay(url, reporter_kind)
    await relay.warm()
    server = relay.build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    # stdout carries the MCP protocol itself; every diagnostic goes to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="failecho-mcp: %(message)s")
    for noisy in ("httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    url = os.environ.get("FAILECHO_URL") or DEFAULT_URL
    reporter_kind = os.environ.get("FAILECHO_REPORTER_KIND") or None
    log.info("relaying to %s", url)
    try:
        anyio.run(serve, url, reporter_kind)
    except KeyboardInterrupt:
        pass
