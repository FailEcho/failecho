"""Reference integration: Pydantic AI.

Wraps a toolset so every tool call reports its outcome to FailEcho. The
wrapper is transparent: it returns exactly what the underlying toolset
returned, and re-raises exactly what it raised.

    from failecho import FailEcho
    from failecho.integrations.pydantic_ai import instrument_toolset

    echo = FailEcho("https://failecho.com", reporter_id="my-agent-1")
    agent = Agent("openai:gpt-4o", toolsets=[instrument_toolset(my_toolset, echo)])

For an agent you did not construct:

    async with instrument(agent, echo):
        result = await agent.run("...")

PRIVACY: only the tool *name* and the outcome leave the process. ``tool_args``
are never read, never forwarded and never logged -- they are exactly the kind
of thing FailEcho refuses to collect.

Requires ``pydantic-ai`` (or ``pydantic-ai-slim``) to be installed; FailEcho
itself does not depend on it.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

from failecho import FailEcho
from failecho.adapters import FailEchoSink, ToolCall, ToolTelemetrySink

try:  # pragma: no cover - exercised only when the framework is installed
    from pydantic_ai.toolsets import WrapperToolset
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "failecho.integrations.pydantic_ai requires pydantic-ai. "
        "Install it with: pip install pydantic-ai-slim"
    ) from exc


class FailEchoToolset(WrapperToolset):
    """A Pydantic AI toolset that reports every tool outcome to FailEcho.

    Behaviourally invisible: it times the call, forwards the result, and
    reports metadata out of band. It never retries and never swallows an
    exception -- the agent's control flow is unchanged.
    """

    def __init__(
        self,
        wrapped: Any,
        sink: ToolTelemetrySink | None = None,
        *,
        echo: FailEcho | None = None,
        service: str | None = None,
        version: str | None = None,
    ) -> None:
        super().__init__(wrapped=wrapped)
        if sink is None:
            sink = FailEchoSink(echo or FailEcho())
        # dataclass base: assign through object to avoid frozen/field clashes
        object.__setattr__(self, "sink", sink)
        object.__setattr__(self, "service", service or _default_service(wrapped))
        object.__setattr__(self, "version", version)

    async def call_tool(self, name: str, tool_args: dict, ctx: Any, tool: Any) -> Any:
        call = ToolCall(service=self.service, operation=name, version=self.version)
        started = time.monotonic()
        try:
            result = await super().call_tool(name, tool_args, ctx, tool)
        except Exception as error:
            latency_ms = int((time.monotonic() - started) * 1000)
            try:
                await self.sink.tool_failed(call, latency_ms, error)
            except Exception:  # noqa: BLE001 - telemetry must never mask a tool error
                pass
            raise
        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            await self.sink.tool_succeeded(call, latency_ms)
        except Exception:  # noqa: BLE001 - nor break a successful call
            pass
        return result


def _default_service(toolset: Any) -> str:
    """Name the toolset the way FailEcho names services."""
    for attribute in ("id", "name"):
        value = getattr(toolset, attribute, None)
        if isinstance(value, str) and value:
            return value
    return type(toolset).__name__


def instrument_toolset(
    toolset: Any,
    echo: FailEcho | None = None,
    *,
    service: str | None = None,
    version: str | None = None,
    sink: ToolTelemetrySink | None = None,
) -> FailEchoToolset:
    """Wrap one toolset. The usual entry point."""
    return FailEchoToolset(
        wrapped=toolset, sink=sink, echo=echo, service=service, version=version
    )


@asynccontextmanager
async def instrument(agent: Any, echo: FailEcho | None = None, **kwargs: Any):
    """Instrument every toolset on an existing agent, for the duration of a block.

    Uses the agent's own override mechanism, so nothing is mutated permanently.
    """
    wrapped = [instrument_toolset(t, echo, **kwargs) for t in agent.toolsets]
    with agent.override(toolsets=wrapped):
        yield agent
