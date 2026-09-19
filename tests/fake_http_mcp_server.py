"""A Streamable HTTP MCP server for the proxy tests, built on the MCP SDK.

    python tests/fake_http_mcp_server.py PORT json|sse [TOKEN]

`json` answers each request with a JSON body, statelessly; `sse` answers
with an event stream and issues a session id, the way a stateful server
does. With TOKEN, requests without `Authorization: Bearer TOKEN` get 401.
"""

import sys

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import PlainTextResponse

port, mode = int(sys.argv[1]), sys.argv[2]
token = sys.argv[3] if len(sys.argv) > 3 else None

server = MCPServer(name="fake-http-server")


@server.tool()
def ok(q: str = "") -> str:
    return "ok " + q


@server.tool()
def fails() -> str:
    raise RuntimeError("503 service unavailable")


app = server.streamable_http_app(
    streamable_http_path="/mcp", json_response=(mode == "json"), stateless_http=(mode == "json"),
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False), host="")

if token:
    inner = app

    async def app(scope, receive, send):  # noqa: F811 - wraps the SDK app
        if scope["type"] == "http":
            auth = dict(scope["headers"]).get(b"authorization", b"").decode()
            if auth != f"Bearer {token}":
                await PlainTextResponse("unauthorized", status_code=401)(scope, receive, send)
                return
        await inner(scope, receive, send)

uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
