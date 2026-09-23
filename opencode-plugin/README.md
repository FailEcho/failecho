# failecho-opencode

When a tool fails in OpenCode, this puts the network's evidence in the failing
tool's output — before the model decides what to do next. The model is never
asked to call anything.

```bash
mkdir -p .opencode/plugin
curl -o .opencode/plugin/failecho.js \
  https://raw.githubusercontent.com/FailEcho/failecho/main/opencode-plugin/plugin/failecho.js
```

That is the whole installation: OpenCode auto-discovers any `*.js` in
`.opencode/plugin/`. Or, from npm:

```json
{ "plugin": ["failecho-opencode"] }
```

## What it looks like

```
$ curl -s https://api.github.com/repos/foo/bar/issues
HTTP/2 429
x-ratelimit-remaining: 0
rate limit exceeded

FailEcho: try wait_until_reset (worked 33/40).
```

The tool's own output is untouched; one line is appended after it.

## Why a plugin and not the MCP server

FailEcho also ships an MCP server, and OpenCode can register it. Then the
model has to decide to call a tool while it is busy failing at something else.
In our lab, over 32 runs, it decided to do that **0.19 times per run** — and
the first run under the strictest instruction we could write used `bash`
thirteen times and the tool zero.

`tool.execute.after` fires for every tool OpenCode runs: `bash`, `webfetch`,
and every MCP tool. Nothing is left to choose.

## What leaves your machine

Per call, this and nothing else:

| field | example |
|---|---|
| `service` | `api.github.com`, or the MCP server's name |
| `operation` | `GET /repos`, `create_issue` |
| `outcome` | `success` / `failure` |
| `error_type` | `rate_limit`, `server_error`, `timeout`, … |
| `error_code` | `429` |
| `latency_ms` | `1840` |

Never the command, the arguments, the output, a file path, an environment
variable, a token, or the URL beyond its host. The failure text is read in
this process to pick the class, then dropped.

Successes are reported too, because a failure rate without a denominator is
not a rate. A failure followed by a success on the same target inside two
minutes is reported as a recovery — that sequence is the only evidence the
network has about what actually fixes anything.

Local tools — `read`, `write`, `edit`, `grep`, `glob`, `list` — are never
reported. A missing file is your problem, not the network's. A `bash` command
with no URL in it is not reported either.

## Switches

| variable | effect |
|---|---|
| `FAILECHO_DISABLED=1` | nothing runs |
| `FAILECHO_ADVISE=0` | report, but never touch a tool's output |
| `FAILECHO_ENDPOINT` | somewhere else — a local instance, your own |
| `FAILECHO_REPORTER_ID` | a stable name for this installation |
| `FAILECHO_TEAM` | private mode: your evidence, never pooled |

## The contract

It never raises into the agent, never blocks it for longer than three seconds
waiting for advice, and never replaces a tool's output. If the network is down
or slow, the run continues exactly as it would have without the plugin —
there is a test for each of those.

MIT. Source and the rest of FailEcho: <https://github.com/FailEcho/failecho>
