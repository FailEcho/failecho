"""FailEcho -- application entrypoint.

Live failure intelligence for autonomous software. Agents share anonymous tool
failures and recovery outcomes; other agents query that shared evidence before
retrying.

One process, one SQLite file, no broker, no cache, no queue. It is meant to run
comfortably in well under 500 MB of RAM on a small VPS.

Branding note: "FailEcho" is the product identity. Wire formats are not
branded -- endpoint paths, MCP tool names and field names stay exactly as they
were, because machine clarity outranks naming purity.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import hashlib
import time
from contextlib import AsyncExitStack
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api import observe, outcome, query, services
from app.core.config import settings
from app.db.database import SessionLocal, init_db
from app.schemas.services import HealthResponse

API_DESCRIPTION = """
FailEcho provides live failure intelligence for AI agents and autonomous
software.

**Before you retry, check the echo.**

FailEcho is a shared, cross-agent failure intelligence network. AI agents
report privacy-safe **tool failures**, successful calls and **recovery
outcomes**; other agents query that shared evidence to learn whether a failure
is widespread right now and which recovery actions have actually worked.

Reachable over the **Model Context Protocol (MCP)** at `/mcp` and over this
REST API. No account, no API key, free during the public MVP.

### The three calls

| Endpoint | When |
|---|---|
| `POST /v1/observe` | after every tool call -- successes *and* failures |
| `POST /v1/query`   | when a call fails, **before** you retry |
| `POST /v1/outcome` | after you tried a recovery action |

### MCP

The same four operations are available over MCP (Streamable HTTP) at `/mcp`:
`check_tool_failure`, `report_tool_failure`, `report_tool_success`,
`report_recovery_outcome`. See `/llms.txt` for a machine-readable summary.

### Privacy

Only structured failure metadata is accepted. No prompts, no tool arguments,
no tool results, no request/response bodies, no headers, no keys, no customer
data. Error messages are normalized (identifiers replaced, credential-shaped
substrings redacted) and the raw string is discarded. `X-Reporter-ID` is
optional and is salted+hashed before storage.

### Limits

Reads are never rate limited. Writes (`/v1/observe`, `/v1/outcome` and the MCP
report tools) are limited per client IP. A single reporter contributes at most
5 attempts per hour to any recovery action's confidence, so no one client can
buy a recommendation.

### Honesty

Every number returned is deterministic arithmetic over observation counts.
Confidence is a Wilson score lower bound you can recompute yourself. When
evidence is thin the answer is `INSUFFICIENT_DATA` and `null`, never a guess.

No account, no key, no payment. MVP.
"""

STATIC_DIR = Path(__file__).parent / "web" / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    async with AsyncExitStack() as stack:
        if settings.mcp_enabled:
            # A route's sub-app lifespan is not run by the parent, so the MCP
            # session manager is started (and rebuilt per startup) here.
            from app.mcp_server import mcp_lifespan

            await stack.enter_async_context(mcp_lifespan(settings.mcp_path))
        yield


app = FastAPI(
    title="FailEcho API",
    docs_url=None,
    version="0.1.0",
    summary="Live failure intelligence for autonomous software.",
    description=API_DESCRIPTION,
    lifespan=lifespan,
    contact={
        "name": "FailEcho",
        "url": settings.base_url(),
        "email": "support@failecho.com",
    },
    license_info={"name": "MIT"},
)

# Public, free, unauthenticated API: any agent or browser may call it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origin_list(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(observe.router, prefix="/v1")
app.include_router(query.router, prefix="/v1")
app.include_router(outcome.router, prefix="/v1")
app.include_router(services.router, prefix="/v1")


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["status"],
    summary="Liveness probe",
    description=(
        "Returns `{\"status\": \"ok\"}` when the process is up and the "
        "database answers. No authentication, no rate limit."
    ),
)
async def health() -> HealthResponse:
    async with SessionLocal() as session:
        await session.execute(text("SELECT 1"))
    return HealthResponse(status="ok")


if settings.mcp_enabled:
    # Streamable HTTP MCP endpoint, in-process, on one exact path (no
    # trailing-slash redirect for clients to follow).
    from app.mcp_server import build_mcp_route

    app.router.routes.append(build_mcp_route(settings.mcp_path))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


LLMS_TXT_TEMPLATE = """# FailEcho

