"""The bundled Python client, driven against the FastAPI TestClient."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))

from failure_network import UNKNOWN, Client, FailureNetworkError  # noqa: E402


class _Bridge:
    """Route the client's urllib calls into the in-process TestClient."""

    def __init__(self, test_client):
        self.test_client = test_client

    def __call__(self, request, timeout=None):
        response = self.test_client.post(
            request.full_url,
            content=request.data,
            headers=dict(request.header_items()),
        )

        class _Response:
            def read(self_inner):
                return response.content

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

        return _Response()


def test_client_round_trip(client):
    api = Client("http://testserver", reporter_id="agent-alpha")
    with patch("urllib.request.urlopen", _Bridge(client)):
        ack = api.observe_failure(
            service="github-mcp",
            operation="create_issue",
            version="2.8.1",
            schema_hash="abc",
            error_type="validation_error",
            error_code="422",
            error_message="Repository 91827 not found",
        )
        assert ack["accepted"] is True

        api.observe_success(
            service="github-mcp", operation="create_issue", version="2.8.1",
            schema_hash="abc", latency_ms=318,
        )

        intel = api.query(
            service="github-mcp",
            operation="create_issue",
            version="2.8.1",
            schema_hash="abc",
            error_type="validation_error",
            error_code="422",
            error_message="Repository 12345 not found",
        )
        assert intel["known"] is True
        assert intel["fingerprint"] == ack["fingerprint"]

        assert api.report_recovery(
            fingerprint=intel["fingerprint"], action="refresh_schema", successful=True
        ) == {"accepted": True}


def test_client_is_fail_soft_by_default():
    """Telemetry must never break the agent it observes."""
    api = Client("http://127.0.0.1:9", timeout=0.2)
    assert api.observe_failure(service="x", operation="y", error_type="z") is None
    assert api.query(service="x", operation="y") == UNKNOWN


def test_client_can_raise_when_asked():
    api = Client("http://127.0.0.1:9", timeout=0.2, fail_soft=False)
    try:
        api.query(service="x", operation="y")
    except FailureNetworkError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected FailureNetworkError")


def test_client_omits_none_fields(client):
    """Null fields are dropped, so the wire payload stays minimal."""
    sent = {}

    class _Capture(_Bridge):
        def __call__(self, request, timeout=None):
            sent.update(json.loads(request.data))
            return super().__call__(request, timeout)

    api = Client("http://testserver")
    with patch("urllib.request.urlopen", _Capture(client)):
        api.observe_success(service="x", operation="y")
    assert sent == {"service": "x", "operation": "y", "outcome": "success"}
