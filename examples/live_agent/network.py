"""MCP client for FailEcho.

This is the point of the demo: an *external process* talking to the network
over MCP, using the official SDK. Nothing here imports the server's internals
-- if the MCP surface is broken, this file breaks with it.

    async with MCPNetworkClient(reporter_id="agent-a") as network:
        intel = await network.check_tool_failure(...)
"""

from __future__ import annotations

from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

DEFAULT_URL = "http://127.0.0.1:8000/mcp"


class MCPNetworkClient:
    """Thin async wrapper over the four network tools.

    Satisfies the ``FailureNetwork`` protocol in ``client/auto_recovery.py``,
    so the wrapper there can drive it without knowing it is MCP.
    """

    def __init__(
        self,
        reporter_id: str,
        url: str = DEFAULT_URL,
        demo: bool = True,
        timeout: float = 15.0,
    ) -> None:
        """
        :param demo: send ``X-Reporter-Kind: demo`` so this traffic is stored
            and usable as evidence but excluded from real-adoption metrics.
            Leave it on for anything that is not a real production agent.
        """
        self.reporter_id = reporter_id
        self.url = url
        self.demo = demo
        self.timeout = timeout
        self._stack: list = []
        self.session: ClientSession | None = None
        self.server_info: Any = None

    async def __aenter__(self) -> "MCPNetworkClient":
        headers = {"X-Reporter-Kind": "demo"} if self.demo else {}
        http = httpx2.AsyncClient(headers=headers, timeout=self.timeout)
        await http.__aenter__()
        self._stack.append(http)

        transport = streamable_http_client(self.url, http_client=http)
        read, write = await transport.__aenter__()
        self._stack.append(transport)

        session = ClientSession(read, write)
        await session.__aenter__()
        self._stack.append(session)

        result = await session.initialize()
        self.server_info = result.server_info
        self.session = session
        return self

    async def __aexit__(self, *exc_info) -> None:
        for resource in reversed(self._stack):
            try:
                await resource.__aexit__(*exc_info)
            except Exception:  # noqa: BLE001 - teardown must not mask errors
                pass
        self._stack.clear()
        self.session = None

    # -- tools -------------------------------------------------------------
    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert self.session is not None, "use MCPNetworkClient as an async context"
        payload = {k: v for k, v in arguments.items() if v is not None}
        result = await self.session.call_tool(tool, payload)
        if result.is_error:
            raise RuntimeError(f"MCP tool {tool} failed: {result.content}")
        return dict(result.structured_content or {})

    async def list_tools(self) -> list[str]:
        assert self.session is not None
        return [tool.name for tool in (await self.session.list_tools()).tools]

    async def check_tool_failure(self, **kwargs: Any) -> dict[str, Any]:
        return await self._call("check_tool_failure", kwargs)

    async def report_tool_failure(self, **kwargs: Any) -> dict[str, Any]:
        return await self._call(
            "report_tool_failure", {**kwargs, "reporter_id": self.reporter_id}
        )

    async def report_tool_success(self, **kwargs: Any) -> dict[str, Any]:
        return await self._call(
            "report_tool_success", {**kwargs, "reporter_id": self.reporter_id}
        )

    async def report_recovery_outcome(
        self, fingerprint: str, action: str, successful: bool
    ) -> dict[str, Any]:
        return await self._call(
            "report_recovery_outcome",
            {
                "fingerprint": fingerprint,
                "action": action,
                "successful": successful,
                "reporter_id": self.reporter_id,
            },
        )
