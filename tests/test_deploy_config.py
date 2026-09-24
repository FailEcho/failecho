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
    # the one other origin a page may talk to is the lab, and only to read
    connect = policy[policy.index("connect-src"):]
    connect = connect[:connect.index(";")]
    assert connect == "connect-src 'self' https://lab.failecho.com"


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


def test_the_autoreport_package_builds_from_the_repo():
    """`failecho-autoreport` is published as its own zero-dependency
    distribution, assembled from the canonical source at build time. The
    manifest the script writes must declare no dependencies -- the whole point
    is that a LangChain user does not inherit ours."""
    from pathlib import Path
    import re

    script = Path("scripts/build_autoreport_package.py").read_text()
    assert 'name = "failecho-autoreport"' in script
    assert "dependencies = []" in script
    assert 'packages = ["failecho_autoreport"]' in script
    # version comes from the source, never typed twice
    src = Path("failecho_autoreport/__init__.py").read_text()
    assert re.search(r'^__version__ = "\d+\.\d+\.\d+"', src, re.M)
    assert "__version__" in script


def test_deploy_script_refuses_a_dirty_checkout_and_checks_the_site():
    """Nineteen hand deploys in a day, each the same four commands. The script
    must refuse to reset a tree with local edits -- that is how a fix is lost
    -- and must check the live pages afterwards rather than trusting the
    restart."""
    from pathlib import Path

    script = Path("deploy/deploy.sh").read_text()
    assert "set -euo pipefail" in script
    assert "local modifications" in script and "status --porcelain" in script
    assert "reset --hard" in script and "fetch -q origin main" in script
    assert "systemctl restart" in script
    assert "real_observations_total" in script, "the honesty counter is part of the check"
    assert "/llms.txt" in script and "/setup" in script
    # writes go through the failecho user, never root
    assert "sudo -u failecho git" in script


def test_agent_unit_is_first_party_and_boxed():
    """The agent must read the same token file the server does, so the two
    cannot disagree; must be memory-capped below the server; and must run as
    the unprivileged user."""
    from pathlib import Path

    unit = Path("deploy/failecho-agent.service").read_text()
    assert "EnvironmentFile=/etc/failecho.env" in unit, "token must come from the server's own file"
    assert "EnvironmentFile=/etc/failecho-agent.env" in unit
    assert "User=failecho" in unit and "Type=oneshot" in unit
    assert "MemoryMax=150M" in unit
    assert "FAILECHO_REPORTER_ID=operator-agent" in unit
    assert "ProtectSystem=strict" in unit and "NoNewPrivileges=yes" in unit
    timer = Path("deploy/failecho-agent.timer").read_text()
    assert "OnCalendar=*:00/30" in timer


def test_the_otlp_route_has_its_own_body_limit_at_the_edge():
    """The application caps OTLP batches at 256KB; a chunked body has no
    length to check there, so the edge carries a matching per-path limit
    while everything else keeps the 64KB backstop."""
    assert "@otlp path /v1/otlp/*" in CADDYFILE
    block = CADDYFILE[CADDYFILE.index("request_body @otlp"):]
    assert "max_size 320KB" in block[: block.index("}")]
    assert "max_size 64KB" in CADDYFILE


def test_the_lab_is_pruned_like_production():
    """The lab was set to keep 96 hours of raw rows and nothing ran the pruner
    against it, so its queries slowed down every day and every ask-side time
    in the lab paid for a network slower than the one users get."""
    service = (DEPLOY / "failecho-lab-prune.service").read_text()
    timer = (DEPLOY / "failecho-lab-prune.timer").read_text()
    assert "EnvironmentFile=/etc/failecho-lab.env" in service, "the lab's own database and retention"
    assert "/srv/failecho-lab/app/scripts/prune.py" in service
    assert "ReadWritePaths=/srv/failecho-lab/data" in service
    assert "/srv/failecho/data" not in service, "the lab pruner must never touch production's database"
    assert "OnCalendar=*:35" in timer


def test_both_pruners_have_a_writable_tmp():
    """ProtectSystem=strict makes /tmp read-only; a large GROUP BY needs a temp
    file. The lab's first prune failed with "disk I/O error", and production's
    identical unit would have failed the same way as soon as it grew."""
    for name in ("failecho-prune.service", "failecho-lab-prune.service"):
        unit = (DEPLOY / name).read_text()
        assert "ProtectSystem=strict" in unit and "PrivateTmp=true" in unit, name


def test_the_mirror_reports_to_production_only_labelled_and_reads_the_lab_only():
    """The lab-to-production mirror (24 Sep). Unlabelled, its writes would be
    counted as independent adoption; pointed the wrong way, it would feed the
    lab its own traffic."""
    service = (DEPLOY / "failecho-mirror.service").read_text()
    timer = (DEPLOY / "failecho-mirror.timer").read_text()
    assert "EnvironmentFile=/etc/failecho.env" in service, "the operator token lives there"
    assert "FAILECHO_MIRROR_ENDPOINT=http://127.0.0.1:8000" in service, "production, on this host"
    assert "FAILECHO_MIRROR_LAB_DB=/srv/failecho-lab/data/lab.db" in service
    assert "/etc/failecho-lab.env" not in service
    assert "ProtectSystem=strict" in service and "PrivateTmp=true" in service and "NoNewPrivileges=true" in service
    assert "User=failecho" in service
    assert "OnCalendar=*:02/10" in timer
