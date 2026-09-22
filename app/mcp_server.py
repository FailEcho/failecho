"""FailEcho's MCP server: cross-agent failure intelligence for AI agents.

Thin by design: every tool validates its input with the same Pydantic models
the REST API uses and then calls the same functions in
:mod:`app.core.service`. There is no second copy of the normalization,
fingerprinting or intelligence logic, so an MCP client and a curl user cannot
disagree about what a failure means.

Transport: Streamable HTTP, mounted inside the existing FastAPI app at
``/mcp``. Stateless mode with JSON responses -- no per-session memory, no
long-lived SSE streams to hold open, which is what makes it viable on a small
VPS. No separate process to run or supervise.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.requests import ClientDisconnect
from starlette.routing import Route

from app.core.config import (
    OPERATOR_BEARER_HEADER,
    OPERATOR_HEADER,
    REPORTER_KIND_HEADER,
    settings,
)
from app.core.privacy import hash_reporter_id
from app.core.ratelimit import check_write_limit, client_key
from app.core.service import (
    compact_query,
    operator_token_from,
    query_intelligence,
    record_observation,
    record_recovery_outcome,
    source_from_kind,
)
from app.db.database import SessionLocal
from app.schemas.common import OPERATION_NAMING, SERVICE_NAMING
from app.schemas.observe import ObserveRequest
from app.schemas.outcome import OutcomeRequest
from app.schemas.query import QueryRequest

INSTRUCTIONS = """
FailEcho provides live cross-agent failure intelligence: what other agents
saw when a tool failed, and what fixed it. Use it when another tool fails,
before retrying blindly. Call check_tool_failure BEFORE retrying:
it returns whether others are failing the same way now, the recovery actions
that worked, and one recommendation -- or `skip` when nothing has.
Close the loop: report_tool_failure on a failure, report_tool_success on a
success (rates need a denominator), report_recovery_outcome after you try
a fix. Name things as the server does: service = serverInfo.name or the API
host, operation = the tool or endpoint name. Send failure metadata only --
never prompts, arguments, results, bodies, headers, keys or user content;
error text is normalized server-side and the raw string discarded.
""".strip()

mcp_server = MCPServer(
    name="failecho",
    title="FailEcho",
    version="0.1.0",
    instructions=INSTRUCTIONS,
    website_url=settings.base_url(),
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _reporter_from(ctx: Context | None, reporter_id: str | None) -> str | None:
    """Hash the caller-supplied reporter id. Raw ids are never stored."""
    return hash_reporter_id(reporter_id)


def _request_headers(ctx: Context | None) -> dict:
    request = getattr(getattr(ctx, "request_context", None), "request", None)
    if request is None:
        return {}
    return {str(k).lower(): v for k, v in dict(request.headers).items()}


def _source_from(ctx: Context | None) -> str:
    """Honour the self-label headers sent by the MCP client.

    Demo agents label themselves (``X-Reporter-Kind: demo``) and the
    operator's own agents prove themselves (``X-FailEcho-Operator``), so
    neither ever counts as real adoption while both stay usable evidence.
    """
    headers = _request_headers(ctx)
    return source_from_kind(
        headers.get(REPORTER_KIND_HEADER.lower()),
        operator_token_from(
            headers.get(OPERATOR_HEADER.lower()),
            headers.get(OPERATOR_BEARER_HEADER.lower()),
        ),
    )


def _rate_limit_key(ctx: Context | None) -> str:
    """Key MCP writes into the *same* budget as REST writes from that client.

    Deliberately not namespaced by transport: switching from REST to MCP must
    not hand an agent a second budget. Falls back to a shared ``unknown``
    bucket when the transport exposes no peer address (e.g. stdio), which is
    conservative rather than open.
    """
    request = getattr(getattr(ctx, "request_context", None), "request", None)
    peer = None
    if request is not None and getattr(request, "client", None):
        peer = request.client.host
    return client_key(peer or "unknown", _request_headers(ctx))


def _rate_limited(ctx: Context | None) -> dict[str, Any] | None:
    allowed, retry_after = check_write_limit(_rate_limit_key(ctx))
    if allowed:
        return None
    return {
        "accepted": False,
        "error": "rate_limited",
        "retry_after_seconds": retry_after,
        "detail": (
            "Write rate limit exceeded for this client. Reads "
            "(check_tool_failure) are never rate limited."
        ),
    }


# ---------------------------------------------------------------------------
# Tool 1 -- the reason this server exists
# ---------------------------------------------------------------------------


@mcp_server.tool(
    name="check_tool_failure",
    title="Check what the network knows about a tool failure",
    description=(
        "Use FailEcho when another tool fails, before retrying blindly. Call this "
        "BEFORE retrying a failed tool, API or MCP call. Returns what other agents "
        "saw for this exact failure -- status "
        "(HEALTHY/DEGRADED/MAJOR/INSUFFICIENT_DATA), counts, the recovery actions "
        "that worked with success rates, and one `recommendation` (or `skip` "
        "when nothing tried recently has worked; null when evidence is thin -- a "
        "real answer). Works on your own history before anyone else joins. Free, "
        "anonymous, stores nothing, never rate limited."
    ),
)
async def check_tool_failure(
    service: Annotated[
        str,
        Field(description=SERVICE_NAMING, max_length=128),
    ],
    operation: Annotated[
        str,
        Field(
            description=OPERATION_NAMING,
            max_length=128,
        ),
    ],
    error_type: Annotated[
        str | None,
        Field(
            description="Failure class, e.g. 'validation_error', 'timeout', 'rate_limit', 'auth_error'.",
            max_length=64,
        ),
    ] = None,
    error_message: Annotated[
        str | None,
        Field(
            description="The error text only; normalized server-side, not stored by this call.",
            max_length=2000,
        ),
    ] = None,
    error_code: Annotated[
        str | None,
        Field(description="Code, e.g. '422', 'ECONNRESET'.", max_length=32),
    ] = None,
    version: Annotated[
        str | None,
        Field(description="Service/tool version, if known.", max_length=64),
    ] = None,
    schema_hash: Annotated[
        str | None,
        Field(
            description="Short hash of the tool schema you used (separates 'API broke' from 'schema stale').",
            max_length=64,
        ),
    ] = None,
    reporter_id: Annotated[
        str | None,
        Field(
            description="Optional stable agent id; hashed, never stored raw. Lets from_other_agents be answered.",
            max_length=200,
        ),
    ] = None,
    verbose: Annotated[
        bool,
        Field(
            description="Full record (timestamps, rates, effective counts, every action). Default: the compact answer."
        ),
    ] = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Query FailEcho for live intelligence about a failure."""
    payload = QueryRequest(
        service=service,
        operation=operation,
        version=version,
        schema_hash=schema_hash,
        error_type=error_type,
        error_code=error_code,
        error_message=error_message,
    )
    async with SessionLocal() as session:
        result = await query_intelligence(
            session,
            payload,
            reporter_hash=_reporter_from(ctx, reporter_id),
            source=_source_from(ctx),
        )
    return result.model_dump() if verbose else compact_query(result)


