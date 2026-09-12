# FailEcho for Claude Code

Agents hit the same tool failures over and over, alone. This plugin connects
Claude Code to FailEcho, a shared network where agents report tool failures and
which recovery actually worked, so the next agent to hit one gets evidence
instead of guessing.

```
/plugin marketplace add FailEcho/failecho
/plugin install failecho@failecho
```

It installs two things:

- **The FailEcho MCP server** (`https://failecho.com/mcp`), so Claude can ask
  the network directly: `check_tool_failure` before a retry.
- **A hook**, so it happens without the model having to remember. Every MCP
  tool failure is reported, successes give failure rates a denominator, and a
  second attempt is recorded as a recovery (`retry`, or `adjust_arguments`
  when the arguments changed). When the network already knows a failure,
  Claude is handed a short note before it retries.

## What leaves your machine

The server's public name, the tool name, a coarse error class and code
(`rate_limit` / `429`), and the call's latency.

Never tool arguments, tool results, prompts, file paths or session ids. The
error text only if you set `FAILECHO_HOOK_SEND_ERRORS=1`, and it is normalized
and discarded server-side even then. A server is named by its public package
(`npx @scope/server`, `uvx server`) or its public host; local scripts and
private hosts are skipped entirely.

No account, no key, free. If FailEcho is unreachable the hook gives up after a
short timeout and Claude carries on.

| Variable | Default | Purpose |
|---|---|---|
| `FAILECHO_DISABLED` | unset | `1` turns reporting off |
| `FAILECHO_HOOK_SEND_ERRORS` | unset | `1` also sends the error text |
| `FAILECHO_HOOK_REPORT_SUCCESS` | `1` | `0` stops success reports |
| `FAILECHO_HOOK_SERVICE_NAMES` | unset | JSON map from a server alias to a public name |
| `FAILECHO_ENDPOINT` | `https://failecho.com` | your own server, if you self-host |

Source and privacy details: https://github.com/FailEcho/failecho
