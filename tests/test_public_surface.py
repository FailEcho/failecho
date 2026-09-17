"""The public surface a launch depends on: stats split, incidents, discovery."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import settings
from tests.conftest import insert_observation, mcp_call, observe

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# real vs synthetic
# ---------------------------------------------------------------------------


def established(reporter: str) -> None:
    """An `agent` reporter past the adoption threshold: five observations,
    two services, spread over twenty minutes (tests/test_adoption_threshold.py).
    Stored under the hash the API would give `reporter`, so later API reports
    with X-Reporter-ID: reporter belong to the same, established, reporter."""
    from app.core.privacy import hash_reporter_id

    for i, service in enumerate(("api.github.com", "api.github.com", "pypi.org", "pypi.org", "api.github.com")):
        insert_observation(service=service, operation="op", reporter_hash=hash_reporter_id(reporter),
                           minutes_ago=60 - i * 5, fingerprint=None)


def test_real_and_synthetic_counts_never_mix(client):
    established("real-agent")
    for _ in range(7):
        insert_observation(source="synthetic", reporter_hash="demo-1")

    stats = client.get("/v1/stats").json()
    assert stats["observations_total"] == 12
    assert stats["real_observations_total"] == 5
    assert stats["real_observations_24h"] == 5
    assert stats["real_reporters_24h"] == 1
    assert stats["synthetic_observations"] == 7
    assert stats["demo_data"] is True
    # The headline real number must never include demo rows.
    assert stats["real_observations_total"] + stats["synthetic_observations"] == 12


def test_no_demo_data_means_no_banner(client):
    observe(client)
    stats = client.get("/v1/stats").json()
    assert stats["demo_data"] is False
    assert stats["synthetic_observations"] == 0
    # one anonymous report is real, kept, and not yet adoption
    assert stats["real_observations_total"] == 0 and stats["sparse_observations"] == 1


def test_real_fingerprint_count_is_real_only(client):
    established("real-agent")
    observe(client, headers={"X-Reporter-ID": "real-agent"})
    observe(client, error_message="Permission denied", error_code="403", headers={"X-Reporter-ID": "real-agent"})
    insert_observation(source="synthetic", fingerprint="f" * 32)

    stats = client.get("/v1/stats").json()
    assert stats["real_failure_fingerprints"] == 2
    assert stats["fingerprints_total"] == 2


# ---------------------------------------------------------------------------
# active incidents
# ---------------------------------------------------------------------------


def test_active_incident_table_has_5m_volume(client):
    for _ in range(12):
        observe(client)
    row = client.get("/v1/services").json()[0]
    assert row["status"] == "MAJOR"
    assert row["observations_5m"] == 12
    assert row["observations_1h"] == 12
    assert row["failure_rate_5m"] == 1.0


# ---------------------------------------------------------------------------
# recovery intelligence panel
# ---------------------------------------------------------------------------


def test_recovery_intelligence_lists_evidenced_actions(client):
    fingerprint = observe(client)["fingerprint"]
    for index in range(6):
        client.post(
            "/v1/outcome",
            json={
                "fingerprint": fingerprint,
                "action": "refresh_schema",
                "successful": True,
            },
            headers={"X-Reporter-ID": f"agent-{index}"},
        )

    entries = client.get("/v1/recovery-intelligence").json()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["service"] == "github"
    assert entry["operation"] == "create_issue"
    assert entry["action"] == "refresh_schema"
    assert entry["attempts"] == 6
    assert entry["success_rate"] == 1.0
    assert 0 < entry["confidence"] < 1
    assert entry["unique_reporters"] == 6
    assert entry["demo_data"] is False


def test_recovery_intelligence_hides_thin_evidence(client):
    fingerprint = observe(client)["fingerprint"]
    for _ in range(3):
        client.post(
            "/v1/outcome",
            json={"fingerprint": fingerprint, "action": "reconnect", "successful": True},
        )
    assert client.get("/v1/recovery-intelligence").json() == []


def test_recovery_intelligence_flags_demo_entries(client):
    """Synthetic evidence must be labelled wherever it is displayed."""
    result = subprocess.run(
        [sys.executable, "scripts/seed_demo.py", "--reset"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr

    entries = client.get("/v1/recovery-intelligence").json()
    assert entries, "seeded demo data should produce recommendations"
    assert all(entry["demo_data"] is True for entry in entries)
    assert entries[0]["action"] == "refresh_schema"

    hidden = client.get(
        "/v1/recovery-intelligence", params={"include_demo": False}
    ).json()
    assert hidden == []


# ---------------------------------------------------------------------------
# machine discovery
# ---------------------------------------------------------------------------


def test_llms_txt_tells_an_agent_when_to_use_the_service(client):
    response = client.get("/llms.txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text

    assert "when a tool, API or MCP operation fails, an agent can ask" in _flat(body)
    assert "Canonical site" in body
    for endpoint in ("/mcp", "/v1/query", "/v1/observe", "/v1/outcome", "/openapi.json"):
        assert endpoint in body
    for tool in (
        "check_tool_failure",
        "report_tool_failure",
        "report_tool_success",
        "report_recovery_outcome",
    ):
        assert tool in body
    assert "Do not send prompts" in body


@pytest.mark.parametrize(
    "schema_name,forbidden",
    [("ObserveRequest", "secrets"), ("QueryRequest", "never stored")],
)
def test_openapi_explains_the_privacy_contract(client, schema_name, forbidden):
    schema = client.get("/openapi.json").json()
    described = schema["components"]["schemas"][schema_name]
    text = (described.get("description") or "") + "".join(
        prop.get("description", "") for prop in described["properties"].values()
    )
    assert forbidden in text


def test_openapi_documents_every_public_endpoint(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths) >= {
        "/v1/observe",
        "/v1/query",
        "/v1/outcome",
        "/v1/services",
        "/v1/stats",
        "/v1/recovery-intelligence",
        "/health",
    }
    for path, methods in paths.items():
        for method, spec in methods.items():
            assert spec.get("description"), f"{method} {path} has no description"


def test_the_dashboard_shows_real_and_synthetic_separately(client):
    """The split moved to /network with the dashboard; it still has to hold."""
    body = client.get("/network").text
    assert "Live network" in body
    assert "Demo data" in body
    assert "Demo-agent observations" in body
    assert "Synthetic observations" in body
    assert "Active incidents" in body
    assert "Recovery echoes" in body
    assert client.get("/static/app.js").status_code == 200


def test_mcp_endpoint_is_advertised_where_agents_look(client):
    assert "/mcp" in client.get("/llms.txt").text
    assert "/mcp" in client.get("/setup").text
    assert "MCP" in client.get("/openapi.json").json()["info"]["description"]
    # ...and it actually answers.
    assert "known" in mcp_call(
        client,
        "check_tool_failure",
        {"service": "x", "operation": "y", "error_type": "z"},
    )


# ---------------------------------------------------------------------------
# demo labelling and FIN_DEMO_MODE
# ---------------------------------------------------------------------------


def test_reporter_kind_header_labels_demo_traffic(client, rows):
    client.post(
        "/v1/observe",
        json={
            "service": "demo-issues-api",
            "operation": "create_issue",
            "outcome": "failure",
            "error_type": "validation_error",
        },
        headers={"X-Reporter-Kind": "demo", "X-Reporter-ID": "demo-agent-a"},
    )
    observe(client)  # unlabelled: real telemetry

    sources = sorted(row["source"] for row in rows("observations"))
    assert sources == ["agent", "demo_agent"]

    stats = client.get("/v1/stats").json()
    assert stats["demo_agent_observations"] == 1
    assert stats["real_observations_total"] == 0 and stats["sparse_observations"] == 1
    assert stats["observations_total"] == 2
    assert stats["demo_data"] is True


def test_self_labelling_can_only_downgrade(client, rows):
    """Nothing a caller sends can promote a row to real telemetry."""
    for kind in ("real", "production", "agent", "definitely-real"):
        client.post(
            "/v1/observe",
            json={
                "service": "x",
                "operation": "y",
                "outcome": "success",
            },
            headers={"X-Reporter-Kind": kind},
        )
    assert {row["source"] for row in rows("observations")} == {"agent"}


def test_demo_mode_flag_is_exposed_and_off_by_default(client):
    stats = client.get("/v1/stats").json()
    assert stats["demo_mode"] is False

    object.__setattr__(settings, "demo_mode", True)
    try:
        assert client.get("/v1/stats").json()["demo_mode"] is True
    finally:
        object.__setattr__(settings, "demo_mode", False)


def test_demo_mode_never_generates_traffic(client):
    """Turning the flag on must label, not fabricate."""
    object.__setattr__(settings, "demo_mode", True)
    try:
        stats = client.get("/v1/stats").json()
        assert stats["demo_mode"] is True
        assert stats["observations_total"] == 0
        assert stats["real_observations_total"] == 0
        assert client.get("/v1/services").json() == []
    finally:
        object.__setattr__(settings, "demo_mode", False)


def test_demo_mode_is_announced_where_the_data_is(client):
    body = client.get("/").text
    assert 'id="demo-mode-badge"' in body
    assert 'id="demo-mode-banner"' in body
    assert "DEMO MODE" in body

    dashboard = client.get("/network").text
    assert "never counted as real adoption" in dashboard
    assert "stat-demo-agent" in dashboard


def test_the_glama_connector_claim_is_served_as_json(client):
    """Glama fetches this from our origin to prove we control the domain.

    It must be valid JSON on a 2xx, so a redirect, an HTML error page or a
    stray byte breaks the listing rather than the site, which is exactly the
    kind of break nobody notices.
    """
    response = client.get("/.well-known/glama.json")
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    body = response.json()
    assert body["$schema"] == "https://glama.ai/mcp/schemas/connector.json"
    assert body["claim"].startswith("glama_claim_")


def test_llms_txt_carries_the_config_block_agents_need(client):
    """An agent told to set itself up should not have to guess key names.

    llms.txt gave the endpoint and the transport but not the mcpServers
    shape, so configuring a client meant a second fetch of /setup, or a
    guess at whether the key is "type", "transport" or "kind".
    """
    body = client.get("/llms.txt").text
    assert '"mcpServers"' in body
    assert '"type": "http"' in body
    assert "/mcp" in body


def test_the_pypi_relay_readme_carries_the_registry_token():
    """The MCP registry reads this package's PyPI description and refuses the
    listing unless the token is in it. It lives in a build script, where
    nothing else would ever exercise it."""
    from pathlib import Path

    builder = Path(__file__).resolve().parents[1] / "scripts" / "build_relay_package.py"
    assert "mcp-name: com.failecho/failecho" in builder.read_text()


def test_the_watchdog_waits_before_it_restarts_anything():
    """Restarting on a single failed probe turns one flaky network second into
    a real outage, and a watchdog that keeps restarting a server broken for
    some other reason hides the problem while looking busy."""
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "deploy" / "failecho-watchdog.sh"
    body = script.read_text()
    assert "LIMIT=2" in body, "one failure must not trigger a restart"
    assert "STILL FAILING after restart" in body, "say so instead of looping"
    assert "not restarting again" in body
    assert "--resolve failecho.com:443:127.0.0.1" in body, (
        "probe through Caddy on the real hostname, not just the local port -- "
        "the app can be fine while nothing external can reach it"
    )


def test_the_watchdog_gives_up_and_stays_given_up():
    """An audit found it deleted its failure counter after a failed restart,
    while logging that it would not restart again. The counter then rebuilt to
    the limit and it restarted a minute later -- a restart loop that buries
    whatever actually broke."""
    from pathlib import Path

    body = (Path(__file__).resolve().parents[1]
            / "deploy" / "failecho-watchdog.sh").read_text()
    assert "GAVE_UP=" in body, "a failed restart must leave a marker behind"
    # The marker is written where the old code wiped the counter.
    tail = body[body.index("STILL FAILING after restart"):]
    assert "$GAVE_UP" in tail.split("fi")[0], "give-up must be recorded, not forgotten"
    # And it has to be checked before anything decides to restart.
    assert body.index('if [[ -f "$GAVE_UP" ]]') < body.index("systemctl restart failecho")
    # Recovery is the thing that clears it, so an outage that heals resets.
    assert 'rm -f "$STATE" "$GAVE_UP"' in body


def test_a_failed_deploy_rolls_back_instead_of_leaving_the_site_down():
    """It waited for health and then exited 1 with the new, broken code still
    installed -- so a bad deploy was an outage until somebody noticed. Proved
    by deploying code that raises on import: it rolled back and the site kept
    serving."""
    from pathlib import Path

    body = (Path(__file__).resolve().parents[1] / "deploy" / "failecho").read_text()
    deploy = body[body.index("  deploy)"):]
    assert "failecho-previous" in deploy, "snapshot the version that is serving"
    assert "ROLLED BACK" in deploy
    # Readiness has to mean the public path answers, not just the local port:
    # the app can be healthy while nothing outside can reach it.
    assert "--resolve failecho.com:443:127.0.0.1" in deploy
    assert deploy.index("rm -rf \"$PREV\"") < deploy.index("systemctl restart failecho")


def _flat(text):
    """llms.txt is hard-wrapped; match prose on words, not on line breaks."""
    import re

    return re.sub(r"\s+", " ", text)
