"""Experimental: wrap a tool call so failures become failure intelligence.

This is a sketch of how an agent framework might integrate the network. It is
deliberately small and deliberately **passive**.

    outcome = await run_with_failure_intelligence(
        tool_call=lambda: github.create_issue(**args),
        service="github-mcp",
        operation="create_issue",
        network=client,
    )

    if outcome.failed and outcome.decision.recommendation:
        # YOUR code decides whether to act on this.
        if outcome.decision.confidence > 0.8:
            refresh_schema()

What it does
------------
    call tool
      -> success: report the success, return the result
      -> failure: report the failure, query the network, return a
                  FailureDecision for the caller to act on

SAFETY: this wrapper never performs a recovery action. It does not retry, it
does not refresh anything, it does not call a fallback. Executing a recovery
can have side effects (double-posting, double-charging) that only the caller
can reason about, so the decision stays with the caller. Automatic recovery is
not a feature we are ready to ship.

``network`` is any object with the four async methods in
:class:`FailureNetwork` -- the MCP client in ``examples/live_agent`` satisfies
it, and so would a REST wrapper. This module imports nothing outside the
standard library.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

__all__ = [
    "FailureDecision",
    "FailureNetwork",
    "ToolOutcome",
    "classify_exception",
    "run_with_failure_intelligence",
    "send_error_text",
]


def send_error_text() -> bool:
    """Whether this process is configured to send raw error text.

    Read on every call rather than at import, so a test or a caller can turn
    it on and off without reloading the module.
    """
    return os.environ.get("FAILECHO_SEND_ERRORS", "").strip() in {"1", "true", "yes"}


@runtime_checkable
class FailureNetwork(Protocol):
    """The slice of the network this helper needs. Transport-agnostic."""

    async def report_tool_failure(self, **kwargs: Any) -> dict[str, Any]: ...

    async def report_tool_success(self, **kwargs: Any) -> dict[str, Any]: ...

    async def check_tool_failure(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class FailureDecision:
    """What the network knows, handed back for the caller to act on."""

    fingerprint: str
    known: bool
    status: str
    recommendation: str | None = None
    confidence: float = 0.0
    observations: int = 0
    unique_reporters: int = 0
    demo_data_included: bool = False
    #: The untouched tool response, so callers can read fields this dataclass
    #: does not model yet.
    intelligence: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def actionable(self) -> bool:
        """True when the network has an evidenced action to suggest."""
        return self.recommendation is not None


@dataclass
class ToolOutcome:
    """Result of one guarded tool call."""

    ok: bool
    result: Any = None
    error: BaseException | None = None
    decision: FailureDecision | None = None
    latency_ms: int = 0

    @property
    def failed(self) -> bool:
        return not self.ok


def classify_exception(exc: BaseException) -> tuple[str, str | None, str | None]:
    """Default classifier: ``(error_type, error_code, error_message)``.

    Understands objects that carry a ``status_code`` / ``code`` attribute (most
    HTTP client errors do). Override it with the ``classify`` argument when
    your tool raises something richer.

    PRIVACY: no error text by default. An exception's own message routinely
    quotes the thing that caused it -- the row it could not parse, the path it
    could not read, the argument it rejected -- so ``str(exc)`` is not
    metadata and sending it automatically is not a promise this network can
    keep. The class and the status code are, and they are what the
    fingerprint is built from anyway. Set ``FAILECHO_SEND_ERRORS=1`` to send
    the text as well, the way the Claude Code hook uses
    ``FAILECHO_HOOK_SEND_ERRORS``; it is normalized server-side and the raw
    string discarded, but the decision to send it is yours to make.
    """
    error_type = type(exc).__name__
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    message = str(exc) if send_error_text() else None
    return error_type, (str(code) if code is not None else None), message


async def run_with_failure_intelligence(
    *,
    tool_call: Callable[[], Awaitable[Any]],
    service: str,
    operation: str,
    network: FailureNetwork,
    version: str | None = None,
    schema_hash: str | None = None,
    classify: Callable[[BaseException], tuple[str, str | None, str]] = classify_exception,
    report_success: bool = True,
) -> ToolOutcome:
    """Call a tool; on failure, report it and return what the network knows.

    Never raises the tool's exception: it is returned on the outcome so the
    caller keeps one code path. Never performs recovery.
    """
    started = time.monotonic()
    try:
        result = await tool_call()
    except Exception as exc:  # noqa: BLE001 - the caller inspects it
        latency_ms = int((time.monotonic() - started) * 1000)
        error_type, error_code, error_message = classify(exc)

        await network.report_tool_failure(
            service=service,
            operation=operation,
            version=version,
            schema_hash=schema_hash,
            error_type=error_type,
            error_code=error_code,
            error_message=error_message,
            latency_ms=latency_ms,
        )
        intelligence = await network.check_tool_failure(
            service=service,
            operation=operation,
            version=version,
            schema_hash=schema_hash,
            error_type=error_type,
            error_code=error_code,
            error_message=error_message,
        )
        recommendation = (intelligence or {}).get("recommendation") or {}
        return ToolOutcome(
            ok=False,
            error=exc,
            latency_ms=latency_ms,
            decision=FailureDecision(
                fingerprint=(intelligence or {}).get("fingerprint", ""),
                known=bool((intelligence or {}).get("known")),
                status=(intelligence or {}).get("status", "INSUFFICIENT_DATA"),
                recommendation=recommendation.get("action"),
                confidence=float(recommendation.get("confidence") or 0.0),
                observations=((intelligence or {}).get("observations") or {}).get(
                    "total", 0
                ),
                unique_reporters=((intelligence or {}).get("observations") or {}).get(
                    "unique_reporters", 0
                ),
                demo_data_included=bool(
                    (intelligence or {}).get("demo_data_included")
                ),
                intelligence=intelligence or {},
            ),
        )

    latency_ms = int((time.monotonic() - started) * 1000)
    if report_success:
        # The denominator of every failure rate in the network.
        await network.report_tool_success(
            service=service,
            operation=operation,
            version=version,
            schema_hash=schema_hash,
            latency_ms=latency_ms,
        )
    return ToolOutcome(ok=True, result=result, latency_ms=latency_ms)
