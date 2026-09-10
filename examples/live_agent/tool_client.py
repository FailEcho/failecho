"""The agent's side of the tool: an HTTP client with a *cached* schema.

The cache is the whole point. Agents do not re-read a tool schema on every
call, so when a provider renames a field the agent keeps sending the old one
until something makes it refresh. ``refresh_schema()`` is that something, and
whether it helps is exactly what the failure network learns.

Standard library only -- no extra dependency to run the demo.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass


class ToolCallError(Exception):
    """A failed tool call, carrying the fields telemetry needs."""

    def __init__(self, status_code: int, error_type: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.message = message


@dataclass
class CachedSchema:
    version: str
    schema_hash: str
    content_field: str


class IssueTool:
    """``create_issue`` on the demo tool server, with a stale cached schema."""

    def __init__(self, base_url: str = "http://127.0.0.1:8765") -> None:
        self.base_url = base_url.rstrip("/")
        # Deliberately stale: the agent was built against v2, which took "body".
        self.schema = CachedSchema(
            version="2.0.0", schema_hash="v2body", content_field="body"
        )

    # -- transport ---------------------------------------------------------
    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode() or "{}")
            raise ToolCallError(
                status_code=exc.code,
                error_type=body.get("error", "http_error"),
                message=body.get("message", str(exc)),
            ) from None

    # -- operations --------------------------------------------------------
    async def create_issue(self, repository: str, text: str) -> dict:
        """Call the tool using whatever field name the cached schema says."""
        payload = {"repository": repository, self.schema.content_field: text}
        return await asyncio.to_thread(self._request, "POST", "/issues", payload)

    async def refresh_schema(self) -> CachedSchema:
        """The recovery action: re-read the tool schema and adopt it."""
        fresh = await asyncio.to_thread(self._request, "GET", "/schema", None)
        content_field = [f for f in fresh["fields"] if f != "repository"][0]
        self.schema = CachedSchema(
            version=fresh["version"],
            schema_hash=fresh["schema_hash"],
            content_field=content_field,
        )
        return self.schema

    async def ping(self) -> bool:
        try:
            await asyncio.to_thread(self._request, "GET", "/state", None)
            return True
        except Exception:  # noqa: BLE001
            return False
