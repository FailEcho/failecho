"""POST /v1/otlp/traces -- OpenTelemetry traces as a FailEcho source.

An agent gateway, an OTel Collector, or an SDK exporter points its
``otlphttp`` traces endpoint here and every outbound failure it already
records becomes an observation, through the same ``record_observation`` as
every other write. Both OTLP/HTTP encodings are accepted, because
collectors send protobuf by default and forcing JSON would mean every
operator changing their exporter.

The response is the spec's ``ExportTraceServiceResponse``: 200, with
``partial_success.rejected_spans`` set to the spans that did not map to an
observation. Most will not -- server spans, internal spans, spans with no
callee -- and that is expected, not an error. Only traces; metrics and logs
carry no failure shape worth taking.

Bounds, because one request can carry hundreds of spans: its own body cap
(``OTLP_MAX_BODY_BYTES``, enforced before the body is read), a hard cap on
spans per request, and the same per-IP write limiter as ``/v1/observe`` --
one request is one write. The per-reporter evidence cap is unchanged, so a
gateway fronting a thousand agents still weighs five attempts an hour.

What it does not give: recovery outcomes. A span says a call failed and,
later, that one succeeded; it does not say what anyone decided in between.
Failure rates and the skip verdict, yes; what fixed it still comes from a
human, the wrapper's ``recovered()``, or the MCP tool. The setup page says
so.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import RateLimitDep, ReporterDep, SessionDep, SourceDep
from app.core.otlp_map import MAX_SPANS, count_spans, spans_to_observations
from app.core.service import record_observation

router = APIRouter(tags=["network"])

#: Larger than the global cap because a batch is many spans, still bounded.
#: A batch over it is refused whole, never truncated. Caddy carries the same
#: number for chunked bodies (deploy/Caddyfile).
OTLP_MAX_BODY_BYTES = 256 * 1024

PROTOBUF = "application/x-protobuf"


def _decode(body: bytes, content_type: str) -> dict:
    if content_type.startswith(PROTOBUF):
        from google.protobuf.json_format import MessageToDict
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

        message = ExportTraceServiceRequest()
        message.ParseFromString(body)
        return MessageToDict(message, preserving_proto_field_name=False)
    return json.loads(body.decode("utf-8"))


def _respond(accepts_protobuf: bool, rejected: int, message: str | None, http_status: int = 200) -> Response:
    if accepts_protobuf:
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse

        resp = ExportTraceServiceResponse()
        if rejected or message:
            resp.partial_success.rejected_spans = rejected
            resp.partial_success.error_message = message or ""
        return Response(content=resp.SerializeToString(), media_type=PROTOBUF, status_code=http_status)
    body: dict = {}
    if rejected or message:
        body["partialSuccess"] = {"rejectedSpans": rejected, "errorMessage": message or ""}
    return JSONResponse(content=body, status_code=http_status)


@router.post(
    "/otlp/traces",
    status_code=status.HTTP_200_OK,
    dependencies=[RateLimitDep],
    summary="Ingest OpenTelemetry traces as observations",
    description=(
        "OTLP/HTTP traces, protobuf (`application/x-protobuf`) or JSON. Client "
        "spans become observations: `server.address` (or `rpc.service`, "
        "`peer.service`, the resource `service.name`) is the service; "
        "`rpc.method`, or the HTTP method plus the first segment of `http.route`, "
        "is the operation; the span status and `http.response.status_code` "
        "decide the outcome and the error class. Every other span is rejected, "
        "which is normal, and counted in `partialSuccess.rejectedSpans`.\n\n"
        "**Allowlist, not blocklist:** only a fixed set of attribute keys is "
        "read. `url.full`, headers, bodies, `db.statement`, `exception.*`, "
        "`gen_ai.*`, events and links are never read, under any setting. Strip "
        "them at your collector anyway; that keeps control on your side.\n\n"
        "At most 500 spans and 256KB per request; rate limited per client IP "
        "like every write. Spans do not carry recovery outcomes -- what fixed "
        "a failure still needs `POST /v1/outcome` or the MCP tool."
    ),
)
async def ingest_traces(
    request: Request,
    session: SessionDep,
    reporter: ReporterDep,
    source: SourceDep,
) -> Response:
    content_type = (request.headers.get("content-type") or "").lower()
    accepts_protobuf = content_type.startswith(PROTOBUF)
    body = await request.body()
    if len(body) > OTLP_MAX_BODY_BYTES:
        return _respond(accepts_protobuf, 0, f"batch exceeds {OTLP_MAX_BODY_BYTES} bytes", 413)
    try:
        payload = _decode(body, content_type)
    except Exception:  # noqa: BLE001 - a body that is not OTLP is a 400, whatever went wrong parsing it
        return _respond(accepts_protobuf, 0, "body is not an OTLP ExportTraceServiceRequest", 400)
    if not isinstance(payload, dict):
        return _respond(accepts_protobuf, 0, "body is not an OTLP ExportTraceServiceRequest", 400)
    if count_spans(payload) > MAX_SPANS:
        return _respond(accepts_protobuf, 0, f"more than {MAX_SPANS} spans in one request", 413)

    mapped = spans_to_observations(payload)
    for observation in mapped.observations:
        await record_observation(session, observation, reporter_hash=reporter, source=source)
    message = None
    if mapped.rejected:
        message = (f"{mapped.rejected} of {mapped.spans} spans were not outbound calls with a callee and were "
                   "not stored; this is expected for most traces")
    return _respond(accepts_protobuf, mapped.rejected, message)
