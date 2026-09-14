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


def test_the_edge_sets_the_headers_a_browser_will_not_assume():
    """There were none of these. Each one is a default the browser applies if
    it is told to and does not if it is not."""
    for header in ("Strict-Transport-Security", "Content-Security-Policy",
                   "X-Content-Type-Options", "X-Frame-Options",
                   "Referrer-Policy", "Permissions-Policy",
                   "Cross-Origin-Opener-Policy"):
        assert header in CADDYFILE, f"{header} is not set"
    assert "-Server" in CADDYFILE, "Caddy still announces itself"
    assert "max-age=31536000" in CADDYFILE
    # Not preloaded: that is a one-way door and belongs to a decision, not to
    # a config tidy-up. (Checked on the directive, not the comment above it.)
    hsts = CADDYFILE[CADDYFILE.index("Strict-Transport-Security"):]
    assert "preload" not in hsts[:hsts.index("\n")]


def test_the_policy_allows_no_third_party_code():
    """script-src 'self' is only true while the site serves its own scripts.
    The API reference used to pull Swagger UI from a CDN, which meant a
    third-party bundle executing with this origin's privileges."""
    policy = CADDYFILE[CADDYFILE.index("Content-Security-Policy"):]
    policy = policy[:policy.index("\n")]
    assert "script-src 'self'" in policy
    assert "cdn." not in policy and "unsafe-eval" not in policy
    assert "frame-ancestors 'none'" in policy
    assert "object-src 'none'" in policy and "base-uri 'none'" in policy


def test_the_proxy_caps_a_body_the_app_cannot_measure():
    """Content-Length covers the ordinary case and the app checks it. A
    chunked body has no length to check, so the limit is here too, where the
    connection can be cut before the bytes reach a worker."""
    assert "request_body {" in CADDYFILE
    assert "max_size 64KB" in CADDYFILE


def test_the_service_keeps_its_database_to_itself():
    """The database and the backups were mode 0644 in 0755 directories.
    Nothing in the web configuration exposes them and this box has no other
    human accounts, but "no other account can read it" is cheaper to guarantee
    once than to keep checking."""
    unit = (DEPLOY / "failecho.service").read_text()
    assert "UMask=0077" in unit


def test_uvicorn_does_not_log_the_uri_a_second_time():
    """Caddy already logs every request with its headers filtered out.
    Uvicorn's copy goes to journald, where nothing filters anything, and a
    query string is caller-supplied."""
    unit = (DEPLOY / "failecho.service").read_text()
    assert "--no-access-log" in unit
