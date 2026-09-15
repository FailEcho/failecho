# OTLP receiver: gateways as FailEcho sources

Written 2026-09-15. Not built yet.

## Why a receiver and not a proxy

The agent egress proxy is a crowded market: agentgateway (Linux Foundation),
Lasso MCP Gateway, Docker MCP Gateway, IBM ContextForge, Bifrost, and enough
others that there are "12 best" listicles. Every one of them sees every
outbound failure from every agent behind it, and every one of them keeps that
knowledge private to a single tenant. None of them shares what fixed it.

That gap is what FailEcho already is. So the move is not to build a thirteenth
proxy; it is to accept the telemetry the existing twelve already emit. Most of
them speak OpenTelemetry. A FailEcho OTLP receiver turns every gateway that
emits OTel into a FailEcho source with zero code on their side -- the operator
adds one exporter URL to configuration they already have.

## Stack

Nothing new at the infrastructure layer. Same FastAPI app, same SQLite, same
single uvicorn worker under its 400MB cap, same Caddy and Cloudflare in front.

One new dependency: `opentelemetry-proto` (pure Python, pulls `protobuf`).
OTLP/HTTP has two encodings and collectors send protobuf by default, so
accepting only JSON would force every operator to change their exporter
config. Accept both:

- `application/x-protobuf` -- `ExportTraceServiceRequest.FromString()`
- `application/json` -- the OTLP JSON mapping, parsed with `MessageToDict`
  or by hand for the handful of fields used

Everything downstream of parsing is existing code: `record_observation()` in
`app/core/service.py`, which REST and MCP already share, so the fingerprint,
the canonicalisation, the normalizer and the source rules are identical to
every other write path. The receiver is a translator, not a second pipeline.

## Endpoint

    POST /v1/otlp/traces
    Content-Type: application/x-protobuf | application/json
    X-Reporter-ID: <optional, hashed before storage, as everywhere>

Response follows the OTLP spec: `200` with an `ExportTraceServiceResponse`,
`partial_success.rejected_spans` set to the count that did not map to an
observation (most spans will not -- a trace has far more spans than failures,
and that is expected rather than an error).

Only traces. Metrics and logs have no failure shape in them worth taking.

## Mapping: span -> observation

A span becomes an observation only when it is an outbound call. Everything
else is dropped and counted as rejected. The mapping follows OTel semantic
conventions, generic first, so it works for any instrumented client and not
only for one gateway:

| observation field | from span | notes |
|---|---|---|
| `outcome` | `status.code` | `STATUS_CODE_ERROR` -> failure, `OK`/`UNSET` on a client span -> success |
| `service` | `server.address`, else `rpc.service`, else `peer.service`, else resource `service.name` | canonicalised by the existing `canonical_service()` |
| `operation` | `rpc.method`, else `http.route`/`url.template`, else span name | never `url.full` -- it carries query strings |
| `error_type` | derived: `http.response.status_code` class, `rpc.grpc.status_code`, `error.type`, `exception.type` | through the same `classify()` table the hook and the wrapper use |
| `error_code` | `http.response.status_code` when present | |
| `error_message` | **not taken by default** | `exception.message` only when the operator sets `FIN_OTLP_ACCEPT_EXCEPTION_MESSAGE=1`, and then it hits the normalizer like every other message |
| `latency_ms` | `end_time_unix_nano - start_time_unix_nano` | |
| `version` | `service.version` resource attribute | |

Spans that are not client-side calls -- `span.kind` of `SERVER`, `INTERNAL`,
`CONSUMER` -- are rejected. A gateway's own request handling is not shared
infrastructure anybody else calls.

MCP-specific attributes: the GenAI/MCP semantic conventions are still moving
and each gateway names things slightly differently. The mapping keeps a small
per-gateway table (`app/core/otlp_map.py`) filled in **only from real spans
captured from that gateway**, never from its documentation. First target is
agentgateway, because it emits OTel natively and is the one with a community
worth asking.

## Privacy: allowlist, not blocklist

The receiver reads a fixed set of attribute keys and ignores everything else.
It does not iterate attributes looking for things to redact. This is the
inverse of the normalizer's approach, and it is the right one here because a
span can carry anything -- request bodies, headers, prompts -- and the only
safe policy toward an open-ended bag of attributes is to never look inside
it.