# ---------------------------------------------------------------------------
# Tool 2 -- contribute a failure
# ---------------------------------------------------------------------------


@mcp_server.tool(
    name="report_tool_failure",
    title="Report a failed tool call to the network",
    description=(
        "Report a failed tool/API/MCP call so others can recognise it; call after "
        "a failure, alongside check_tool_failure.\n\n"
        "PRIVACY: this is a shared network. Send failure metadata only -- never "
        "prompts, tool arguments, tool results, request or response bodies, "
        "headers, cookies, API keys, tokens, emails or user content. Error text "
        "is normalized server-side and the raw string discarded. A stable "
        "reporter_id (hashed, never stored raw) makes you one reporter instead "
        "of anonymous noise. Writes are rate limited per client."
    ),
)
async def report_tool_failure(
    service: Annotated[str, Field(description=SERVICE_NAMING, max_length=128)],
    operation: Annotated[str, Field(description=OPERATION_NAMING, max_length=128)],
    error_type: Annotated[
        str | None,
        Field(description="Short failure class, e.g. 'validation_error'.", max_length=64),
    ] = None,
    error_message: Annotated[
        str | None,
        Field(
            description="Error text. Normalized before storage; no secrets please.",
            max_length=2000,
        ),
    ] = None,
    error_code: Annotated[
        str | None, Field(description="Protocol/vendor code, e.g. '422'.", max_length=32)
    ] = None,
    version: Annotated[
        str | None, Field(description="Version of the service/tool.", max_length=64)
    ] = None,
    schema_hash: Annotated[
        str | None, Field(description="Short hash of the tool schema used.", max_length=64)
    ] = None,
    latency_ms: Annotated[
        int | None, Field(description="Observed call latency in milliseconds.", ge=0)
    ] = None,
    reporter_id: Annotated[
        str | None,
        Field(
            description=(
                "Optional stable identifier for your agent. Salted and hashed "
                "on arrival; never stored raw."
            ),
            max_length=200,
        ),
    ] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Store a failure observation and return its fingerprint."""
    limited = _rate_limited(ctx)
    if limited:
        return limited

    payload = ObserveRequest(
        service=service,
        operation=operation,
        version=version,
        schema_hash=schema_hash,
        outcome="failure",
        error_type=error_type,
        error_code=error_code,
        error_message=error_message,
        latency_ms=latency_ms,
    )
    async with SessionLocal() as session:
        result = await record_observation(
            session,
            payload,
            reporter_hash=_reporter_from(ctx, reporter_id),
            source=_source_from(ctx),
        )
    return {
        "accepted": result.accepted,
        "fingerprint": result.fingerprint,
        "known": result.known,
        "observations": result.observations,
        "normalized_error": result.normalized_error,
    }


# ---------------------------------------------------------------------------
# Tool 3 -- contribute a success
# ---------------------------------------------------------------------------


@mcp_server.tool(
    name="report_tool_success",
    title="Report a successful tool call to the network",
    description=(
        "Report that a call SUCCEEDED. Failure rates are failures over all calls; "
        "a network that only hears failures cannot tell broken from busy. No "
        "error data -- service, operation, version, latency."
    ),
)
async def report_tool_success(
    service: Annotated[str, Field(description=SERVICE_NAMING, max_length=128)],
    operation: Annotated[
        str, Field(description=OPERATION_NAMING, max_length=128)
    ],
    version: Annotated[
        str | None, Field(description="Version of the service/tool.", max_length=64)
    ] = None,
    schema_hash: Annotated[
        str | None, Field(description="Short hash of the tool schema used.", max_length=64)
    ] = None,
    latency_ms: Annotated[
        int | None, Field(description="Observed call latency in milliseconds.", ge=0)
    ] = None,
    reporter_id: Annotated[
        str | None,
        Field(description="Optional stable agent id; hashed, never stored raw.", max_length=200),
    ] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Store a success observation."""
    limited = _rate_limited(ctx)
    if limited:
        return limited

    payload = ObserveRequest(
        service=service,
        operation=operation,
        version=version,
        schema_hash=schema_hash,
        outcome="success",
        latency_ms=latency_ms,
    )
    async with SessionLocal() as session:
        await record_observation(
            session,
            payload,
            reporter_hash=_reporter_from(ctx, reporter_id),
            source=_source_from(ctx),
        )
    return {"accepted": True}


# ---------------------------------------------------------------------------
# Tool 4 -- close the loop
# ---------------------------------------------------------------------------


@mcp_server.tool(
    name="report_recovery_outcome",
    title="Report whether a recovery action worked",
    description=(
        "After acting on a failure -- retry, wait, refresh_schema, reconnect, "
        "use_fallback, reauthenticate -- report whether it worked. Every "
        "recommendation others get is built from these. Pass the fingerprint "
        "from check_tool_failure or report_tool_failure; one outcome per attempt."
    ),
)
async def report_recovery_outcome(
    fingerprint: Annotated[
        str,
        Field(
            description="From check_tool_failure or report_tool_failure (32 hex chars).",
            pattern=r"^[0-9a-f]{32}$",
        ),
    ],
    action: Annotated[
        str,
        Field(
            description="What you tried, e.g. 'refresh_schema' ([a-z0-9_.-]).",
            max_length=64,
        ),
    ],
    successful: Annotated[
        bool, Field(description="True when the action resolved the failure.")
    ],
    reporter_id: Annotated[
        str | None,
        Field(description="Optional stable agent id; hashed, never stored raw.", max_length=200),
    ] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Store a recovery outcome."""
    limited = _rate_limited(ctx)
    if limited:
        return limited

    payload = OutcomeRequest(
        fingerprint=fingerprint, action=action, successful=successful
    )
    async with SessionLocal() as session:
        await record_recovery_outcome(
            session,
            payload,
            reporter_hash=_reporter_from(ctx, reporter_id),
            source=_source_from(ctx),
        )
    return {"accepted": True}


# ---------------------------------------------------------------------------
# ASGI wiring
# ---------------------------------------------------------------------------


class _MCPEndpoint:
    """ASGI shim pointing at the session manager of the current lifespan.

    The SDK's session manager may only be ``run()`` once per instance, while an
    app can be started more than once in a single process (tests, reload). The
    route is registered once against this shim; each startup builds a fresh
    manager and points the shim at it.
    """

    def __init__(self) -> None:
        self.app = None

    async def __call__(self, scope, receive, send) -> None:
        if self.app is None:  # pragma: no cover - only if MCP is disabled
            from starlette.responses import JSONResponse

            response = JSONResponse(
                {"error": "mcp_unavailable", "detail": "MCP endpoint is not running"},
                status_code=503,
            )
            await response(scope, receive, send)
            return
        if scope.get("type") == "http" and scope.get("method") == "GET":
            # Streamable HTTP lets a client GET the endpoint to open a stream
            # for server-initiated messages. This server is stateless and
            # never sends one, so the SDK would hold that connection open
            # forever carrying nothing -- a free connection-hold for anyone
            # who asks. The spec's answer for a server that does not offer
            # the stream is 405, and every client that probes with GET is
            # written to accept it and carry on with POST.
            from starlette.responses import JSONResponse

            response = JSONResponse(
                {"error": "method_not_allowed",
                 "detail": "This MCP server is stateless and offers no server-initiated stream. "
                           "POST JSON-RPC to this path."},
                status_code=405,
                headers={"Allow": "POST, DELETE"},
            )
            await response(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except ClientDisconnect:
            # A client that hangs up mid-request is not an error on this
            # side: there is nobody left to answer, and the SDK's read of
            # the body raises out through every middleware, printing a full
            # traceback in production. It happens roughly hourly (probes,
            # scanners, a restarted client) and a log full of tracebacks
            # that mean nothing is how a traceback that means something
            # gets missed.
            #
            # A response still has to be produced. Swallowing the exception
            # and returning is what the first version did, and Starlette's
            # middleware then raised `RuntimeError("No response returned.")`
            # in its place -- one traceback traded for another, visible in
            # production two seconds after that deploy. So: 499, nginx's
            # code for a client that closed the request, written to a socket
            # that is probably already gone.
            try:
                await send({"type": "http.response.start", "status": 499,
                            "headers": [(b"content-length", b"0")]})
                await send({"type": "http.response.body", "body": b""})
            except Exception:  # noqa: BLE001 - the socket really is gone
                pass
            return


#: Registered once on the FastAPI app; wired up by :func:`mcp_lifespan`.
mcp_endpoint = _MCPEndpoint()


def _build_asgi(path: str):
    """Build the SDK's Streamable HTTP ASGI endpoint for ``path``.

    Stateless + JSON responses: each request is self-contained, so there is no
    session state to grow and no SSE stream to hold open -- the cheapest mode
    to run on a small VPS. DNS-rebinding protection is opt-in via
    ``FIN_MCP_ALLOWED_HOSTS`` / ``FIN_MCP_ALLOWED_ORIGINS``: this service is
    public, unauthenticated and holds no per-user data, so the check protects
    nothing by default and would reject every reverse-proxied hostname.
    """
    hosts = settings.mcp_host_list()
    origins = settings.mcp_origin_list()
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(hosts or origins),
        allowed_hosts=hosts,
        allowed_origins=origins,
    )
    sdk_app = mcp_server.streamable_http_app(
        streamable_http_path=path,
        stateless_http=True,
        json_response=True,
        transport_security=security,
        host="",  # empty string disables the SDK's localhost auto-protection
    )
    for route in sdk_app.routes:
        if isinstance(route, Route) and route.path == path:
            return route.app
    raise RuntimeError("MCP SDK did not expose a streamable HTTP route")


def build_mcp_route(path: str) -> Route:
    """One exact route for the MCP endpoint.

    A ``Mount`` would answer ``/mcp/`` and redirect ``/mcp`` with a 307 that
    every MCP client would then have to follow. An exact route avoids it.
    """
    return Route(
        path,
        endpoint=mcp_endpoint,
        methods=["GET", "POST", "DELETE", "OPTIONS"],
        name="mcp",
    )


@asynccontextmanager
async def mcp_lifespan(path: str):
    """Run the MCP session manager for the lifetime of the FastAPI app."""
    mcp_endpoint.app = _build_asgi(path)
    try:
        async with mcp_server.session_manager.run():
            yield
    finally:
        mcp_endpoint.app = None


# ---------------------------------------------------------------------------
# Trim what every client injects into the model on every turn
# ---------------------------------------------------------------------------

def _strip_schema_titles() -> None:
    """Pydantic labels every parameter with a `title` ("Schema Hash") that
    repeats its name; the tool list is pasted into a model's context on every
    turn by most MCP clients, so those labels are paid for constantly and
    inform nothing. Removed from the input schemas after registration."""
    for tool in mcp_server._tool_manager._tools.values():  # noqa: SLF001 - no public hook for this
        schema = tool.parameters
        if isinstance(schema, dict):
            schema.pop("title", None)
            for name, prop in list((schema.get("properties") or {}).items()):
                if isinstance(prop, dict):
                    prop.pop("title", None)
                    schema["properties"][name] = _compact_optional(prop)


def _compact_optional(prop: dict) -> dict:
    """``X | None = None`` comes out of Pydantic as ``anyOf: [X, {type:
    null}]`` plus ``default: null``: two extra objects per optional field,
    and most of our fields are optional. A field left out of ``required`` is
    already optional, so the plain ``X`` says the same thing in a third of
    the tokens. The function signature still accepts an explicit null, so a
    client that sends one keeps working."""
    variants = prop.get("anyOf")
    if not isinstance(variants, list) or len(variants) != 2:
        return prop
    rest = [v for v in variants if v != {"type": "null"}]
    if len(rest) != 1 or not isinstance(rest[0], dict):
        return prop
    out = {k: v for k, v in prop.items() if k != "anyOf" and not (k == "default" and v is None)}
    return {**rest[0], **out}


_strip_schema_titles()
