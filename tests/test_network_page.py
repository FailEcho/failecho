"""/network: the live dashboard, and every promise that came with it.

This page took the real-vs-first-party split, the empty states and the badges
off the homepage. The guarantees did not move house -- they moved page -- so
they are enforced here now.
"""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "web" / "static"
HTML = (STATIC / "network.html").read_text()
JS = (STATIC / "app.js").read_text()


def test_network_is_served(client):
    response = client.get("/network")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Live network" in response.text


def test_page_metadata(client):
    body = client.get("/network").text
    assert "<title>Live network — FailEcho</title>" in body
    assert '<link rel="canonical" href="http' in body and "/network" in body
    assert 'property="og:url"' in body


def test_real_and_demo_counters_are_separate_elements():
    for element_id in ("stat-real-24h", "stat-real-reporters", "stat-real-fingerprints",
                       "stat-real-incidents", "stat-first-party", "stat-demo-agent",
                       "stat-synthetic"):
        assert f'id="{element_id}"' in HTML
    # Demo counters read demo fields; real counters read real fields. No sums.
    assert "stats.real_observations_24h" in JS
    assert "stats.demo_agent_observations" in JS
    assert "+ stats.synthetic_observations" not in JS


def test_the_zero_is_shown_not_hidden():
    """Empty must read as bootstrapping, never as broken -- and never as fake."""
    assert "Public network is bootstrapping." in HTML
    assert "No real external telemetry has been reported today." in HTML
    assert "Be one of the first agents contributing to the network." in HTML
    assert 'show("real-empty", stats.real_observations_24h === 0)' in JS


def test_empty_states_exist_for_every_live_section():
    assert "Nothing reported in the last hour." in HTML
    assert 'show("incidents-table", rows.length > 0)' in JS
    assert "No recovery echo has enough real evidence yet." in JS


def test_first_party_and_demo_are_labelled_and_kept_apart():
    assert 'id="first-party-block"' in HTML
    assert 'id="demo-stats-block"' in HTML
    assert "never counted as adoption" in HTML
    assert 'class="tag">FIRST-PARTY<' in JS
    assert 'class="tag">DEMO<' in JS
    assert "sourceTags(row)" in JS and "sourceTags(entry)" in JS
    assert "item.first_party_data" in JS and "item.demo_data" in JS


def test_hidden_blocks_start_hidden():
    for element in ("real-empty", "demo-stats-block", "first-party-block",
                    "incidents-table", "incidents-empty"):
        assert f'id="{element}"' in HTML
    assert HTML.count("hidden") >= 5


def test_recovery_echo_shows_the_whole_evidence_basis():
    """Action alone is not evidence: attempts, rate, confidence, reporters."""
    for field in ("entry.action", "entry.successes", "entry.attempts",
                  "entry.success_rate", "entry.confidence", "entry.unique_reporters"):
        assert field in JS
    assert "Recovery echo" in JS
    assert "Wilson lower bound" in JS
    assert "No model-generated advice." in HTML


def test_status_legend_matches_the_backend_thresholds():
    from app.core.config import settings

    assert settings.healthy_max_failure_rate == 0.05
    assert settings.degraded_max_failure_rate == 0.30
    assert settings.min_observations_for_status == 10
    assert "under 5% failures" in HTML
    assert "5–30%" in HTML
    assert "above 30%" in HTML
    assert "at least 10 observations" in HTML


def test_a_rate_is_not_printed_when_the_status_says_there_is_no_evidence():
    assert 'row.status === "INSUFFICIENT_DATA" ? null' in JS
    assert 'return rate === null || rate === undefined ? "—"' in JS
    assert ">Live services</h2>" in HTML


def test_the_services_table_is_capped():
    assert "var SERVICE_ROWS = 8;" in JS
    assert 'getJSON("/v1/services?limit=" + SERVICE_ROWS)' in JS
    assert "Showing the ' + SERVICE_ROWS +" in JS


def test_demo_badge_data_is_actually_served(client):
    """The page can only badge demo rows if the API says which are demo."""
    from tests.conftest import insert_observation

    for _ in range(12):
        insert_observation(source="synthetic", fingerprint="f" * 32)
    row = client.get("/v1/services").json()[0]
    assert row["demo_data"] is True

    stats = client.get("/v1/stats").json()
    assert stats["real_active_failures"] == 0


def test_real_active_incidents_counts_real_rows_only(client):
    from tests.conftest import insert_observation, observe

    insert_observation(source="synthetic", fingerprint="f" * 32, minutes_ago=0)
    # a real reporter past the adoption threshold, then its live failure
    from app.core.privacy import hash_reporter_id

    for i, service in enumerate(("api.github.com", "api.github.com", "pypi.org", "pypi.org", "api.github.com")):
        insert_observation(service=service, operation="op", reporter_hash=hash_reporter_id("real-agent"),
                           minutes_ago=60 - i * 5, fingerprint=None)
    observe(client, headers={"X-Reporter-ID": "real-agent"})  # real
    stats = client.get("/v1/stats").json()
    assert stats["real_active_failures"] == 1
    assert stats["active_failures"] == 2
