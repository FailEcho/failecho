# failecho-mcp

Stdio MCP server that relays to [FailEcho](https://failecho.com): before your
agent retries a failed tool, check what other agents already tried and whether
it worked.

```json
{
  "mcpServers": {
    "failecho": {
      "command": "npx",
      "args": ["-y", "failecho-mcp"]
    }
  }
}
```

For hosts that can only start a local process. If your client speaks
Streamable HTTP, point it at `https://failecho.com/mcp` directly instead —
one less moving part.

## What it is

A relay, not a second FailEcho. It has no database and stores nothing: every
message is forwarded to the shared network and the reply handed back, so you
get the same four tools, with the same descriptions and the same evidence, as
using the URL directly.

- `check_tool_failure` — before a retry
- `report_tool_failure`, `report_tool_success` — failure rates need a denominator
- `report_recovery_outcome` — what actually fixed it

No account, no API key. Zero dependencies, Node 18+.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `FAILECHO_URL` | `https://failecho.com/mcp` | Network to relay to. Point it at your own server if you self-host. |
| `FAILECHO_REPORTER_ID` | unset | Optional. Salted and hashed on arrival; lets FailEcho tell your evidence from someone else's. |
| `NODE_USE_ENV_PROXY` | unset | Behind an HTTP proxy, set to `1` (Node 24+). Node's `fetch` ignores `HTTPS_PROXY` unless told to, and the relay answers `FailEcho unreachable: fetch failed`. Found by running the relay in a machine whose only way out is a proxy. |

## Proxy: FailEcho in front of your other MCP servers

A model given FailEcho's tools has to think of asking, and while it is
handling a failure it mostly does not. The proxy puts the answer where the
model is already looking. Wrap the command a client would start:

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "failecho-mcp", "proxy", "--", "npx", "-y", "@modelcontextprotocol/server-github"]
    }
  }
}
```

or a remote server (a header token works; OAuth does not -- connect those
directly):

```bash
npx -y failecho-mcp proxy --header "Authorization: Bearer $TOKEN" -- https://mcp.example.com/mcp
```

Every message passes through unchanged, as the same bytes, except the
response to a tool call that failed. That one gets one line added:

```
FailEcho: try backoff, worked 128/251 (confidence 0.61).
```

-- or `no clear fix yet; other agents tried ...`, or `skip -- nothing other
agents tried recently has fixed this failure`, or nothing at all when the
network has no evidence. Nothing is acted on for you.

Each tool call's outcome is reported as its shape only: the server's name,
the tool name, an error class and code, the latency. Never arguments,
results or the error text. A call that failed transiently and is repeated
with the same arguments within two minutes is reported as a retry, and
whether it worked; the arguments are compared as a hash in memory and never
leave. Advice waits at most 3 seconds, holds only the failed response, and
if FailEcho is unreachable the error passes through unchanged.

| Variable | Default | Purpose |
|---|---|---|
| `FAILECHO_DISABLED` | unset | `1`: a plain pipe, nothing reported or added |
| `FAILECHO_ADVISE` | `1` | `0`: report, but do not add advice |
| `FAILECHO_ENDPOINT` | `https://failecho.com` | Network to report to and read from |
| `FAILECHO_REPORTER_ID` | random per run | Stable id, so your machine counts as one reporter |

Behind an HTTP proxy the same `NODE_USE_ENV_PROXY=1` applies to the proxy's own requests.

## Privacy

Metadata only: the service, the operation, an error class and code, how long
the call took. Never prompts, tool arguments, tool results, headers or keys.
Error text is normalised server-side and the raw string discarded.

MIT. Source: https://github.com/FailEcho/failecho
