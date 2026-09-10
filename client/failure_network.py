"""Tiny client for FailEcho (importable as ``failecho``).

Zero dependencies -- stdlib ``urllib`` only -- so dropping it into an agent
never drags in a transitive dependency tree.

Design rule: **telemetry must never break the agent it observes.** Every call
is fail-soft by default: a network error, a timeout or a 5xx returns a neutral
value instead of raising. Pass ``fail_soft=False`` to opt into exceptions.

    from failure_network import Client

    client = Client("http://localhost:8000")

    client.observe_failure(
        service="github-mcp",
        operation="create_issue",
        version="2.8.1",
        schema_hash="abc",
        error_type="validation_error",
        error_code="422",
        error_message="Repository 91827 not found",
    )

    intel = client.query(
        service="github-mcp",
        operation="create_issue",
        version="2.8.1",
        schema_hash="abc",
        error_type="validation_error",
        error_code="422",
        error_message="Repository 12345 not found",
    )
    print(intel)

PRIVACY: only the fields below are ever transmitted. Never pass prompts, tool
arguments, tool results or secrets in ``error_message``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

__all__ = ["Client", "FailureNetworkError"]

DEFAULT_TIMEOUT = 3.0

#: Returned by :meth:`Client.query` when the network cannot be reached, so
#: callers can use one code path for "no data" and "no network".
UNKNOWN: dict[str, Any] = {
    "known": False,
    "status": "INSUFFICIENT_DATA",
    "recommendation": None,
    "recovery_actions": [],
}


class FailureNetworkError(RuntimeError):
    """Raised only when ``fail_soft=False``."""


class Client:
    """Minimal synchronous client for the three network endpoints."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        reporter_id: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        fail_soft: bool = True,
    ) -> None:
        """
        :param reporter_id: optional stable identifier for this agent/process.
            Sent as ``X-Reporter-ID`` and salted+hashed server-side; it is
            never stored raw. Omit it to stay fully anonymous.
        """
        self.base_url = base_url.rstrip("/")
        self.reporter_id = reporter_id
        self.timeout = timeout
        self.fail_soft = fail_soft

    # -- transport ---------------------------------------------------------
    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        body = json.dumps(
            {k: v for k, v in payload.items() if v is not None}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        if self.reporter_id:
            request.add_header("X-Reporter-ID", self.reporter_id)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            if self.fail_soft:
                return None
            raise FailureNetworkError(f"POST {path} failed: {exc}") from exc

    # -- write side --------------------------------------------------------
    def observe_failure(
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
        """Report a failed tool call. Returns the fingerprint envelope."""
        return self._post(
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

    def observe_success(
        self,
        *,
        service: str,
        operation: str,
        version: str | None = None,
        schema_hash: str | None = None,
        latency_ms: int | None = None,
    ) -> dict[str, Any] | None:
        """Report a successful tool call -- the denominator of every rate."""
        return self._post(
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

    def report_recovery(
        self, *, fingerprint: str, action: str, successful: bool
    ) -> dict[str, Any] | None:
        """Report whether a recovery action worked."""
        return self._post(
            "/v1/outcome",
            {
                "fingerprint": fingerprint,
                "action": action,
                "successful": successful,
            },
        )

    # -- read side ---------------------------------------------------------
    def query(
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
        """Ask the network about a failure. Call this *before* retrying.

        Always returns a dict; on transport failure it returns :data:`UNKNOWN`
        so the caller can keep one branch for "the network has no opinion".
        """
        result = self._post(
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
