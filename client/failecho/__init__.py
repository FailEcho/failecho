"""FailEcho client -- live failure intelligence for autonomous software.

    from failecho import FailEcho

    echo = FailEcho(endpoint="https://failecho.com", reporter_id="my-agent-1")

    outcome = await echo.observe_tool_call(
        service="github-mcp",
        operation="create_issue",
        version="2.8.1",
        schema_hash="a817ce",
        call=lambda: github.create_issue(**args),
    )

    if outcome.failed and outcome.decision.actionable:
        # YOUR code decides whether to act on this.
        if outcome.decision.confidence > 0.8:
            refresh_schema()

Three guarantees, in order of importance:

1. **It never breaks your agent.** Every call is fail-soft: a timeout, a 500 or
   an unreachable host is swallowed and the tool result is returned anyway.
   FailEcho is never a hard dependency of your runtime. Set
   ``FAILECHO_DISABLED=1`` or ``enabled=False`` and everything becomes a no-op.
2. **It never performs recovery for you.** A failure returns a
   :class:`FailureDecision` describing what other agents observed. Executing a
   recovery can double-post, double-charge or corrupt state, so that call
   belongs to you.
3. **It sends metadata only.** Service, operation, version, schema hash,
   outcome, error type/code, a normalized error message and latency. Never
   prompts, arguments, results, bodies, headers, keys or user content.

Zero dependencies: standard library only.
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from auto_recovery import (  # noqa: F401  (re-exported for one obvious import)
    FailureDecision,
    ToolOutcome,
    classify_exception,
    run_with_failure_intelligence,
)
from failure_network import (  # noqa: F401  (the original sync REST client)
    UNKNOWN,
    Client,
    FailureNetworkError,
)

__all__ = [
    "Client",
    "FailEcho",
    "FailEchoError",
    "FailureDecision",
    "ToolOutcome",
    "classify_exception",
    "run_with_failure_intelligence",
    "UNKNOWN",
]

#: Preferred exception alias. Same object as the original, so either name works.
FailEchoError = FailureNetworkError

DEFAULT_ENDPOINT = "https://failecho.com"
DEFAULT_TIMEOUT = 3.0


@dataclass
class _Result:
    ok: bool
    body: dict[str, Any] | None = None


class FailEcho:
    """Report tool outcomes to FailEcho and ask it what others observed."""

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        reporter_id: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        enabled: bool | None = None,
        success_sample_rate: float = 1.0,
        demo: bool = False,
    ) -> None:
        """
        :param reporter_id: optional stable identifier for this agent or
            process. It is salted and hashed server-side and never stored raw.
            Supplying one is not required, but it is what lets FailEcho count
            you as one independent reporter rather than anonymous noise -- which
            improves evidence quality, poisoning resistance, and its ability to
            tell you that a recommendation came from somebody other than you.
        :param success_sample_rate: fraction of *successful* calls to report,
            1.0 by default. **Leave it at 1.0.** A failure rate is failures
            divided by total calls, and the network currently has far too few
            denominators; sampling is here so it can be turned down later, once
            volume makes that necessary. Failures and recovery outcomes are
            never sampled.
        :param demo: send ``X-Reporter-Kind: demo`` so this traffic is stored
            and usable but excluded from FailEcho's real-adoption metrics. Use
            it for examples and tutorials, never for production agents.
        :param enabled: master switch. Defaults to on unless
            ``FAILECHO_DISABLED`` is set in the environment.
        """
        self.endpoint = endpoint.rstrip("/")
        self.reporter_id = reporter_id
        self.timeout = timeout
        self.success_sample_rate = max(0.0, min(1.0, success_sample_rate))
        self.demo = demo
        if enabled is None:
            enabled = os.environ.get("FAILECHO_DISABLED", "") not in ("1", "true", "yes")
        self.enabled = enabled
        self._random = random.Random()

    # -- transport ---------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.reporter_id:
            headers["X-Reporter-ID"] = self.reporter_id
        if self.demo:
            headers["X-Reporter-Kind"] = "demo"
        return headers

    def _post_sync(self, path: str, payload: dict[str, Any]) -> _Result:
        body = json.dumps({k: v for k, v in payload.items() if v is not None}).encode()
        request = urllib.request.Request(
            f"{self.endpoint}{path}", data=body, method="POST", headers=self._headers()
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return _Result(True, json.loads(response.read().decode("utf-8")))
        except (urllib.error.URLError, OSError, ValueError):
            # Fail-soft, always. Telemetry must never break the caller.
            return _Result(False, None)

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        import asyncio

        result = await asyncio.to_thread(self._post_sync, path, payload)
        return result.body

    # -- the one call most agents need -------------------------------------
    async def observe_tool_call(
        self,
        *,
        service: str,
        operation: str,
        call: Callable[[], Awaitable[Any]],
        version: str | None = None,
        schema_hash: str | None = None,
        classify: Callable[[BaseException], tuple[str, str | None, str]] = classify_exception,
    ) -> ToolOutcome:
        """Run a tool call, report what happened, and on failure ask FailEcho.

        Success -> reports metadata, returns the result.
        Failure -> reports metadata, queries FailEcho, returns a
        :class:`FailureDecision` for **you** to act on. The tool's exception is
        attached to the outcome rather than raised, so callers keep one path.

        Never retries, never refreshes, never falls back on your behalf.
        """
        started = time.monotonic()
        try:
            result = await call()
        except Exception as exc:  # noqa: BLE001 - handed back on the outcome
            latency_ms = int((time.monotonic() - started) * 1000)
            error_type, error_code, error_message = classify(exc)

            await self.report_failure(
                service=service,
                operation=operation,
                version=version,
                schema_hash=schema_hash,
                error_type=error_type,
                error_code=error_code,
                error_message=error_message,
                latency_ms=latency_ms,
            )
            intelligence = await self.query(
                service=service,
                operation=operation,
                version=version,
                schema_hash=schema_hash,
                error_type=error_type,
                error_code=error_code,
                error_message=error_message,
            )
            return ToolOutcome(
                ok=False,
                error=exc,
                latency_ms=latency_ms,
                decision=_decision_from(intelligence),
            )

        latency_ms = int((time.monotonic() - started) * 1000)
        await self.report_success(
            service=service,
            operation=operation,
            version=version,
            schema_hash=schema_hash,
            latency_ms=latency_ms,
        )
        return ToolOutcome(ok=True, result=result, latency_ms=latency_ms)

    # -- the four primitives ------------------------------------------------
    async def report_success(
        self,
        *,
        service: str,
        operation: str,
        version: str | None = None,
        schema_hash: str | None = None,
        latency_ms: int | None = None,
    ) -> dict[str, Any] | None:
        """Report a successful call. This is the denominator of every rate."""
        if self.success_sample_rate < 1.0 and self._random.random() > self.success_sample_rate:
            return None
        return await self._post(
            "/v1/observe",
            {
                "service": service,
                "operation": operation,
                "version": version,
                "schema_hash": schema_hash,
                "outcome": "success",
                "latency_ms": latency_ms,
            },
        )

    async def report_failure(
        self,
        *,
        service: str,
        operation: str,
        version: str | None = None,
        schema_hash: str | None = None,
        error_type: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        latency_ms: int | None = None,
    ) -> dict[str, Any] | None:
        """Report a failed call. Never sampled."""
        return await self._post(
            "/v1/observe",
            {
                "service": service,
                "operation": operation,
                "version": version,
                "schema_hash": schema_hash,
                "outcome": "failure",
                "error_type": error_type,
                "error_code": error_code,
                "error_message": error_message,
                "latency_ms": latency_ms,
            },
        )

    async def query(
        self,
        *,
        service: str,
        operation: str,
        version: str | None = None,
        schema_hash: str | None = None,
        error_type: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        """Ask FailEcho about a failure. Always returns a dict.

        On transport failure it returns the neutral "nothing known" shape, so
        callers need no special case for "FailEcho is down".
        """
        result = await self._post(
            "/v1/query",
            {
                "service": service,
                "operation": operation,
                "version": version,
                "schema_hash": schema_hash,
                "error_type": error_type,
                "error_code": error_code,
                "error_message": error_message,
            },
        )
        return result if result is not None else dict(UNKNOWN)

    async def report_recovery(
        self, *, fingerprint: str, action: str, successful: bool
    ) -> dict[str, Any] | None:
        """Report whether a recovery action worked. Never sampled.

        This is the highest-value telemetry in the network: without it, FailEcho
        is an error counter.
        """
        return await self._post(
            "/v1/outcome",
            {
                "fingerprint": fingerprint,
                "action": action,
                "successful": successful,
            },
        )


def _decision_from(intelligence: dict[str, Any] | None) -> FailureDecision:
    intelligence = intelligence or {}
    recommendation = intelligence.get("recommendation") or {}
    observations = intelligence.get("observations") or {}
    return FailureDecision(
        fingerprint=intelligence.get("fingerprint", ""),
        known=bool(intelligence.get("known")),
        status=intelligence.get("status", "INSUFFICIENT_DATA"),
        recommendation=recommendation.get("action"),
        confidence=float(recommendation.get("confidence") or 0.0),
        observations=observations.get("total", 0),
        unique_reporters=observations.get("unique_reporters", 0),
        demo_data_included=bool(intelligence.get("demo_data_included")),
        intelligence=intelligence,
    )
