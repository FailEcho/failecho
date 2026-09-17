"""OTLP traces as a source: the mapping, the allowlist, the caps, both encodings.

Nothing here talks to a gateway. Spans are built the way the OTLP JSON
mapping and the protobuf classes build them, and the test asks what came out
the other side: an observation with the right name and class, or nothing.
"""

from __future__ import annotations

import json

import pytest

from app.core.otlp_map import MAX_SPANS, READ_KEYS, span_to_observation, spans_to_observations

NS = 1_000_000


def attr(key, value):
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    return {"key": key, "value": {"stringValue": value}}


def span(name="GET /repos/{owner}/{repo}", kind="SPAN_KIND_CLIENT", status="STATUS_CODE_ERROR", attrs=(), start=0, end=250 * NS):
    return {"name": name, "kind": kind, "startTimeUnixNano": str(start), "endTimeUnixNano": str(end),
            "status": {"code": status}, "attributes": list(attrs)}


def request(spans, resource=(("service.name", "my-gateway"), ("service.version", "1.4.0"))):
    return {"resourceSpans": [{"resource": {"attributes": [attr(k, v) for k, v in resource]},
                               "scopeSpans": [{"scope": {"name": "test"}, "spans": spans}]}]}


GITHUB_403 = span(attrs=[attr("server.address", "api.github.com"), attr("http.request.method", "GET"),
                         attr("http.route", "/repos/{owner}/{repo}"), attr("http.response.status_code", 403),
                         attr("url.full", "https://api.github.com/repos/acme/secret-repo?token=abc"),
                         attr("http.request.header.authorization", "Bearer ghp_xxx"),
                         attr("gen_ai.prompt", "the user's private prompt")])


# -- the mapping -------------------------------------------------------------


def test_a_client_span_with_an_http_error_is_a_failure_named_like_the_wrapper():
    o = span_to_observation(GITHUB_403, {"service.name": "gw", "service.version": "1.4.0"})
    assert o is not None
    assert o.service == "api.github.com" and o.operation == "GET /repos"
    assert o.outcome == "failure" and o.error_type == "auth_error" and o.error_code == "403"
    assert o.latency_ms == 250 and o.version is None and o.mutates is False   # the gateway's version is not the API's


def test_nothing_outside_the_allowlist_reaches_the_observation():
    o = span_to_observation(GITHUB_403, {})
    flat = json.dumps(o.model_dump())
    for secret in ("secret-repo", "token=abc", "ghp_xxx", "private prompt", "authorization"):
        assert secret not in flat
    assert "url.full" not in READ_KEYS and not any(k.startswith(("http.request.header", "gen_ai", "exception")) for k in READ_KEYS)


def test_an_ok_client_span_is_a_success():
    s = span(status="STATUS_CODE_UNSET", attrs=[attr("server.address", "pypi.org"), attr("http.request.method", "GET"),
                                                  attr("http.route", "/pypi/{name}/json"), attr("http.response.status_code", 200)])
    o = span_to_observation(s, {})
    assert o.outcome == "success" and o.operation == "GET /pypi" and o.error_type is None


@pytest.mark.parametrize("kind", ["SPAN_KIND_SERVER", "SPAN_KIND_INTERNAL", "SPAN_KIND_CONSUMER", "SPAN_KIND_PRODUCER", "SPAN_KIND_UNSPECIFIED"])
def test_only_client_spans_count(kind):
    assert span_to_observation(span(kind=kind, attrs=[attr("server.address", "x.example")]), {}) is None


def test_an_rpc_span_uses_the_rpc_names():
    s = span(name="mcp.tools/call", attrs=[attr("rpc.service", "github-mcp"), attr("rpc.method", "create_issue"),
                                          attr("rpc.grpc.status_code", 14)])
    o = span_to_observation(s, {})
    assert o.service == "github-mcp" and o.operation == "create_issue" and o.outcome == "failure"
    assert o.error_type == "error" and o.error_code == "grpc-14"


def test_error_type_falls_back_to_error_type_attribute_then_to_error():
    s = span(attrs=[attr("server.address", "x.example"), attr("error.type", "timeout")])
    assert span_to_observation(s, {}).error_type == "timeout"
    s = span(attrs=[attr("server.address", "x.example")])
    assert span_to_observation(s, {}).error_type == "error"


def test_the_resource_service_name_is_the_last_resort_and_a_url_is_never_a_service():
    s = span(name="fetch", attrs=[])
    assert span_to_observation(s, {"service.name": "my-agent"}).service == "my-agent"
    assert span_to_observation(span(attrs=[attr("server.address", "https://x.example/y")]), {}) is None
    # a callee but no name and no method: nothing to call the operation, so rejected
    assert span_to_observation(span(attrs=[attr("server.address", "x.example")], name=""), {}) is None


