# FailEcho

**Failure intelligence for AI agents and autonomous software.**
Before you retry, check the echo.

FailEcho is a live cross-agent failure intelligence network. AI agents share
privacy-safe tool failures and recovery outcomes so other agents can avoid
repeating the same bad retry.

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-1e6b45)
![FastAPI](https://img.shields.io/badge/FastAPI-async-17211d)
![MCP](https://img.shields.io/badge/MCP-streamable%20http-c00010)
![License MIT](https://img.shields.io/badge/license-MIT-4a574f)

```
Agent A fails.
FailEcho learns.

Agent B encounters the same failure.
It sees what actually worked for other agents.

Agent B benefits from evidence it never generated itself.
```

## Connect in one minute

**MCP endpoint**

```
https://failecho.com/mcp
```

```bash
claude mcp add --transport http failecho https://failecho.com/mcp
```

```json
{
  "mcpServers": {
    "failecho": { "type": "http", "url": "https://failecho.com/mcp" }
  }
}
```

Python, if you want failures *and* successes reported automatically:

```python
from failecho import FailEcho

echo = FailEcho("https://failecho.com", reporter_id="my-agent-1")

outcome = await echo.observe_tool_call(
    service="github-mcp",
    operation="create_issue",
    call=lambda: github.create_issue(**args),
)

if outcome.failed and outcome.decision.actionable:
    do(outcome.decision.recommendation)   # your code decides, never FailEcho
```

No account. No API key. Free during the public MVP.
Full integration guide: [Connect an agent](#connect-an-agent).

## What it does

See whether other AI agents are hitting the same tool failure right now — and
which recovery actions actually worked. FailEcho exposes a Model Context
Protocol (MCP) endpoint that agents can query after a tool failure, plus a REST
API.

| Tool | When the agent calls it |
|---|---|
| `check_tool_failure` | a tool failed — **before** retrying |
| `report_tool_failure` | contribute the failure |
| `report_tool_success` | contribute a success (the denominator) |
| `report_recovery_outcome` | say whether the fix worked |

FailEcho normalizes error text deterministically (no model) into a fingerprint,
accumulates recovery outcomes against it, and returns a recommendation only
when independent reporters agree. Thin evidence returns `INSUFFICIENT_DATA`
rather than a guess. Confidence is a Wilson score lower bound you can recompute
from the counts returned beside it.

It stores failure **metadata** only: no prompts, tool arguments, tool results,
request or response bodies, headers, keys or user content. Raw error text is
discarded after normalization.

**Live:** <https://failecho.com> · [/docs](https://failecho.com/docs) ·
[/openapi.json](https://failecho.com/openapi.json) ·
[/llms.txt](https://failecho.com/llms.txt)

This is **not** an observability platform, an error database, an uptime monitor
or an LLM debugger. The unit of the system is:

```
service + operation + version + schema_hash + failure fingerprint
                     + observed recovery outcomes
```

### Vocabulary

| Term | Meaning |
|---|---|
| **FailEcho Network** | the whole system |
| **Failure Echo** | a normalized observed failure, shared by fingerprint |
| **Recovery Echo** | evidence that a recovery action worked |
| **Incident** | a sudden abnormal failure increase |
| **Reporter** | an agent or runtime sending telemetry |
| **Fingerprint** | the canonical normalized error identity |

The brand vocabulary is for humans. Wire formats are deliberately unbranded:
endpoint paths, MCP tool names and field names (`fingerprint`,
`recommendation`, `recovery_actions`) stay exactly as they are, because machine
clarity outranks naming purity.

---

## See the network effect locally

Two terminals, about a minute.

```bash
# 1. the network
uv run uvicorn app.main:app --reload
#    or: .venv/bin/python -m uvicorn app.main:app --reload

# 2. six independent agents hitting the same broken tool
uv run python examples/live_agent/run_demo.py
#    or: .venv/bin/python examples/live_agent/run_demo.py
```

The demo starts a small local tool server, then runs six logically independent
agents against it. Every network call goes over **MCP**, from an external
process, using the official MCP SDK.

```
Agent A calls a tool. It fails: the provider renamed a field.
        |
        v
Agent A reports the failure          -> the network records it
Agent A has no evidence to go on, so it retries (fails),
        refreshes the tool schema (works), and reports both outcomes
        |
        v
Agents C, D, E, F hit the same failure with different repository ids
        -> normalization collapses all of them onto ONE fingerprint
        -> the network accumulates evidence from 5 independent reporters
        |
        v
Agent B hits the same failure with yet another id, and asks first
        -> the network recognises the fingerprint
        -> "refresh_schema: 5/5 successes, 5 reporters, confidence 0.57"
        -> "retry: 0/5. Do not bother."
        |
        v
Agent B skips the retry the others wasted a call on, refreshes, succeeds,
and reports its outcome -- which makes the next agent's answer better.
```

Agent B never met Agent A. It only met the network. That is the entire product.

Real output from the sixth agent, which had reported nothing before it asked:

```text
Calling tool...
x tool failed

  422 validation_error
  Repository 987654 rejected field body: field "body" is no longer accepted, use "content"

Checking shared failure intelligence...

  Fingerprint:            6ed9ef705ff4037af2c977306b8b9f92
  Known failure:          YES
  Observed failures:      11
  Independent reporters:  6
  Service status:         MAJOR

  Recovery actions others reported:
    refresh_schema        5/5 (100.0%) confidence 0.57 reporters 5
    retry                 0/5 (0.0%) confidence 0.00 reporters 5

Best observed recovery:
  refresh_schema
  Skipping retry: other agents already proved it does not work here.

Applying recovery: refresh_schema
  Refreshed tool schema -> v3.0.0, field 'content'
  Retrying tool call...
  + tool call succeeded

Reporting recovery outcome...
+ accepted   (refresh_schema -> success)
```

Watch it land on the homepage at <http://localhost:8000> while the demo runs.
Demo agents label themselves with `X-Reporter-Kind: demo`, so their traffic is
real evidence but is **never** counted as adoption — see [Demo data](#demo-data).

Details, including how to run the tool server separately, are in
[`examples/live_agent`](examples/live_agent).

---

## Connect an agent

Two ways in, and the difference matters.

**MCP** lets an agent *explicitly* ask and report — the model decides when to
call `check_tool_failure`, so you get intelligence exactly where the agent
reasons about a failure, and nothing else.

**SDK instrumentation** reports success and failure telemetry *automatically*
for every tool call, without the model deciding anything. That is what
produces denominators, and without denominators every failure rate in the
network is meaningless.

Most deployments want both.

### 1. MCP

```bash
claude mcp add --transport http failecho https://failecho.com/mcp
```

```json
{
  "mcpServers": {
    "failecho": {
      "type": "http",
      "url": "https://failecho.com/mcp"
    }
  }
}
```

| Tool | When the agent calls it |
|---|---|
| `check_tool_failure` | a tool failed — **before** retrying |
| `report_tool_failure` | contribute the failure |
| `report_tool_success` | contribute a success (the denominator) |
| `report_recovery_outcome` | say whether the fix worked |

### 2. Python

Copy `client/` into your project (not published to PyPI yet), then:

```python
from failecho import FailEcho

echo = FailEcho(
    endpoint="https://failecho.com",
    reporter_id="my-agent-1",       # optional, hashed server-side
)

outcome = await echo.observe_tool_call(
    service="github-mcp",
    operation="create_issue",
    version="2.8.1",
    schema_hash="a817ce",
    call=lambda: github.create_issue(**args),
)

if outcome.failed and outcome.decision.actionable:
    # YOUR code decides. FailEcho never acts on your behalf.
    if outcome.decision.confidence > 0.8:
        refresh_schema()
        await echo.report_recovery(
            fingerprint=outcome.decision.fingerprint,
            action="refresh_schema",
            successful=True,
        )
```

`observe_tool_call` reports the success or the failure, queries FailEcho when
the call failed, and hands you a `FailureDecision`. It never retries, never
refreshes and never falls back — executing a recovery can double-post or
double-charge, so that decision stays yours.

**It cannot break your agent.** Every call is fail-soft: a timeout or an
unreachable host is swallowed and your tool result is returned anyway. Set
`FAILECHO_DISABLED=1` and the whole client becomes a no-op.

### 3. Framework instrumentation

Reference integration, Pydantic AI:

```python
from failecho import FailEcho
from failecho.integrations.pydantic_ai import instrument_toolset

echo = FailEcho("https://failecho.com", reporter_id="my-agent-1")
agent = Agent("openai:gpt-4o", toolsets=[instrument_toolset(my_toolset, echo)])
```

Every tool call now reports its outcome. The wrapper is behaviourally
invisible: same results, same exceptions, same control flow. Tool arguments are
never read and never sent.

Other frameworks (LangChain, LlamaIndex, CrewAI, OpenAI Agents SDK, Claude Code
hooks) are not built yet. They should implement
`failecho.adapters.ToolTelemetrySink` — four events, one direction — rather
than touch FailEcho's core. See `client/failecho/adapters.py`.

### 4. REST

```bash
curl -X POST https://failecho.com/v1/query \
  -H "Content-Type: application/json" \
  -H "X-Reporter-ID: my-agent-1" \
  -d '{
    "service": "github-mcp",
    "operation": "create_issue",
    "error_type": "validation_error",
    "error_code": "422",
    "error_message": "Repository 555812 was not found"
  }'
```

### About reporter IDs

Optional, and never required. A stable one is salted and hashed on arrival —
the raw value is never stored — and it improves three things: independent
reporter counting, poisoning resistance, and FailEcho's ability to tell you
that a recommendation came from somebody other than you. Anonymous reporting
stays fully supported.

---

## Concept

```
Agent A fails
    |
    v
reports anonymously  ---------> network learns
                                    |
Agent B hits the same problem       |
    |                               |
    v                               v
queries the network  <---------  what happened to others
    |
    v
skips the useless retry, uses the recovery that works
```

---

## Run locally

Python 3.11+.

```bash
# with uv
uv venv
uv pip install -r requirements.txt
uv run uvicorn app.main:app --reload

# or plain venv + pip
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload
```

Seed synthetic demo data so the homepage has something to show:

```bash
.venv/bin/python scripts/seed_demo.py            # add demo data
.venv/bin/python scripts/seed_demo.py --reset    # replace existing demo data
.venv/bin/python scripts/seed_demo.py --purge    # remove demo data
```

Then:

* homepage — <http://localhost:8000>
* MCP endpoint — <http://localhost:8000/mcp> (Streamable HTTP)
* agent-readable overview — <http://localhost:8000/llms.txt>
* API docs — <http://localhost:8000/docs>
* machine-readable schema — <http://localhost:8000/openapi.json>
* health — <http://localhost:8000/health>

Run the tests:

```bash
.venv/bin/python -m pytest
```

Fold expired raw observations into hourly aggregates (safe to run any time):

```bash
.venv/bin/python scripts/prune.py --dry-run
.venv/bin/python scripts/prune.py
```

End-to-end examples (server must be running):

```bash
.venv/bin/python client/example_agent.py            # REST, single agent
.venv/bin/python examples/live_agent/run_demo.py    # MCP, six agents, network effect
```

The demo runs its tool server in a background thread. To run it separately
(two terminals) instead:

```bash
.venv/bin/python examples/live_agent/tool_server.py
.venv/bin/python examples/live_agent/run_demo.py --no-tool-server
```

---

## MCP

The MCP server runs **inside the same FastAPI process** — no second service to
deploy or supervise — and speaks Streamable HTTP at `/mcp`. It is stateless
with JSON responses: no per-session memory, no long-lived streams, which is
what keeps it viable on a small VPS.

### Connect

Claude Code:

```bash
claude mcp add --transport http failecho https://failecho.com/mcp
# local:
claude mcp add --transport http failecho http://localhost:8000/mcp
```

Generic MCP client config (`mcpServers` style):

```json
{
  "mcpServers": {
    "failecho": {
      "type": "http",
      "url": "https://failecho.com/mcp"
    }
  }
}
```

Raw JSON-RPC, if you want to see it work:

```bash
curl -s localhost:8000/mcp \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

### Tools

| Tool | Purpose |
|---|---|
| `check_tool_failure` | **Call before retrying.** What is happening with this failure right now, and what recovery actually worked? |
| `report_tool_failure` | Contribute a failure observation. Returns its fingerprint. |
| `report_tool_success` | Contribute a success, so failure rates have a denominator. |
| `report_recovery_outcome` | Report whether a recovery action worked. |

All four call the same functions as the REST endpoints (`app/core/service.py`),
so an MCP client and a curl user can never disagree about what a failure means
— there is one normalizer, one fingerprint function, one intelligence layer.

Example `check_tool_failure` result:

```json
{
  "known": true,
  "fingerprint": "01ae47053fbb3eabf8f3e480cba45ba8",
  "status": "MAJOR",
  "observations": { "total": 418, "last_5m": 81, "last_1h": 201, "unique_reporters": 47 },
  "failure_rate": { "last_5m": 0.73, "last_1h": 0.31 },
  "recovery_actions": [
    { "action": "refresh_schema", "attempts": 124, "successes": 117,
      "success_rate": 0.9435, "effective_attempts": 124, "unique_reporters": 45,
      "confidence": 0.8881 }
  ],
  "recommendation": { "action": "refresh_schema", "confidence": 0.8881 },
  "demo_data_included": false
}
```

`demo_data_included` tells an agent when synthetic demo rows are part of the
numbers. Disable MCP entirely with `FIN_MCP_ENABLED=0`.

---

## REST API

Three calls. No account, no API key, no payment.

| Endpoint | When to call it |
|---|---|
| `POST /v1/observe` | after every tool call — successes **and** failures |
| `POST /v1/query` | when a call fails, **before** you retry |
| `POST /v1/outcome` | after you tried a recovery action |

### Report a failure

```bash
curl -s localhost:8000/v1/observe \
  -H 'content-type: application/json' \
  -H 'X-Reporter-ID: my-agent-1' \
  -d '{
    "service": "github-mcp",
    "operation": "create_issue",
    "version": "2.8.1",
    "schema_hash": "a817ce",
    "outcome": "failure",
    "error_type": "validation_error",
    "error_code": "422",
    "error_message": "Repository 918272 was not found",
    "latency_ms": 421
  }'
```

```json
{
  "accepted": true,
  "fingerprint": "01ae47053fbb3eabf8f3e480cba45ba8",
  "known": true,
  "observations": 143,
  "normalized_error": "Repository <N> was not found"
}
```

The message is normalized before anything is stored:
`Repository 918272 was not found` → `Repository <N> was not found`. The
fingerprint is `sha256(service | operation | version | schema_hash |
error_type | error_code | normalized_error)`, truncated to 32 hex chars.

### Report a success

Failure rates need a denominator, so send successes too:

```bash
curl -s localhost:8000/v1/observe \
  -H 'content-type: application/json' \
  -d '{
    "service": "github-mcp", "operation": "create_issue",
    "version": "2.8.1", "schema_hash": "a817ce",
    "outcome": "success", "latency_ms": 318
  }'
```

### Query the network

```bash
curl -s localhost:8000/v1/query \
  -H 'content-type: application/json' \
  -d '{
    "service": "github-mcp",
    "operation": "create_issue",
    "version": "2.8.1",
    "schema_hash": "a817ce",
    "error_type": "validation_error",
    "error_code": "422",
    "error_message": "Repository 555812 was not found"
  }'
```

```json
{
  "known": true,
  "fingerprint": "01ae47053fbb3eabf8f3e480cba45ba8",
  "status": "MAJOR",
  "looks_new": false,
  "observations": { "total": 418, "last_5m": 81, "last_1h": 201, "unique_reporters": 47 },
  "failure_rate": { "last_5m": 0.73, "last_1h": 0.31 },
  "recovery_actions": [
    { "action": "refresh_schema", "attempts": 124, "successes": 117,
      "success_rate": 0.9435, "confidence": 0.8881 },
    { "action": "retry", "attempts": 91, "successes": 17,
      "success_rate": 0.1868, "confidence": 0.12 }
  ],
  "recommendation": {
    "action": "refresh_schema", "confidence": 0.8881,
    "based_on_attempts": 124, "based_on_successes": 117
  }
}
```

When the network has nothing useful:

```json
{ "known": false, "status": "INSUFFICIENT_DATA", "recommendation": null }
```

`/v1/query` is read-only. It stores nothing.

### Report a recovery outcome

```bash
curl -s localhost:8000/v1/outcome \
  -H 'content-type: application/json' \
  -d '{
    "fingerprint": "01ae47053fbb3eabf8f3e480cba45ba8",
    "action": "refresh_schema",
    "successful": true
  }'
```

```json
{ "accepted": true }
```

Actions are free-form strings in V1. Common ones: `retry`, `wait`,
`refresh_schema`, `remove_optional_field`, `reconnect`, `use_fallback`,
`reauthenticate`, `abort`.

### Status

```bash
curl -s localhost:8000/v1/services              # per service/operation health, worst first
curl -s localhost:8000/v1/stats                 # counters; real and synthetic kept separate
curl -s localhost:8000/v1/recovery-intelligence # best evidenced recovery actions
curl -s localhost:8000/health                   # {"status":"ok"}
curl -s localhost:8000/llms.txt                 # agent-readable description of the service
```

---

## Python client

Zero dependencies — standard library only. Copy `client/failure_network.py`
and `client/failecho.py` into your agent (the package is not published yet).

`failecho` is the preferred import name and simply re-exports
`failure_network`, which keeps working unchanged — the rename is additive, so
no existing code breaks.

```python
from failecho import Client   # or: from failure_network import Client

client = Client("http://localhost:8000", reporter_id="my-agent-1")

client.observe_failure(
    service="github-mcp",
    operation="create_issue",
    version="2.8.1",
    schema_hash="abc",
    error_type="validation_error",
    error_code="422",
    error_message="Repository 91827 not found",
)

intel = client.query(
    service="github-mcp",
    operation="create_issue",
    version="2.8.1",
    schema_hash="abc",
    error_type="validation_error",
    error_code="422",
    error_message="Repository 12345 not found",
)

if intel["recommendation"]:
    action = intel["recommendation"]["action"]     # e.g. "refresh_schema"
    client.report_recovery(
        fingerprint=intel["fingerprint"], action=action, successful=True
    )

client.observe_success(service="github-mcp", operation="create_issue", latency_ms=318)
```

Every call is **fail-soft**: a timeout or an unreachable server returns `None`
(or a neutral `INSUFFICIENT_DATA` dict from `query`) instead of raising.
Telemetry must never break the agent it observes.

---

## Privacy

Privacy is a product feature, not a setting.

**Collected** — structured failure metadata only:

| Field | Notes |
|---|---|
| `service`, `operation`, `version`, `schema_hash` | what was called |
| `outcome` | `success` or `failure` |
| `error_type`, `error_code` | short classifiers |
| `normalized_error` | identifiers replaced, secrets redacted |
| `latency_ms` | |
| `fingerprint` | SHA-256 digest |
| `reporter_hash` | salted hash of an optional header, or `NULL` |
| `created_at`, `source` | |

**We do not want, and never store:**

* prompts
* model messages
* tool arguments
* tool results
* request bodies and response bodies
* HTTP headers and cookies
* API keys, tokens and secrets
* customer names, emails and any user content
* credit-card data

Metadata only. If a field is not in the table above, this network does not
want it — and the schemas give it nowhere to land.

How that is enforced:

1. The request schemas have no fields for any of it. Unknown JSON keys are
   dropped by Pydantic before the handler runs, so an agent that accidentally
   sends `{"prompt": ...}` cannot persist it here.
2. The raw `error_message` is normalized at the edge and the raw string is
   discarded — never written to a column, never logged. Only
   `normalized_error` survives.
3. Normalization runs a redaction pass first: credential-shaped substrings
   (bearer tokens, API keys, JWTs, card-shaped digit groups) become
   `<REDACTED>` rather than being categorised and kept.
4. `X-Reporter-ID` is optional, salted with `FIN_REPORTER_SALT` and hashed on
   arrival. The raw value is never stored. Rotating the salt makes existing
   hashes unlinkable.
5. There is no authentication, so there is no account, email or billing
   identity to leak in the first place.

Normalization examples:

```
Repository 918272 was not found              -> Repository <N> was not found
User carol@acme.com at 10.0.12.7 failed      -> User <EMAIL> at <IP> failed
GET https://api.example.com/v1/x?y=2 failed  -> GET <URL> failed
token=sk_live_9aBc12345678xyz rejected       -> <REDACTED> rejected
HTTP 422 unprocessable                       -> HTTP 422 unprocessable   (unchanged)
```

Small numbers survive on purpose: `422` and `500` are semantics, not
identifiers. See `app/core/normalize.py` and `app/core/privacy.py`.

---

## How the numbers are produced

Everything is deterministic arithmetic over observation counts. No model, no
learned parameter, nothing you cannot recompute yourself.

**Incident status** (MVP heuristic, constants in `app/core/config.py`):

```
< 10 observations in the last hour        -> INSUFFICIENT_DATA
failure rate < 5%                         -> HEALTHY
failure rate >= 5%  and < 30%             -> DEGRADED
failure rate >= 30%                       -> MAJOR
```

The 5-minute window takes over from the 1-hour window once it holds at least 5
observations, so a fresh incident is not diluted by an hour of healthy history.
This is a threshold on a ratio — not change-point detection, not seasonality
aware, not statistically calibrated. It is labelled MVP logic on purpose.

**Recovery confidence** is the lower bound of the 95% Wilson score interval for
that action's success rate. It folds sample size into the number, so 5/5
successes ranks below 117/124 successes. An action is only recommended with at
least **5 attempts** and a **60% success rate**, and confidence is capped below
1.0. Thin evidence returns `"recommendation": null`. The network never
fabricates confidence.

**Unique reporters** counts distinct non-null reporter hashes, so one agent
sending 1000 events does not look like 1000 independent reporters. Anonymous
observations are excluded from that count, making it a lower bound.

---

## Abuse floor (V1)

No accounts, so the defences are structural rather than identity-based. Two
independent layers, both transparent:

**Per-reporter evidence cap.** For confidence and recommendations, one reporter
contributes at most `FIN_MAX_REPORTER_WEIGHT_PER_HOUR` (default **5**)
attempts per `fingerprint + action + hour`. Raw counts are still reported
verbatim — the API returns `attempts` alongside `effective_attempts`, so you
can see both what was reported and what actually counted. Successes are scaled
down proportionally when a bucket is capped, so trimming volume never invents a
better success rate. All anonymous reports in a bucket are treated as **one**
reporter: unattributed evidence cannot prove it is independent.

**Reporter diversity.** A recommendation needs 5 effective attempts and a 60%
success rate. Evidence backed by fewer than `FIN_MIN_UNIQUE_REPORTERS`
(default **3**) distinct reporters is not blocked — anonymous reporting is a
supported mode — but its confidence is multiplied by
`FIN_LOW_DIVERSITY_CONFIDENCE_FACTOR` (default **0.7**).

**Write rate limiting.** `POST /v1/observe`, `POST /v1/outcome` and the MCP
reporting tools share one budget of `FIN_RATE_LIMIT_WRITES_PER_MINUTE`
(default **120**) per client IP — switching transport does not buy a second
budget. Reads are never rate limited; querying is the product. The limiter is
an in-process dict: **it is not distributed**, so a second worker would get its
own budget, and it does not stop a distributed flood. The evidence cap is the
defence that survives an attacker who changes IP, because it limits influence
rather than requests.

Behind Cloudflare or nginx, set `FIN_TRUST_PROXY=1` so the limiter reads
`CF-Connecting-IP` / `X-Forwarded-For` instead of the proxy's own address.
Leave it off when the server is directly exposed: trusting those headers would
let any client forge its own rate-limit identity.

**Reporter identity** is still optional and still hashed with a salt before
storage. Raw identifiers are never written anywhere.

---

## Retention and pruning

Raw observations are the hot path (the 5-minute and 1-hour windows read them
directly) and also the thing that grows without bound. So:

```
raw observations   kept FIN_RETENTION_HOURS (default 48h)
                   then folded into hourly aggregates and deleted
hourly aggregates  kept indefinitely
```

Two aggregate tables: `hourly_stats` (successes, failures, unique reporters,
latency sum/count per hour × service × operation × version × schema × source)
and `hourly_recovery_stats` (attempts, successes, and the **capped** effective
counts per hour × fingerprint × action).

**The invariant:** a raw row is aggregated and deleted inside one transaction,
so aggregates only ever describe rows that no longer exist. "Raw + aggregates"
is a total, never a double count — and re-running the pruner is a no-op,
because what it already folded is gone. Short windows (5m, 1h) always read raw
rows only, so pruning can never change a live status. The recovery cap is
applied per hour bucket, which is exactly the grain the aggregates use, so
pruning cannot change a recommendation either.

```bash
python scripts/prune.py                # use FIN_RETENTION_HOURS
python scripts/prune.py --hours 24     # override the window
python scripts/prune.py --dry-run      # report only, change nothing
python scripts/prune.py --vacuum       # also reclaim file space (briefly locks)
```

```text
Retention window: 48h
Cutoff:           2026-09-08T09:51:18Z
Aggregated 18429 observations into 96 hourly buckets
Aggregated 812 recovery outcomes into 41 hourly buckets
Deleted 18429 raw observations
Deleted 812 raw recovery outcomes
Database size:    4.21 MB
```

Recommended cron (hourly, at :15) — **not needed for local development**:

```cron
15 * * * * /srv/failure-network/.venv/bin/python /srv/failure-network/scripts/prune.py >> /var/log/failure-network-prune.log 2>&1
```

Or use the bundled systemd timer: `deploy/failure-network-prune.timer`.

---

## Project layout

```
app/
  main.py               FastAPI app, CORS, static homepage, /health, /llms.txt
  mcp_server.py         MCP tools + Streamable HTTP endpoint (same process)
  api/                  observe.py  query.py  outcome.py  services.py  deps.py
  core/                 normalize.py  fingerprint.py  intelligence.py
                        service.py  retention.py  ratelimit.py
                        privacy.py  config.py  clock.py
  db/                   database.py (async engine)  models.py
  schemas/              Pydantic request/response models with agent-readable docs
  web/static/           index.html  style.css  app.js   (no framework, no build)
                        logo.svg  favicon.svg  og-image.svg
client/
  failecho/             the public client package
    __init__.py         FailEcho: observe_tool_call, report_*, query
    adapters.py         ToolTelemetrySink -- the framework seam
    integrations/
      pydantic_ai.py    reference integration (optional dependency)
  failure_network.py    zero-dependency REST client (still supported)
  auto_recovery.py      the passive wrapper FailEcho is built on
  auto_recovery.py      failure-aware tool wrapper (reports + asks, never acts)
  example_agent.py      end-to-end REST usage example
examples/live_agent/
  tool_server.py        local tool that just shipped a breaking change
  tool_client.py        agent-side tool client with a stale cached schema
  network.py            MCP client (official SDK) for the four network tools
  agents.py             the autonomous loop: fail -> report -> ask -> recover
  run_demo.py           one command, six independent agents
scripts/
  seed_demo.py          synthetic demo telemetry (source='synthetic')
  prune.py              aggregate + delete expired raw rows
deploy/
  failure-network.service        systemd unit
  failure-network-prune.timer    hourly retention timer
  Caddyfile.failecho-dev         optional origin-level .dev redirect
LICENSE  SECURITY.md  CONTRIBUTING.md  .env.example
tests/                  the suite
```

`app/core/service.py` is the seam that keeps transports honest: REST handlers
and MCP tools both call `record_observation`, `query_intelligence` and
`record_recovery_outcome`. Nothing in `app/core/` knows what HTTP is, so the
next transport (OTel receiver, worker, CLI) plugs in the same way.

## Demo data

`scripts/seed_demo.py` writes ~2000 observations and ~300 recovery outcomes
across four services, every row tagged `source='synthetic'`:

| Service | Operation | Scenario |
|---|---|---|
| `github-mcp` | `create_issue` | **MAJOR** — schema drift; `refresh_schema` fixes it, `retry` does not |
| `search-api` | `search` | **DEGRADED** — upstream timeouts; `use_fallback` works |
| `stripe-mcp` | `create_refund` | **HEALTHY** — occasional rate limiting |
| `example-agent-tool` | `run` | **HEALTHY** — rare crash, only 3 recovery attempts, so no recommendation is given |

There are two kinds of non-real telemetry, and both are labelled at the row
level by a `source` column:

| `source` | Where it comes from | Counted as adoption |
|---|---|---|
| `agent` | a real autonomous system | **yes** |
| `demo_agent` | a caller that sent `X-Reporter-Kind: demo` (the demo agents) | no |
| `synthetic` | `scripts/seed_demo.py` | no |

`demo_agent` rows are *real* observations from *real* tool calls — the demo
genuinely breaks a tool and genuinely recovers — but they are demonstrations,
so they stay out of adoption metrics. Self-labelling can only ever downgrade a
report: nothing a caller sends can promote a row to real telemetry, which is
why trusting the header is safe.

`FIN_DEMO_MODE=1` marks a deployment as a demonstration instance: `/v1/stats`
returns `demo_mode: true` and the homepage shows a `DEMO MODE` badge. It never
generates traffic — it only labels what is already stored. Nothing in this
project fabricates telemetry at startup.

Both kinds are tracked separately everywhere they surface:

* `/v1/stats` reports `real_observations_total`, `real_observations_24h`,
  `real_reporters_24h` and `real_failure_fingerprints` **excluding** all demo
  rows, plus `synthetic_observations` and `demo_agent_observations` separately.
  They are never summed into one adoption number.
* the homepage renders real telemetry in the headline block and synthetic
  counters in a separate, visibly labelled block;
* `/v1/recovery-intelligence` flags every entry with `demo_data: true|false`
  (`?include_demo=false` hides them);
* `POST /v1/query` and the MCP `check_tool_failure` tool return
  `demo_data_included`, so an autonomous caller knows when it is acting on
  demo evidence.

Remove it all with `python scripts/seed_demo.py --purge`.

---

## Configuration

Every setting is an environment variable; defaults are in
`app/core/config.py`.

| Variable | Default | Meaning |
|---|---|---|
| `FIN_DATABASE_URL` | `sqlite+aiosqlite:///./data/failure_network.db` | swap for `postgresql+asyncpg://...` later |
| `FIN_REPORTER_SALT` | `dev-salt-change-me` | **change in production**; rotating it unlinks old hashes |
| `FIN_WINDOW_SHORT_SECONDS` | `300` | short window |
| `FIN_WINDOW_LONG_SECONDS` | `3600` | long window |
| `FIN_MIN_OBSERVATIONS_FOR_STATUS` | `10` | below this: `INSUFFICIENT_DATA` |
| `FIN_HEALTHY_MAX_FAILURE_RATE` | `0.05` | |
| `FIN_DEGRADED_MAX_FAILURE_RATE` | `0.30` | |
| `FIN_MIN_RECOVERY_ATTEMPTS` | `5` | evidence floor for a recommendation |
| `FIN_MIN_RECOVERY_SUCCESS_RATE` | `0.60` | |
| `FIN_MAX_CONFIDENCE` | `0.99` | never claim certainty |
| `FIN_MAX_REPORTER_WEIGHT_PER_HOUR` | `5` | max attempts one reporter contributes per fingerprint+action+hour |
| `FIN_MIN_UNIQUE_REPORTERS` | `3` | below this, confidence is discounted (never blocked) |
| `FIN_LOW_DIVERSITY_CONFIDENCE_FACTOR` | `0.7` | the discount |
| `FIN_RATE_LIMIT_ENABLED` | `1` | write rate limiting on/off |
| `FIN_RATE_LIMIT_WRITES_PER_MINUTE` | `120` | per client IP, REST + MCP combined |
| `FIN_TRUST_PROXY` | `0` | read `CF-Connecting-IP` / `X-Forwarded-For`; only behind a real proxy |
| `FIN_RETENTION_HOURS` | `48` | raw observations older than this are aggregated and deleted |
| `FIN_MCP_ENABLED` | `1` | mount the MCP endpoint |
| `FIN_MCP_PATH` | `/mcp` | where to mount it |
| `FIN_MCP_ALLOWED_HOSTS` | *(empty)* | comma list; enables DNS-rebinding protection when set |
| `FIN_MCP_ALLOWED_ORIGINS` | *(empty)* | comma list; same |
| `FIN_ALLOWED_ORIGINS` | `*` | CORS origins for browsers (comma-separated) |
| `FIN_PUBLIC_URL` | `http://localhost:8000` | canonical public origin; drives canonical/OG tags, `/llms.txt` and every on-page example |
| `FIN_GITHUB_URL` | *(empty)* | repository link; while empty, no GitHub link is rendered anywhere |
| `FIN_DEMO_MODE` | `0` | label this deployment as a demo instance (generates nothing) |

### Deploying on a small VPS

### Brand assets

```
app/web/static/logo.svg       the mark, inherits surrounding text colour
app/web/static/favicon.svg    the mark with fixed neutrals, for tab bars
app/web/static/og-image.svg   1200x630 social card
```

The mark is a failure event and its echo: one tall stroke in signal red,
repeating outward and decaying. It carries no baked-in wordmark — "FailEcho"
is always HTML text beside it, so the mark stays usable at 16px and as an
avatar.

`og-image.svg` is served as-is. **Most social platforms do not render SVG
previews**; when a PNG becomes necessary, export it once with any tool and
drop it next to the SVG rather than adding a rendering dependency to the
service.

### Domains

`failecho.com` is the canonical public origin. Everything an agent or a human
needs lives on it:

```
https://failecho.com/              homepage
https://failecho.com/mcp           MCP endpoint (Streamable HTTP)
https://failecho.com/docs          API reference
https://failecho.com/openapi.json  machine-readable schema
https://failecho.com/llms.txt      plain-text summary for agents
```

`failecho.dev` is a secondary domain and redirects permanently to
`failecho.com`, preserving the path:

```
https://failecho.dev/*      ->  301  ->  https://failecho.com/*
https://failecho.dev/docs   ->  301  ->  https://failecho.com/docs
https://failecho.dev/mcp    ->  301  ->  https://failecho.com/mcp
```

Do this at the edge, not in the application. The app has no notion of a second
domain and should not grow one.

**`www.failecho.com` → `failecho.com`** is handled at the origin by Caddy
(`redir https://failecho.com{uri} permanent`), so it needs no Cloudflare rule —
only a proxied DNS record for `www`.

**Cloudflare (preferred) for the `.dev` domain.** Add `failecho.dev` to the same
account, then **Rules → Redirect Rules → Create rule**:

| Field | Value |
|---|---|
| When incoming requests match | `Hostname` `contains` `failecho.dev` |
| Then | Dynamic redirect |
| Expression | `concat("https://failecho.com", http.request.uri.path)` |
| Status | `301` |
| Preserve query string | on |

One rule covers both `failecho.dev` and `www.failecho.dev` — the `Hostname
contains` match catches each — and it costs nothing on the free plan. Never
serve a copy of the site from `.dev`: two origins with the same content is the
classic way to have Google pick the wrong canonical. Both hostnames still need proxied DNS records (an `A` to the
origin, or an `AAAA` to `100::` if you would rather the origin never see the
request at all).

**Caddy fallback**, if you ever serve `.dev` from the origin instead — the
config ships in `deploy/Caddyfile.failecho-dev`:

```caddy
failecho.dev, www.failecho.dev {
	redir https://failecho.com{uri} permanent
}
```

`api.failecho.com` is deliberately **not** used in this MVP: a second origin
would mean a second certificate, a second CORS surface and a second thing to
explain, for no benefit while the API and the site are the same process.

Set the origin once, in one place:

```bash
FIN_PUBLIC_URL=https://failecho.com
```

It drives the canonical tag, Open Graph URLs, `/llms.txt`, the MCP endpoint
shown on the homepage and every copyable example. No file in the codebase
hardcodes the domain. Left unset, everything falls back to the request's own
origin, so local development and IP-address access both stay correct.

### Cloudflare checklist

**DNS**

| Name | Type | Value | Proxy |
|---|---|---|---|
| `failecho.com` | A | origin IP | proxied |
| `www.failecho.com` | A | origin IP | proxied |
| `failecho.dev` | A | origin IP | proxied |
| `www.failecho.dev` | A | origin IP | proxied |

`www.failecho.com` → `failecho.com` is handled by Caddy (`redir ... permanent`).
The `.dev` hostnames are handled by the redirect rule above.

**Order matters on first setup:** leave the records **unproxied (grey cloud)**
until Caddy has obtained its Let's Encrypt certificate, then switch to proxied
and set **SSL/TLS → Overview → Full (strict)**. Turning the proxy on first, or
leaving the mode on "Flexible", is the usual way this goes wrong.

**Caching.** Never cache the live surfaces. Caddy already sends
`Cache-Control: no-store` for `/v1/*`, `/health` and `/mcp`, and
`max-age=3600` for `/static/*`; leave Cloudflare on "Respect origin headers"
rather than adding a blanket cache rule. Caching `/mcp` would break MCP
sessions, and caching `/v1/stats` would make the live network look frozen.

**Rate limiting.** Cloudflare rate limiting is a supplement, not a replacement:
FailEcho's own per-IP write limit and per-reporter evidence cap must keep
working with the proxy off, because they are what stop poisoning, and poisoning
does not care about your CDN. Nothing here requires a paid Cloudflare plan.

### Deployment topology

**Intended topology.** The app binds to loopback only; TLS and the public
address belong to Cloudflare and a local reverse proxy:

```
   internet
      |
      v
  Cloudflare (TLS, DNS, DDoS)
      |
      v
  nginx / caddy on the VPS  (:443 -> :8000)
      |
      v
  uvicorn 127.0.0.1:8000    FastAPI + SQLite (WAL)
```

Do **not** bind uvicorn to `0.0.0.0` in this topology. Binding publicly skips
the proxy, exposes the origin directly, and makes `FIN_TRUST_PROXY=1` unsafe
(any client could then forge `X-Forwarded-For` and bypass the rate limit).
Docker is optional and not required.

**Step 1 — generate the reporter salt once and keep it.**

```bash
sudo install -d -o failurenet -g failurenet /srv/failure-network/data
printf 'FIN_REPORTER_SALT=%s\n' "$(openssl rand -hex 16)" \
  | sudo tee /etc/failure-network.env > /dev/null
sudo chmod 600 /etc/failure-network.env
```

Generating it inline on the command line would mint a **new salt on every
restart**, which silently resets every reporter hash and every unique-reporter
count. Generate once, store once.

**Step 2 — production command** (what the systemd unit runs):

```bash
set -a; . /etc/failure-network.env; set +a
export FIN_DATABASE_URL="sqlite+aiosqlite:////srv/failure-network/data/failure_network.db"
export FIN_PUBLIC_URL="https://failecho.com"
export FIN_TRUST_PROXY=1
export FIN_ALLOWED_ORIGINS='*'
export FIN_RETENTION_HOURS=48

/srv/failure-network/.venv/bin/python -m uvicorn app.main:app \
  --host 127.0.0.1 --port 8000 --workers 1 \
  --proxy-headers --forwarded-allow-ips '127.0.0.1' --no-server-header
```

Note the **four** slashes in the SQLite URL: `sqlite+aiosqlite:///` plus the
absolute path `/srv/...`. Three slashes would make it relative to the working
directory.

**Step 3 — reverse proxy.** Caddy:

```caddy
failures.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

nginx:

```nginx
location / {
    proxy_pass         http://127.0.0.1:8000;
    proxy_set_header   Host $host;
    proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto $scheme;
    proxy_buffering    off;   # keeps /mcp responsive
}
```

Then `sudo cp deploy/failure-network.service /etc/systemd/system/` and
`sudo systemctl enable --now failure-network`.

**Environment checklist**

| Variable | Production value | Why |
|---|---|---|
| `FIN_REPORTER_SALT` | 32 hex chars from `/etc/failure-network.env` | set once; rotating it unlinks existing reporter hashes |
| `FIN_DATABASE_URL` | `sqlite+aiosqlite:////srv/failure-network/data/failure_network.db` | absolute path, four slashes |
| `FIN_TRUST_PROXY` | `1` **only** behind the proxy above | otherwise clients forge their own rate-limit identity |
| `FIN_ALLOWED_ORIGINS` | `*`, or `https://yourdomain` | comma-separated; `*` keeps the public API browser-callable |
| `FIN_RETENTION_HOURS` | `48` | raw rows older than this become hourly aggregates |
| `FIN_DEMO_MODE` | `0` in production | `1` only for a demonstration instance |
| `FIN_PUBLIC_URL` | `https://failecho.com` | canonical origin for links, tags and examples |
| `FIN_GITHUB_URL` | repository URL, or unset | no link is rendered while unset |

**Other notes**

* **One worker.** SQLite serialises writes anyway, and the rate limiter and MCP
  session manager are per-process — two workers would mean two independent
  rate-limit budgets. Scale out only after moving to PostgreSQL.
* **MCP** is served from the same process at `/mcp`; it answers with plain
  JSON, so no SSE-specific proxy tuning is needed beyond disabling buffering.
* **Retention:** `deploy/failure-network-prune.timer`, or the cron line above.
* **Backups:** copy `data/` (including `-wal`/`-shm`) or run
  `sqlite3 data/failure_network.db ".backup backup.db"`. No downtime needed.

Resident memory is well under 150 MB with the MCP server mounted; SQLite runs
in WAL mode with `synchronous=NORMAL` and a 5 s busy timeout, so readers are
not blocked by writers.

### Migrating to PostgreSQL later

Every column type is portable, timestamps are naive UTC, there are no
SQLite-specific types and no expression indexes. Migration is
`FIN_DATABASE_URL=postgresql+asyncpg://...` plus `pip install asyncpg` and one
Alembic baseline.

---

## Is it working?

FailEcho publishes the numbers that decide whether the idea holds, on
`/v1/stats`. They are deliberately unflattering.

| Metric | What it answers |
|---|---|
| `real_observations_24h` | is anything real arriving? |
| `real_successes_24h` | do we have denominators, or only complaints? |
| `real_reporters_24h` | how many *independent* systems? |
| `known_hit_rate_24h` | when an agent asks, does FailEcho know anything? |
| `recovery_outcome_ratio_24h` | do agents say whether the fix worked? |
| `cross_agent_help_24h` | did an agent use evidence it did not generate? |

`cross_agent_help_24h` is the one that matters. It counts a query only when the
caller identified itself, a recommendation was returned, and at least one
reporter behind that recommendation was somebody else. Anonymous callers and
single-reporter evidence are not counted — undercounting the effect is honest,
overcounting it is not.

`recovery_outcome_ratio_24h` is the fragile one. Reporting a failure is
automatic; reporting whether the fix worked requires the agent to come back
afterwards. Without those reports FailEcho is an error counter.

### Launch milestones

Internal experiment markers, not marketing claims:

```
1   one real reporter
2   ten independent real reporters
3   100+ real observations per day
4   the first repeated real fingerprint
5   the first real cross-agent recovery benefit
```

Milestone 5 is the hypothesis: an agent hits a failure, queries FailEcho,
receives evidence generated by unrelated agents, changes behaviour, and
recovers. Everything before it is plumbing.

---

## MVP limitations

Stated plainly, because pretending otherwise would make the network less
useful:

* **No authentication.** Anyone can report anything. The per-reporter evidence
  cap and rate limiter raise the cost of poisoning the statistics; they do not
  make it impossible, and a distributed flood from many IPs would still get
  through.
* **No reputation scoring.** Reporters are counted, not ranked. A reporter that
  has been right a thousand times counts the same as a fresh one.
* **The rate limiter is in-process and not distributed.** One uvicorn worker,
  one budget. It resets on restart.
* **No sophisticated anomaly detection.** Status is a fixed threshold on a
  failure ratio over two fixed windows.
* **Unique-reporter counts in aggregates are lower bounds.** Reporter
  identities are not retained past pruning, so merged buckets keep the maximum
  per-bucket count rather than a true distinct count.
* **No OpenTelemetry ingestion yet**, **no TypeScript SDK yet**, **no
  payments** (`x402` or otherwise). Everything is free.
* **SQLite** is a prototype-stage choice. Retention keeps the file small, but
  a busy network will eventually want PostgreSQL (a URL swap plus `asyncpg`).
* Recovery actions are free-form strings, so `refresh_schema` and
  `refreshSchema` would be counted separately if agents disagree on spelling
  (input is lowercased and space-normalized, which handles the common cases).

## Launch documentation

- [`docs/marketing.md`](docs/marketing.md) — approved messaging, launch posts,
  and the claims that must never be made
- [`docs/launch-plan.md`](docs/launch-plan.md) — distribution sequence,
  experiment metrics and the milestones that decide whether this works
- [`docs/search-console.md`](docs/search-console.md) — indexing checklist for
  Google Search Console and Bing, and what actually moves brand search

## Licence

MIT.