Never read, under any setting: `url.full`, `url.query`, `http.request.header.*`,
`http.response.header.*`, `http.request.body.*`, `db.statement`,
`exception.stacktrace`, `gen_ai.prompt`, `gen_ai.completion`, any
`*.content`, any event bodies, any span links.

The operator can also strip at their edge before anything reaches FailEcho,
and the docs recommend it: an OTel Collector `attributes` processor that
deletes payload-bearing keys, and a `filter` processor that sends only spans
with error status. That is the trust-preserving default -- they keep control
of what leaves, and FailEcho's allowlist is the second line rather than the
only one.

## Limits and abuse

This opens a bigger write surface than `/v1/observe`, because one request can
carry hundreds of spans. Bounds, all reusing what exists:

- **Body size.** The global 16KB cap stays for everything else; this route gets
  its own, 256KB, still bounded. Caddy's `request_body max_size` needs a
  matching per-path raise. A batch over the cap is refused whole with 413, not
  truncated.
- **Spans per request.** Hard cap, 500. Above it, 413.
- **Rate.** The existing `write_limiter` in `app/core/ratelimit.py` keyed by
  client IP, exactly as `/v1/observe` is. One OTLP request counts as one write
  regardless of span count, so the per-span cap above is what bounds work.
- **Reporter weight.** Unchanged: `max_reporter_weight_per_hour = 5`, so a
  gateway fronting a thousand agents still contributes at most five attempts
  per hour to any recovery action's confidence. This is the property that
  stops one large operator buying a recommendation, and it already exists.
- **Source.** Stored as `source: agent` like any other unoperated write, so it
  counts as independent adoption -- correctly, since it is.

## What it does not give you

Recovery outcomes. A span records that a call failed and, later, that a call
succeeded. It does not record that anyone decided to refresh a schema in
between. So the receiver produces failure rates and the "what happened next"
heuristic, the same limit the log scanner has, and explicit outcomes still
come from a human, the wrapper's `recovered()`, or the MCP tool. Say so on
the setup page; do not let a gateway operator believe they are contributing
the half that they are not.

## Build steps

1. `app/api/otlp.py` -- route, content-type dispatch, per-route body and span
   caps, limiter check, OTLP response shape.
2. `app/core/otlp_map.py` -- pure function `spans_to_observations()`, the
   allowlist, the generic semconv mapping, the empty per-gateway table.
   Unit-tested against JSON fixtures; no network.
3. Middleware change in `app/main.py` -- the body cap becomes per-route.
   `deploy/Caddyfile` gets a matching `@otlp path /v1/otlp/*` block with
   `max_size 256KB`.
4. Fixtures from real spans. Generate them with the Python OTel SDK first
   (`opentelemetry-sdk` + `opentelemetry-exporter-otlp-proto-http`, in a
   throwaway venv, pointed at a local server), then capture from a real
   agentgateway once one is running somewhere. Every mapping row that lands in
   `otlp_map.py` must have a fixture that exercises it.
5. Setup page: three snippets, each run before being written down --
   - OTel Collector pipeline with `filter` (errors only) and `attributes`
     (strip payload keys) processors, exporting `otlphttp` to FailEcho
   - agentgateway's tracing config pointed at the same URL
   - the Python SDK, for people instrumenting their own client
6. llms.txt: one paragraph, in the same descriptive register as the rest.

## Verification, in order

1. Unit: fixtures through `spans_to_observations()`; the allowlist test
   asserts that a span carrying `url.full`, headers and a prompt produces an
   observation containing none of them.
2. Local end to end: Python SDK exporter -> local FailEcho on a throwaway
   port -> `/v1/query` shows the failure with the right `error_type`.
3. Collector end to end: an OTel Collector binary with the recommended
   pipeline, if the box's memory allows one (~100MB); otherwise on the laptop.
4. A real gateway: agentgateway with tracing on, one failing tool call,
   observed at FailEcho. This is the one that fills the per-gateway table and
   the only one that proves the integration rather than the protocol.

Production is never a test target. Same rule as everywhere else.

## Effort

Steps 1-3 and the unit tests: a day. The SDK end-to-end: the same day. The
Collector and agentgateway runs: another day, mostly waiting on installs and
reading real spans. Documentation: half a day, because every snippet gets run.

## Order relative to everything else

The log scanner first. It needs nobody, seeds the network from history that
already exists, and has no dependency on any gateway's attribute names. The
receiver second: it opens every gateway as a live source, and by then the
scanner's `--share` path has exercised the same write function under load.
The proxy itself: never. Twelve people built it already.
