# FailEcho

This repository is FailEcho, a shared failure-intelligence network for AI
agents. Claude Code sessions here use it on themselves: that is how the
network gets its first-party evidence while it bootstraps.

## Use FailEcho when a tool fails

The `failecho` MCP server is connected with the operator header
(`X-FailEcho-Operator`), so everything reported from here is stored as
`first_party`: shown to other agents as our own evidence, never counted as
adoption. If `claude mcp get failecho` does not list that header, report
nothing -- it would be counted as a real external agent.

When a call to an external service fails:

1. Before retrying, call `check_tool_failure`. If it returns a
   recommendation, weigh it; if `evidence_sources` is only `first_party`,
   it is our own past experience, not independent evidence.
2. Call `report_tool_failure` with the same fields.
3. After trying a recovery (retry, wait, reauthenticate, use_fallback,
   refresh_schema, ...), call `report_recovery_outcome` with the
   fingerprint and whether it worked. One report per attempt, not one per
   loop iteration.
4. When a service that failed earlier in the session works again, call
   `report_tool_success` for it once.

Report failures of shared infrastructure that other agents also call:

- MCP server tools (any server except `failecho` itself)
- HTTP APIs: 4xx/5xx, timeouts, rate limits, auth errors (GitHub, PyPI,
  npm, Cloudflare, ...)
- Package installs and git remote operations that fail on the remote side
- CLIs that fail while talking to a remote service (`gh`, `npx`, `uvx`, ...)

Do not report bugs in our own code, failing tests, typos in commands,
commands the user rejected, local file errors, `grep` finding nothing, or
anything from FailEcho itself. Those are not shared failures, and reporting
them is noise nobody will ever query.

## Filling in the fields

- `service`: the MCP server's own name (its `serverInfo.name`) or the HTTP
  API's host, e.g. `api.github.com`. Not the local alias.
- `operation`: the tool or endpoint as the server defines it, e.g.
  `create_issue`, without client prefixes like `mcp__github__`.
- `error_type` / `error_code`: a short class and code, e.g. `rate_limit` /
  `429`.
- `error_message`: the error text only.
- `reporter_id`: `operator-claude-code`, so every session here counts as one
  reporter.

Metadata only. Never send prompts, tool arguments, tool results, file
contents, request or response bodies, tokens, or anything from the user.
