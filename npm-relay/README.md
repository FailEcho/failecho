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

## Privacy

Metadata only: the service, the operation, an error class and code, how long
the call took. Never prompts, tool arguments, tool results, headers or keys.
Error text is normalised server-side and the raw string discarded.

MIT. Source: https://github.com/FailEcho/failecho
