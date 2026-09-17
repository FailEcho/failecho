"""OTLP spans -> FailEcho observations. A translator, not a second pipeline.

Every agent gateway in the market sees every outbound failure from every
agent behind it, keeps that knowledge private, and speaks OpenTelemetry.
This module turns their traces into the same ``ObserveRequest`` that
``POST /v1/observe`` and the MCP tools produce, so the fingerprint, the
canonicalisation and the source rules are identical to every other write.

What becomes an observation: a **client** span -- an outbound call. Its
status decides the outcome; the OpenTelemetry semantic conventions decide
the service and the operation, generic conventions first so any
instrumented client maps and not only one gateway. Everything else --
server spans, internal spans, consumers, producers, spans with no idea what
they called -- is dropped and counted as rejected, which is the normal case
(a trace has far more spans than failures).

Privacy is an allowlist, not a blocklist. The mapper reads a fixed set of
attribute keys (``READ_KEYS``) and never iterates the rest. A span can carry
anything -- request bodies, headers, prompts, stack traces -- and the only
safe policy toward an open-ended bag of attributes is to never look inside
it. Never read, under any setting: ``url.full``, ``url.query``, request or
response headers or bodies, ``db.statement``, ``exception.stacktrace``,
``gen_ai.prompt``, ``gen_ai.completion``, any ``*.content``, span events,
span links. ``exception.message`` is not read either: OTLP exporters put it
on an event, and events are not read.

Operation naming matches the zero-code wrapper -- ``METHOD /first-segment``
for HTTP, the RPC method for RPC -- so a gateway's evidence joins the
evidence agents report through ``run`` mode instead of landing beside it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.error_types import canonical_error_type
from app.schemas.observe import ObserveRequest

#: The only attribute keys the mapper reads. Adding one here is a privacy
#: decision and needs a fixture that shows what it carries.
READ_KEYS = frozenset({
    # who was called
    "server.address", "net.peer.name", "peer.service", "rpc.service",
    # what was called
    "rpc.method", "http.route", "url.template", "http.request.method", "http.method",
    # how it went
    "http.response.status_code", "http.status_code", "rpc.grpc.status_code", "error.type",
})
RESOURCE_KEYS = frozenset({"service.name"})

MAX_SPANS = 500

_CLIENT_KINDS = {"SPAN_KIND_CLIENT", 3, "3"}
_ERROR_STATUS = {"STATUS_CODE_ERROR", 2, "2"}
_HOST_LIKE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_:@/")


@dataclass
class Mapped:
    observations: list[ObserveRequest]
    rejected: int
    spans: int


def _value(v: dict | str | int | None):
    """One OTLP AnyValue (JSON mapping) -> a Python scalar, or None for
    anything that is not a scalar (arrays, kvlists, bytes are never read)."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if not isinstance(v, dict):
        return None
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in v:
            return v[key]
    return None


def _attrs(items: list | None, allowed: frozenset[str]) -> dict:
    """Only the allowed keys, read once each; nothing else is touched."""
    out: dict = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if key in allowed and key not in out:
            out[key] = _value(item.get("value"))
    return out


def _first_segment(route: str) -> str:
    seg = next((p for p in str(route).split("/") if p), "")
    if seg and (seg.isdigit() or len(seg) > 40 or seg.startswith("{")):
        seg = "_"
    return f"/{seg}" if seg else ""


def _http_name(method, route: str) -> str:
    """``GET /repos`` -- the zero-code wrapper's shape, so the evidence joins."""
    seg = _first_segment(route)
    return f"{str(method).upper()} {seg}" if seg else str(method).upper()


