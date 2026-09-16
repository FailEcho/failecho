"""Zero-code reporting: patched HTTP clients, and what must not leak."""

from __future__ import annotations

import http.server
import threading

import pytest

import failecho_autoreport.auto as auto
from failecho_autoreport import FailEcho


class Recorder(FailEcho):
    def __init__(self):
        super().__init__(endpoint="http://127.0.0.1:9", reporter_id="t")
        self.bodies = []

    def _post(self, body):
        self.bodies.append(body)


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(auto, "_fe", rec)
    auto.enable()
    return rec


@pytest.fixture(scope="module")
def server():
    """A local HTTP server: /ok -> 200, /missing -> 404, /boom -> 503."""

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            code = {"/ok": 200, "/missing/12345": 404, "/boom": 503}.get(self.path, 200)
            self.send_response(code); self.send_header("Content-Length", "2"); self.end_headers()
            self.wfile.write(b"{}")

        def do_POST(self):
            self.do_GET()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


# -- the route: what leaves the process ------------------------------------


@pytest.mark.parametrize("method,url,expected", [
    ("GET", "https://api.github.com/repos/owner/repo/issues?state=open", ("api.github.com", "GET /repos")),
    ("post", "https://pypi.org/pypi/requests/json", ("pypi.org", "POST /pypi")),
    ("GET", "https://x.example/12345/thing", ("x.example", "GET /_")),
    ("DELETE", "https://x.example/", ("x.example", "DELETE")),
    ("GET", "https://X.Example/A/b", ("x.example", "GET /A")),
])
def test_route_keeps_host_and_first_segment_only(method, url, expected):
    assert auto.route(method, url) == expected


def test_identifiers_query_strings_and_deeper_paths_never_appear(recorder, server):
    import requests
    requests.get(f"{server}/missing/12345?token=SECRET&user=alice")
    recorder.flush(5)
    blob = str(recorder.bodies)
    assert "12345" not in blob and "SECRET" not in blob and "alice" not in blob
    assert recorder.bodies[0]["operation"] == "GET /missing"


# -- the three clients -------------------------------------------------------


def test_requests_is_observed(recorder, server):
    import requests
    assert requests.get(f"{server}/ok").status_code == 200
    assert requests.get(f"{server}/boom").status_code == 503
    recorder.flush(5)
    outcomes = [(b["operation"], b["outcome"], b.get("error_type")) for b in recorder.bodies]
    assert ("GET /ok", "success", None) in outcomes
    assert ("GET /boom", "failure", "server_error") in outcomes


def test_httpx_is_observed(recorder, server):
    import httpx
    assert httpx.get(f"{server}/missing/12345").status_code == 404
    recorder.flush(5)
    assert recorder.bodies[0]["outcome"] == "failure"
    assert recorder.bodies[0]["error_type"] == "not_found"
    assert recorder.bodies[0]["error_code"] == "404"


def test_urllib_is_observed_and_the_exception_still_raises(recorder, server):
    import urllib.error
    import urllib.request
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(f"{server}/boom")
    recorder.flush(5)
    assert recorder.bodies[0]["error_type"] == "server_error"


def test_the_response_is_returned_unchanged(recorder, server):
    import requests
    r = requests.get(f"{server}/ok")
    assert r.status_code == 200 and r.text == "{}"


def test_get_is_a_read_and_post_is_a_write(recorder, server):
    import requests
    requests.get(f"{server}/ok"); requests.post(f"{server}/ok")
    recorder.flush(5)
    by = {b["operation"]: b["mutates"] for b in recorder.bodies}
    assert by == {"GET /ok": False, "POST /ok": True}


# -- it must not eat itself -----------------------------------------------------


def test_reports_to_the_endpoint_are_not_themselves_reported(monkeypatch, server):
    """The wrapper reports over urllib and urllib is patched. Without the
    guard, every report would produce a report, forever."""
    real = FailEcho(endpoint=server, reporter_id="loop")   # a real client, posting for real
    monkeypatch.setattr(auto, "_fe", real)
    auto.enable()
    real.record_failure("x.example", "GET /a", RuntimeError("503"))
    assert real.flush(5)
    assert real.sent == 1
    # the POST to <server>/v1/observe went through patched urllib: it must not
    # have been queued as an observation of <server>
    assert real.queued == 1, f"the report was reported: queued={real.queued}"


def test_enable_is_idempotent(recorder, server):
    """Importing twice must patch once: a doubly wrapped client would report
    every call twice, and the network cannot tell that from two agents."""
    import requests
    auto.enable(); auto.enable()
    assert requests.Session.request.__name__ == "observed"
    requests.get(f"{server}/ok")
    recorder.flush(5)
    assert len(recorder.bodies) == 1, f"one call reported {len(recorder.bodies)} times"


def test_a_self_hosted_endpoint_on_the_same_host_does_not_hide_other_local_calls(monkeypatch, server):
    """The guard that stops the wrapper reporting its own reports used to
    compare hostnames. A FailEcho on 127.0.0.1:8000 then silenced every call
    the agent made to anything else on 127.0.0.1."""
    rec = Recorder()  # endpoint 127.0.0.1:9 -- same host as the test server, different port
    monkeypatch.setattr(auto, "_fe", rec)
    auto.enable()
    import requests
    requests.get(f"{server}/ok")
    rec.flush(5)
    assert len(rec.bodies) == 1
