"""The deployment files carry privacy promises too.

The site says it never stores headers or credentials. The application holds to
that; the reverse proxy in front of it did not. Caddy's default access log
records every request header, so X-FailEcho-Operator, Authorization and the
raw X-Reporter-ID the application is careful to hash were all written to disk
in the clear. These tests are here so that cannot come back quietly.
"""

from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
CADDYFILE = (DEPLOY / "Caddyfile").read_text()


def test_the_access_log_drops_request_headers():
    assert "format filter" in CADDYFILE, "the access log has no field filter"
    assert "request>headers delete" in CADDYFILE
    assert "resp_headers delete" in CADDYFILE


def test_the_access_log_still_records_what_a_log_is_for():
    """Deleting the whole request object would be a different bug: without
    method, URI and status there is nothing to investigate an outage with."""
    for field in ("request>uri delete", "request>method delete", "status delete"):
        assert field not in CADDYFILE, f"the log deletes {field}"


def test_the_dev_proxy_never_logs_headers_either():
    """A development host is still a host with real tokens pointed at it."""
    dev = (DEPLOY / "Caddyfile.failecho-dev").read_text()
    if "log {" in dev:
        assert "request>headers delete" in dev