FailEcho is a live cross-agent failure intelligence network
for AI agents and autonomous software.

Use FailEcho when a tool, API, or MCP operation fails and you want to know
whether other autonomous systems recently experienced the same failure and
which recovery actions worked.

Before retrying blindly, query FailEcho for observed failure rates and
recovery outcomes. A retry that is failing for every other agent right now is
a retry you can skip.

FailEcho is not an error database, not an uptime monitor and not a debugger.
It is a shared, real-time, anonymous network keyed on:
service + operation + version + schema_hash + failure fingerprint.

## Canonical site

{base_url}/

## Endpoints

MCP (Model Context Protocol, Streamable HTTP):
{base_url}/mcp

Config-file clients (Cursor, Claude Desktop, most frameworks) want this
shape, so you can add it yourself without reading the setup page:

{{
  "mcpServers": {{
    "failecho": {{
      "type": "http",
      "url": "{base_url}/mcp"
    }}
  }}
}}

OpenAPI:
{base_url}/openapi.json

Docs:
{base_url}/docs

Setup, every client step by step:
{base_url}/setup

## Claude Code

Two commands install the MCP server and a hook that reports tool failures
automatically, so no model has to remember to call FailEcho:

/plugin marketplace add FailEcho/failecho
/plugin install failecho@failecho

## MCP tools (preferred for agents)

Stateless, no auth.

- check_tool_failure       ask what is happening with a failure, before retrying
- report_tool_failure      contribute a failure observation
- report_tool_success      contribute a success (failure rates need a denominator)
- report_recovery_outcome  report whether a recovery action worked

## REST API

- POST /v1/observe   report one tool-call outcome (success or failure)
- POST /v1/query     ask what FailEcho knows about a failure (stores no telemetry)
- POST /v1/outcome   report whether a recovery action worked
- GET  /v1/services  current status per service/operation
- GET  /v1/stats     network-wide counters, real and demo kept separate
- GET  /v1/recovery-intelligence  best evidenced recovery actions right now
- GET  /health       liveness

## Service naming

`service` is part of the fingerprint, so FailEcho canonicalises it before
matching: packaging affixes are stripped (`mcp-server-fetch`, `fetch-mcp` and
`fetch` are one service) and a short explicit list handles genuine aliases.
Hostnames are left alone: `api.github.com` never becomes `github`. Send the
server's own name (`serverInfo.name`) or the API host and the rest is handled.

## Vocabulary

Failure Echo    a normalized observed failure, shared by fingerprint
Recovery Echo   evidence that a recovery action worked
Incident        a sudden abnormal failure increase
Reporter        an AI agent or runtime sending telemetry
Fingerprint     the canonical normalized error identity

## Status values

HEALTHY (failure rate < 5%), DEGRADED (< 30%), MAJOR (>= 30%),
INSUFFICIENT_DATA (fewer than 10 observations in the last hour).

## Working alone

FailEcho is useful before anyone else has joined. A recovery action is
recommended once five attempts back it, and those five can all be yours: hit
the same failure five times, report what fixed it each time, and the answer
comes back on the sixth. Every recommendation carries `from_other_agents`:
false when the evidence is your own history, true when another reporter paid
for it, null if you sent no reporter id. Send one (`X-Reporter-ID`, or
`reporter_id` over MCP) if you want that distinction.

## Confidence

Confidence is a Wilson score lower bound over observed recovery attempts,
capped below 1.0, discounted when fewer than 3 distinct reporters back it. It
is arithmetic, not a model output, and you can recompute it from the attempts
and successes returned alongside it. When evidence is insufficient the
recommendation is null. FailEcho never fabricates confidence.

## Privacy

Send failure metadata only: service, operation, version, schema_hash, outcome,
error_type, error_code, a short error message, latency.

Do not send prompts, model messages, tool arguments, tool results, request or
response bodies, HTTP headers, cookies, API keys, tokens, customer names,
emails, or any user content. Unknown fields are dropped before storage. Error
messages are normalized (identifiers replaced, credential-shaped substrings
redacted) and the raw text is discarded.

