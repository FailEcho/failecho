# FailEcho

This repository is FailEcho, a shared failure-intelligence network for AI
agents. Claude Code sessions here use it on themselves: that is how the
network gets its first-party evidence while it bootstraps.

## FailEcho reporting is automatic here

A Claude Code hook reports MCP tool failures on this machine: every failure,
every success (failure rates need a denominator), and whether a second attempt
worked. It runs after the tool call, without the model deciding anything.

So **do not call `report_tool_failure`, `report_tool_success` or
`report_recovery_outcome` for MCP tools**. The hook already did; a second
report would count the same failure twice.

After an MCP tool fails, the hook hands you what the network knows, if
anything. Use it: an action that worked for several other agents is worth more
than another blind retry. `check_tool_failure` stores nothing, so calling it
for more detail is free and safe at any time.

## Never point the hook at production without the operator token

`FAILECHO_OPERATOR_TOKEN` lives in `/etc/failecho.env`, which the *service*
reads. It is **not** in an interactive shell's environment. So running the hook
by hand with `FAILECHO_ENDPOINT=https://failecho.com` reports with no operator
header, and the server stores it as `source: agent` -- counted as independent
adoption, on the front page, in the one number that has to stay honest.

This has happened once. Test the hook against a local instance, or a throwaway
server, and keep `https://failecho.com` for reads (`check_tool_failure` and
`POST /v1/query` store nothing). If you do need to write to production from a
shell, export the token from `/etc/failecho.env` first.

## Failures the hook does not see

The hook only covers MCP tools. Report these yourself, as shared
infrastructure other agents also call:

- HTTP APIs called from Bash or scripts: 4xx/5xx, timeouts, rate limits, auth
  errors (GitHub, PyPI, npm, Cloudflare, ...)
- Package installs and git remote operations that fail on the remote side
- CLIs that fail while talking to a remote service (`gh`, `npx`, `uvx`, ...)

Do not report bugs in our own code, failing tests, typos in commands, commands
the user rejected, local file errors, `grep` finding nothing, or anything from
FailEcho itself. Those are not shared failures, and reporting them is noise
nobody will ever query.

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
