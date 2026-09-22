"""A small real-API MCP server, stdio, standard library only.

The OpenCode proxy personas' tool: package and repository lookups against
the real PyPI, npm, crates.io and GitHub APIs, the kind of API-wrapping MCP
server agents are given every day. Its failures are the services' own --
GitHub's unauthenticated rate limit, a crates.io refusal, a timeout -- and
it answers them the way such servers do: a result flagged ``isError`` with
the status and the start of the body.

It runs inside the sandbox VM, reached through the fence like everything
else there. It knows nothing about FailEcho; whether FailEcho sits in front
of it is the whole difference between the two twins.
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = "failecho-fleet-packages-mcp/1.0 (+https://failecho.com)"

TOOLS = [
    ("pypi_latest", "Latest version of a PyPI package.", {"package": "PyPI package name"}),
    ("npm_latest", "Latest version of an npm package.", {"package": "npm package name"}),
    ("crates_latest", "Latest version of a Rust crate on crates.io.", {"crate": "crate name"}),
    ("github_latest_release", "Latest release tag and date of a GitHub repository.", {"repo": "owner/name"}),
    ("github_stars", "Star count of a GitHub repository.", {"repo": "owner/name"}),
    # Two endpoints that fail the way real ones do, so the proxy pair meets
    # failures at all: without them its tools almost always succeeded and the
    # group measured provider luck (20 Sep).
    ("service_status", "Status of a dependency's status endpoint. Flaky: it "
                       "returns 503 about half the time.", {"name": "dependency name"}),
    ("quota_check", "Remaining quota for a dependency. Rate limited.", {"name": "dependency name"}),
]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def status_only(url):
    """A status check whose body is empty: read the code, not JSON."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


def call(name, args):
    q = urllib.parse.quote
    if name == "pypi_latest":
        return {"version": get(f"https://pypi.org/pypi/{q(args['package'])}/json")["info"]["version"]}
    if name == "npm_latest":
        return {"version": get(f"https://registry.npmjs.org/{q(args['package'])}/latest")["version"]}
    if name == "crates_latest":
        return {"version": get(f"https://crates.io/api/v1/crates/{q(args['crate'])}")["crate"]["max_version"]}
    if name == "github_latest_release":
        d = get(f"https://api.github.com/repos/{args['repo']}/releases/latest")
        return {"tag": d["tag_name"], "published_at": d["published_at"]}
    if name == "github_stars":
        return {"stars": get(f"https://api.github.com/repos/{args['repo']}")["stargazers_count"]}
    if name == "service_status":
        status_only("https://httpbingo.org/status/200,200,503,503")
        return {"name": args.get("name", ""), "status": "ok"}
    if name == "quota_check":
        status_only("https://httpbingo.org/status/429")
        return {"name": args.get("name", ""), "remaining": "unknown"}
    raise KeyError(name)


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def error_result(mid, text):
    send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": True}})


def main():
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        if "method" not in msg:
            continue
        mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "packages", "version": "1.0"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": n, "description": d,
                 "inputSchema": {"type": "object", "required": list(props),
                                 "properties": {k: {"type": "string", "description": v} for k, v in props.items()}}}
                for n, d, props in TOOLS]}})
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            try:
                out = call(name, args)
            except KeyError as e:
                error_result(mid, f"unknown tool or missing argument: {e}")
                continue
            except urllib.error.HTTPError as e:
                body = e.read()[:200].decode(errors="replace")
                error_result(mid, f"HTTP {e.code} {e.reason}: {body}")
                continue
            except Exception as e:  # noqa: BLE001 - a timeout, a refused connection
                error_result(mid, f"{type(e).__name__}: {e}")
                continue
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": json.dumps(out)}]}})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found"}})


if __name__ == "__main__":
    main()