def span_to_observation(span: dict, resource: dict) -> ObserveRequest | None:
    """One span -> one observation, or None when it is not an outbound call
    or cannot be named. Pure; raises nothing for a malformed span."""
    if span.get("kind") not in _CLIENT_KINDS:
        return None
    a = _attrs(span.get("attributes"), READ_KEYS)
    service = (a.get("server.address") or a.get("net.peer.name") or a.get("rpc.service")
               or a.get("peer.service") or resource.get("service.name"))
    if not service or not isinstance(service, str):
        return None
    service = service.strip()[:128]
    if not service or any(ch not in _HOST_LIKE for ch in service) or "://" in service or service.startswith("/"):
        return None

    method = a.get("http.request.method") or a.get("http.method")
    route = a.get("http.route") or a.get("url.template")
    if a.get("rpc.method"):
        operation = str(a["rpc.method"])
    elif method and route:
        operation = _http_name(method, route)
    elif method and isinstance(span.get("name"), str) and span["name"].upper().startswith(str(method).upper() + " "):
        # semconv span names are "METHOD /route" or "METHOD" when no route is known
        operation = _http_name(method, span["name"][len(str(method)):].strip())
    elif method:
        operation = str(method).upper()
    else:
        name = span.get("name")
        if not isinstance(name, str) or not name.strip():
            return None
        operation = name.strip()
    operation = operation[:128]

    status = (span.get("status") or {}).get("code")
    code = a.get("http.response.status_code") or a.get("http.status_code")
    code_str = str(code) if code is not None else None
    failed = status in _ERROR_STATUS or (code is not None and int(code) >= 400 if str(code).isdigit() else False)
    if not failed:
        outcome = "success"
        error_type = None
    else:
        outcome = "failure"
        # the status class first, as everywhere; error.type only when there is
        # no HTTP status to go by; the gRPC code as a code, not a class
        error_type = canonical_error_type(None, code_str) if code_str else None
        if not error_type and a.get("error.type"):
            error_type = canonical_error_type(str(a["error.type"])) or None
        if not error_type and a.get("rpc.grpc.status_code") is not None:
            code_str = f"grpc-{a['rpc.grpc.status_code']}"
        if not error_type:
            error_type = "error"

    latency_ms = None
    try:
        start, end = int(span.get("startTimeUnixNano") or 0), int(span.get("endTimeUnixNano") or 0)
        if end > start:
            latency_ms = min((end - start) // 1_000_000, 10 ** 9)
    except (TypeError, ValueError):
        pass

    # resource service.version is the *reporter's* version (the gateway's, the
    # agent's), not the called API's; putting it in `version` would split one
    # failure across every gateway release. Not mapped.
    fields = {"service": service, "operation": operation, "outcome": outcome, "latency_ms": latency_ms}
    if outcome == "failure":
        fields.update(error_type=error_type, error_code=code_str[:32] if code_str else None)
    if method:
        fields["mutates"] = str(method).upper() not in ("GET", "HEAD", "OPTIONS")
    try:
        return ObserveRequest(**{k: v for k, v in fields.items() if v is not None})
    except Exception:  # noqa: BLE001 - a span that does not validate is a rejected span
        return None


def spans_to_observations(payload: dict) -> Mapped:
    """An ExportTraceServiceRequest (JSON mapping) -> observations + rejects.
    Stops counting at MAX_SPANS; the route refuses larger batches outright."""
    observations: list[ObserveRequest] = []
    rejected = 0
    seen = 0
    for rs in payload.get("resourceSpans") or payload.get("resource_spans") or []:
        if not isinstance(rs, dict):
            continue
        resource = _attrs(((rs.get("resource") or {}).get("attributes")), RESOURCE_KEYS)
        for ss in rs.get("scopeSpans") or rs.get("scope_spans") or []:
            if not isinstance(ss, dict):
                continue
            for span in ss.get("spans") or []:
                seen += 1
                if seen > MAX_SPANS:
                    return Mapped(observations, rejected + 1, seen)
                obs = span_to_observation(span, resource) if isinstance(span, dict) else None
                if obs is None:
                    rejected += 1
                else:
                    observations.append(obs)
    return Mapped(observations, rejected, seen)


def count_spans(payload: dict) -> int:
    n = 0
    for rs in payload.get("resourceSpans") or payload.get("resource_spans") or []:
        for ss in (rs.get("scopeSpans") or rs.get("scope_spans") or []) if isinstance(rs, dict) else []:
            n += len(ss.get("spans") or []) if isinstance(ss, dict) else 0
    return n