Reporter identity is optional. Supply X-Reporter-ID (REST) or reporter_id
(MCP) and it is salted and hashed on arrival; the raw value is never stored.
Anonymous reporting is fully supported.

## Cost

No account required.
No API key required.
Free during the public MVP.
"""


ROBOTS_TXT = """User-agent: *
Allow: /

Sitemap: {base_url}/sitemap.xml

# The machine-readable surfaces an agent actually wants:
#   {base_url}/llms.txt
#   {base_url}/openapi.json
#   {base_url}/mcp
"""

#: Pages worth indexing. Deliberately short: the API endpoints are for
#: machines, not for search results, and listing them would only dilute the
#: three surfaces that actually explain what FailEcho is.
SITEMAP_PAGES = ("/", "/network", "/demo", "/about", "/setup", "/docs", "/llms.txt")


#: Injected into the Swagger page. Swagger UI ships no description, no
#: canonical and no brand text, which makes /docs a thin page for a crawler
#: that arrives there first.
DOCS_HEAD = """
<link rel="canonical" href="{base_url}/docs">
<meta name="description" content="FailEcho API reference. FailEcho is a live cross-agent failure intelligence network for AI agents and autonomous software: report tool failures, successes and recovery outcomes, and query what other agents observed.">
<meta property="og:type" content="website">
<meta property="og:site_name" content="FailEcho">
<meta property="og:title" content="FailEcho API — Failure Intelligence for AI Agents">
<meta property="og:description" content="REST and MCP API for FailEcho, a live cross-agent failure intelligence network for AI agents and autonomous software.">
<meta property="og:url" content="{base_url}/docs">
<link rel="icon" href="/static/favicon.png" type="image/png">
"""


@app.get("/docs", include_in_schema=False)
async def swagger_docs(request: Request) -> HTMLResponse:
    """Swagger UI, with the brand and canonical metadata it does not ship."""
    from fastapi.openapi.docs import get_swagger_ui_html

    page = get_swagger_ui_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title="FailEcho API — Failure Intelligence for AI Agents",
    )
    html = page.body.decode()
    html = html.replace(
        "</head>", DOCS_HEAD.format(base_url=public_base_url(request)) + "</head>", 1
    )
    return HTMLResponse(html)


@app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
async def robots_txt(request: Request) -> PlainTextResponse:
    """Public site: indexing is welcome. No crawl traps, no hidden paths."""
    body = ROBOTS_TXT.format(base_url=public_base_url(request))
    return PlainTextResponse(body, media_type="text/plain; charset=utf-8")


def _last_modified() -> str:
    """Newest static-asset timestamp, as an ISO date. Honest, not invented."""
    newest = 0.0
    for path in STATIC_DIR.glob("*"):
        if path.is_file():
            newest = max(newest, path.stat().st_mtime)
    return datetime.fromtimestamp(newest or time.time(), tz=timezone.utc).strftime(
        "%Y-%m-%d"
    )


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap(request: Request) -> Response:
    """A three-URL sitemap: the homepage, the API docs, the agent guide.

    No changefreq, no priority -- both are hints search engines mostly ignore,
    and inventing values for them would be noise dressed as information.
    """
    base = public_base_url(request)
    lastmod = _last_modified()
    urls = "".join(
        f"\n  <url><loc>{base}{path if path != '/' else '/'}</loc>"
        f"<lastmod>{lastmod}</lastmod></url>"
        for path in SITEMAP_PAGES
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}\n</urlset>\n"
    )
    return Response(content=body, media_type="application/xml")


@app.get(
    "/llms.txt",
    response_class=PlainTextResponse,
    include_in_schema=False,
)
async def llms_txt(request: Request) -> PlainTextResponse:
    """Machine-readable description of FailEcho for autonomous discovery.

    URLs come from ``FIN_PUBLIC_URL`` when it is set, so a deployed instance
    advertises its real origin; locally it falls back to the request's own
    base URL, which keeps the examples runnable in development.
    """
    body = LLMS_TXT_TEMPLATE.format(base_url=public_base_url(request))
    return PlainTextResponse(body, media_type="text/plain; charset=utf-8")


DEFAULT_LOCAL_URL = "http://localhost:8000"


def public_base_url(request: Request | None = None) -> str:
    """Canonical origin: the configured public URL, else this request's own.

    One configuration source (``FIN_PUBLIC_URL``) so the production domain is
    never written into templates, docstrings or JavaScript.
    """
    configured = settings.base_url()
    if configured and configured != DEFAULT_LOCAL_URL:
        return configured
    if request is not None:
        return str(request.base_url).rstrip("/")
    return configured


def asset_version() -> str:
    """Short fingerprint of the static assets, used for cache busting.

    Caddy and Cloudflare both cache /static/* for an hour or more, so a
    redeployed logo can keep serving the old bytes long after the file
    changed. Appending ?v=<hash> makes every asset change a new URL.
    """
    stamp = 0.0
    for path in sorted(STATIC_DIR.glob("*")):
        if path.is_file():
            stamp = max(stamp, path.stat().st_mtime)
    return hashlib.sha256(str(stamp).encode()).hexdigest()[:8]


def render_page(filename: str, base_url: str) -> str:
    """Substitute brand tokens into a static page.

    Deliberately a string replace rather than a template engine: three tokens
    do not justify a dependency. The GitHub link is removed entirely when no
    repository URL is configured -- an invented URL would be worse than none.
    """
    html = (STATIC_DIR / filename).read_text(encoding="utf-8")
    html = html.replace("{{PUBLIC_URL}}", base_url)
    html = html.replace("{{ASSET_V}}", asset_version())
    if settings.github_url:
        html = html.replace("{{GITHUB_URL}}", settings.github_url)
        html = html.replace("<!--github-->", "").replace("<!--/github-->", "")
    else:
        while "<!--github-->" in html and "<!--/github-->" in html:
            start = html.index("<!--github-->")
            end = html.index("<!--/github-->") + len("<!--/github-->")
            html = html[:start] + html[end:]
    return html


@app.exception_handler(404)
async def not_found(request: Request, exc: Exception) -> Response:
    """A browser that mistypes a URL should not be handed a JSON error.

    API clients still get JSON: the split is on what the caller asked for, so
    /v1/* and every agent keeps the shape it expects.
    """
    if "text/html" in request.headers.get("accept", ""):
        return HTMLResponse(
            render_page("404.html", public_base_url(request)), status_code=404
        )
    return JSONResponse({"detail": "Not Found"}, status_code=404)


def render_homepage(base_url: str) -> str:
    """Kept as a named entry point; the homepage is just one rendered page."""
    return render_page("index.html", base_url)


@app.get("/", include_in_schema=False)
async def homepage(request: Request) -> HTMLResponse:
    return HTMLResponse(render_page("index.html", public_base_url(request)))


@app.get("/about", include_in_schema=False)
async def about(request: Request) -> HTMLResponse:
    """What FailEcho is, for humans and for search engines."""
    return HTMLResponse(render_page("about.html", public_base_url(request)))


@app.get("/network", include_in_schema=False)
async def network(request: Request) -> HTMLResponse:
    """The live dashboard, off the front page so it cannot dominate it."""
    return HTMLResponse(render_page("network.html", public_base_url(request)))


@app.get("/demo", include_in_schema=False)
async def demo(request: Request) -> HTMLResponse:
    """One failure going through the loop, with the real terminal output."""
    return HTMLResponse(render_page("demo.html", public_base_url(request)))


@app.get("/setup", include_in_schema=False)
async def setup(request: Request) -> HTMLResponse:
    """How to connect, for someone who has never used an MCP client."""
    return HTMLResponse(render_page("setup.html", public_base_url(request)))


@app.get("/.well-known/glama.json", include_in_schema=False)
async def glama_claim() -> FileResponse:
    """Ownership proof for the Glama connector listing.

    The claim token is meant to be fetched publicly from this origin, so it
    lives in a served file rather than in code or in the environment.
    """
    return FileResponse(
        STATIC_DIR / "well-known" / "glama.json", media_type="application/json"
    )


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    """Browsers still ask for /favicon.ico; hand them the PNG mark."""
    return FileResponse(STATIC_DIR / "favicon.png", media_type="image/png")
