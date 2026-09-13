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
from starlette.routing import Route

from app.core.config import OPERATOR_HEADER, REPORTER_KIND_HEADER, settings
from app.core.privacy import hash_reporter_id
from app.core.ratelimit import check_write_limit, client_key
from app.core.service import (
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
FailEcho provides live cross-agent failure intelligence.

Use FailEcho when a tool, API, or MCP operation fails to check whether other
autonomous systems recently experienced the same failure and which recovery
actions actually worked. In short: use FailEcho when another tool fails,
before retrying blindly.

Call `check_tool_failure` BEFORE retrying. It tells you whether other
autonomous systems are hitting the same failure right now, whether it looks
like a new incident, and which recovery actions worked for them.

Then close the loop so the network stays useful for everyone:
`report_tool_failure` when a call fails, `report_tool_success` when it works
(failure rates need a denominator), and `report_recovery_outcome` after you try
a recovery action.

Name things the way other agents will: `service` is the MCP server's own name
(its serverInfo.name) or the HTTP API's host, and `operation` is the tool name
exactly as the server defines it. Evidence is only shared when names match.

Privacy: send failure *metadata* only -- never prompts, tool arguments, tool
results, request or response bodies, API keys, credentials or user content.
Error messages are normalized server-side and the raw text is discarded.
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
        headers.get(REPORTER_KIND_HEADER.lower()), headers.get(OPERATOR_HEADER.lower())
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
        "Use FailEcho when another tool fails, before retrying blindly.\n\n"
        "Use this tool when another tool, API call, or MCP server operation "
        "fails. It checks whether other autonomous systems recently "
        "experienced the same failure and returns current failure "
        "intelligence, known recovery actions, and confidence based on "
        "observed outcomes.\n\n"
        "Call it BEFORE retrying. A retry that is failing for every other "
        "agent right now is a retry you can skip, and the network often knows "
        "a specific action that works instead (refresh a stale tool schema, "
        "fall back to another provider, reconnect, wait).\n\n"
        "Returns: `status` (HEALTHY / DEGRADED / MAJOR / INSUFFICIENT_DATA), "
        "how many observations and distinct reporters have seen this exact "
        "failure in the last 5 minutes and hour, the current failure rate for "
        "the service+operation, every recovery action other agents tried with "
        "its success rate, and a single `recommendation` when the evidence "
        "supports one.\n\n"
        "`recommendation` is null when evidence is insufficient -- that is a "
        "real answer, not an error. Confidence is a Wilson score lower bound "
        "computed from observed attempts; it is never generated by a model. "
        "Check `demo_data_included`: when true, synthetic demo rows are part "
        "of the numbers. `evidence_sources` says who saw it: 'agent' means "
        "independent agents; 'first_party' alone means only FailEcho's own "
        "agents, so weigh it as one reporter's experience.\n\n"
        "This works before any other agent has joined: five attempts back a "
        "recommendation and all five can be your own. "
        "`recommendation.from_other_agents` tells you which -- false means "
        "your own history is answering you, true means somebody else's did. "
        "Pass `reporter_id` if you want that distinction; it is salted and "
        "hashed on arrival.\n\n"
        "Reading is free, anonymous, unauthenticated and never rate limited, "
        "and this call stores nothing."
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
            description=(
                "Short failure class, e.g. 'validation_error', 'timeout', "
                "'rate_limit', 'auth_error'."
            ),
            max_length=64,
        ),
    ] = None,
    error_message: Annotated[
        str | None,
        Field(
            description=(
                "The error text you received. Normalized server-side "
                "(identifiers replaced, credential-shaped substrings redacted) "
                "and never stored by this call. Do not include prompts, tool "
                "arguments, secrets or user content."
            ),
            max_length=2000,
        ),
    ] = None,
    error_code: Annotated[
        str | None,
        Field(description="Protocol/vendor code, e.g. '422', 'ECONNRESET'.", max_length=32),
    ] = None,
    version: Annotated[
        str | None,
        Field(description="Version of the failing service/tool, if known.", max_length=64),
    ] = None,
    schema_hash: Annotated[
        str | None,
        Field(
            description=(
                "Short hash of the tool schema you used. Lets FailEcho "
                "separate 'the API broke' from 'your tool schema is stale'."
            ),
            max_length=64,
        ),
    ] = None,
    reporter_id: Annotated[
        str | None,
        Field(
            description=(
                "Optional stable identifier for your agent. Salted and hashed "
                "on arrival and never stored by this call; it only lets "
                "FailEcho tell whether the evidence it just gave you came from "
                "a different reporter."
            ),
            max_length=200,
        ),
    ] = None,
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
    return result.model_dump()


# ---------------------------------------------------------------------------
# Tool 2 -- contribute a failure
# ---------------------------------------------------------------------------


@mcp_server.tool(
    name="report_tool_failure",
    title="Report a failed tool call to the network",
    description=(
        "Anonymously contribute a tool/API/MCP failure to FailEcho so other "
        "autonomous systems can recognise it. Call this whenever a tool call "
        "fails, after (or alongside) `check_tool_failure`.\n\n"
        "PRIVACY -- this is a shared public network. Send failure metadata "
        "ONLY. Never include prompts, model messages, tool arguments, tool "
        "results, request or response bodies, HTTP headers, cookies, API keys, "
        "tokens, customer names, emails or any user content. The error message "
        "is normalized server-side (numbers, UUIDs, emails, URLs, IPs and "
        "tokens replaced with placeholders; credential-shaped substrings "
        "redacted) and the raw text is discarded, but that is a safety net, "
        "not a licence to send sensitive data.\n\n"
        "Pass a stable `reporter_id` if you can: it is salted and hashed "
        "before storage, is never stored raw, and lets the network count you "
        "as one independent reporter instead of anonymous noise. Writes are "
        "rate limited per client."
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
        "Report that a tool call SUCCEEDED. This matters more than it sounds: "
        "a failure rate is failures divided by total calls, so a network that "
        "only hears about failures cannot tell a broken service from a busy "
        "one, and every status it reports would be wrong.\n\n"
        "Cheap to call and carries no error data at all -- just which "
        "service/operation/version succeeded and how long it took. Same "
        "privacy rules apply: metadata only, never arguments or results."
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
        "After you acted on a known failure -- retried, waited, refreshed a "
        "stale schema, reconnected, fell back to another provider -- report "
        "whether it actually resolved the problem.\n\n"
        "This is the highest-value telemetry in the network: it is the "
        "difference between 'everyone is failing' and 'everyone is failing, "
        "and refreshing the schema fixes it'. Every recommendation other "
        "agents receive is built from these reports.\n\n"
        "Pass the `fingerprint` returned by `check_tool_failure` or "
        "`report_tool_failure`. Common actions: retry, wait, refresh_schema, "
        "remove_optional_field, reconnect, use_fallback, reauthenticate, "
        "abort. Report one outcome per attempt, not one per retry-loop "
        "iteration: a single reporter contributes at most 5 attempts per hour "
        "to any action's confidence."
    ),
)
async def report_recovery_outcome(
    fingerprint: Annotated[
        str,
        Field(
            description=(
                "Fingerprint from check_tool_failure or report_tool_failure "
                "(32 lowercase hex characters)."
            ),
            pattern=r"^[0-9a-f]{32}$",
        ),
    ],
    action: Annotated[
        str,
        Field(
            description=(
                "What you tried, e.g. 'refresh_schema'. Lowercase, "
                "[a-z0-9_.-], spaces become underscores."
            ),
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
        await self.app(scope, receive, send)


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