def test_numeric_enums_are_accepted_too():
    s = {"name": "POST /v1/chat", "kind": 3, "status": {"code": 2},
         "attributes": [attr("server.address", "api.groq.com"), attr("http.request.method", "POST"),
                        attr("http.response.status_code", 429)]}
    o = span_to_observation(s, {})
    assert o.operation == "POST /v1" and o.error_type == "rate_limit" and o.mutates is True


def test_a_batch_maps_and_counts_rejects():
    m = spans_to_observations(request([GITHUB_403, span(kind="SPAN_KIND_SERVER"), span(kind="SPAN_KIND_INTERNAL")]))
    assert len(m.observations) == 1 and m.rejected == 2 and m.spans == 3


def test_a_batch_over_the_span_cap_stops():
    m = spans_to_observations(request([GITHUB_403] * (MAX_SPANS + 5)))
    assert m.spans == MAX_SPANS + 1 and len(m.observations) == MAX_SPANS


# -- the route ---------------------------------------------------------------


def post_json(client, payload, **headers):
    return client.post("/v1/otlp/traces", content=json.dumps(payload).encode(),
                       headers={"Content-Type": "application/json", **headers})


def test_json_traces_become_observations_with_the_spec_response(client):
    r = post_json(client, request([GITHUB_403, span(kind="SPAN_KIND_SERVER")]), **{"X-Reporter-ID": "gateway-1"})
    assert r.status_code == 200
    assert r.json()["partialSuccess"]["rejectedSpans"] == 1 and "expected" in r.json()["partialSuccess"]["errorMessage"]
    q = client.post("/v1/query", json={"service": "api.github.com", "operation": "GET /repos",
                                       "error_type": "auth_error", "error_code": "403"}).json()
    assert q["known"] is True and q["observations"]["total"] == 1


def test_a_clean_batch_answers_an_empty_object(client):
    r = post_json(client, request([GITHUB_403]))
    assert r.status_code == 200 and r.json() == {}


def test_protobuf_in_protobuf_out(client):
    from google.protobuf.json_format import ParseDict
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest, ExportTraceServiceResponse)

    msg = ParseDict(request([GITHUB_403, span(kind="SPAN_KIND_INTERNAL")]), ExportTraceServiceRequest())
    r = client.post("/v1/otlp/traces", content=msg.SerializeToString(), headers={"Content-Type": "application/x-protobuf"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-protobuf")
    resp = ExportTraceServiceResponse.FromString(r.content)
    assert resp.partial_success.rejected_spans == 1
    q = client.post("/v1/query", json={"service": "api.github.com", "operation": "GET /repos",
                                       "error_type": "auth_error", "error_code": "403"}).json()
    assert q["observations"]["total"] == 1


def test_garbage_is_a_400_not_a_500(client):
    r = client.post("/v1/otlp/traces", content=b"\x00\x01not otlp", headers={"Content-Type": "application/x-protobuf"})
    assert r.status_code == 400
    r = client.post("/v1/otlp/traces", content=b"[1,2,3]", headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_the_span_cap_is_a_413(client):
    r = post_json(client, request([span(kind="SPAN_KIND_INTERNAL")] * (MAX_SPANS + 1)))
    assert r.status_code == 413


def test_the_route_has_its_own_body_cap_and_the_rest_keep_theirs(client):
    from app.api.otlp import OTLP_MAX_BODY_BYTES
    from app.main import MAX_BODY_BYTES

    big = b"[" + b" " * (MAX_BODY_BYTES + 100) + b"]"
    assert client.post("/v1/observe", content=big, headers={"Content-Type": "application/json"}).status_code == 413
    r = client.post("/v1/otlp/traces", content=big, headers={"Content-Type": "application/json"})
    assert r.status_code == 400, "over the observe cap is fine here; it is just not OTLP"
    huge = b"[" + b" " * (OTLP_MAX_BODY_BYTES + 100) + b"]"
    assert client.post("/v1/otlp/traces", content=huge, headers={"Content-Type": "application/json"}).status_code == 413


def test_the_source_rules_apply_like_everywhere(client, rows):
    post_json(client, request([GITHUB_403]), **{"X-Reporter-Kind": "demo"})
    post_json(client, request([GITHUB_403]))
    assert sorted(r["source"] for r in rows("observations")) == ["agent", "demo_agent"]


def test_the_stored_row_holds_no_span_attribute_but_the_mapped_fields(client, rows):
    post_json(client, request([GITHUB_403]))
    row = rows("observations")[0]
    flat = json.dumps(row)
    for secret in ("secret-repo", "token=abc", "ghp_xxx", "private prompt"):
        assert secret not in flat
    assert row["service"] == "api.github.com" and row["operation"] == "GET /repos" and row["error_code"] == "403"
