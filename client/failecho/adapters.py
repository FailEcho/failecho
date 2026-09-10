"""The seam a framework integration plugs into.

FailEcho deliberately ships *one* generic wrapper and *one* reference
integration rather than six half-maintained ones. Anything else -- LangChain,
LlamaIndex, CrewAI, the OpenAI Agents SDK, Claude Code hooks -- should
implement this protocol and nothing else. Core intelligence code is never
touched by an integration.

    class MyFrameworkSink:
        async def tool_started(self, call): ...
        async def tool_succeeded(self, call, latency_ms): ...
        async def tool_failed(self, call, latency_ms, error): ...
        async def recovery_reported(self, fingerprint, action, successful): ...

Four events, one direction, no return values that the framework has to honour.
An integration reports; it never decides. Recovery stays with the agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from failecho import FailEcho, FailureDecision, classify_exception


@dataclass(frozen=True)
class ToolCall:
    """What an integration knows about a tool call.

    Note what is absent: arguments, results, prompts, messages. An integration
    that adds them is a bug, not a feature -- FailEcho's schemas would drop
    them anyway, but they should never leave the customer's process.
    """

    service: str
    operation: str
    version: str | None = None
    schema_hash: str | None = None


@runtime_checkable
class ToolTelemetrySink(Protocol):
    """The four events any framework adapter must be able to emit."""

    async def tool_started(self, call: ToolCall) -> None: ...

    async def tool_succeeded(self, call: ToolCall, latency_ms: int) -> None: ...

    async def tool_failed(
        self, call: ToolCall, latency_ms: int, error: BaseException
    ) -> FailureDecision: ...

    async def recovery_reported(
        self, fingerprint: str, action: str, successful: bool
    ) -> None: ...


class FailEchoSink:
    """The default sink: forwards the four events to a :class:`FailEcho`.

    ``tool_started`` is intentionally a no-op. FailEcho records outcomes, not
    spans -- there is nothing useful to report before a call has an outcome,
    and reporting starts would double the write volume for no evidence.
    """

    def __init__(self, echo: FailEcho, classify=classify_exception) -> None:
        self.echo = echo
        self.classify = classify

    async def tool_started(self, call: ToolCall) -> None:
        return None

    async def tool_succeeded(self, call: ToolCall, latency_ms: int) -> None:
        await self.echo.report_success(
            service=call.service,
            operation=call.operation,
            version=call.version,
            schema_hash=call.schema_hash,
            latency_ms=latency_ms,
        )

    async def tool_failed(
        self, call: ToolCall, latency_ms: int, error: BaseException
    ) -> FailureDecision:
        error_type, error_code, error_message = self.classify(error)
        await self.echo.report_failure(
            service=call.service,
            operation=call.operation,
            version=call.version,
            schema_hash=call.schema_hash,
            error_type=error_type,
            error_code=error_code,
            error_message=error_message,
            latency_ms=latency_ms,
        )
        intelligence = await self.echo.query(
            service=call.service,
            operation=call.operation,
            version=call.version,
            schema_hash=call.schema_hash,
            error_type=error_type,
            error_code=error_code,
            error_message=error_message,
        )
        from failecho import _decision_from

        return _decision_from(intelligence)

    async def recovery_reported(
        self, fingerprint: str, action: str, successful: bool
    ) -> None:
        await self.echo.report_recovery(
            fingerprint=fingerprint, action=action, successful=successful
        )


def sink_for(echo: FailEcho | None = None, **kwargs: Any) -> FailEchoSink:
    """Convenience: build the default sink, creating a client if needed."""
    return FailEchoSink(echo or FailEcho(**kwargs))
