"""The public homepage: structure, honesty, accessibility and weight.

The frontend is three static files with no build step, so these tests read
them directly. They guard the things a launch page can silently lose: the
real-vs-demo split, the empty states, the MCP endpoint, and the promise that
no framework crept in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "app" / "web" / "static"
HTML = (STATIC / "index.html").read_text()
CSS = (STATIC / "style.css").read_text()
JS = (STATIC / "app.js").read_text()


# ---------------------------------------------------------------------------
# structure
# ---------------------------------------------------------------------------


def test_hero_states_the_product_immediately(client):
    """Ten seconds: what it is, the tagline, what it does, how to connect."""
    body = client.get("/").text
    assert "FailEcho" in body
    assert "Failure intelligence for AI agents and autonomous software." in body
    assert "Before you retry, check the echo." in body
    assert "which recovery actions actually worked" in body
    assert "Connect MCP" in body
    assert "Read the API docs" in body
    assert "MCP · REST · OpenAPI · No account required" in body


@pytest.mark.parametrize(
    "heading",
    [
        "Live Network",
        "Active Incidents",
        "Recovery Echoes",
        "How FailEcho Works",
        "See It Happen",
        "Connect an Agent",
        "For Developers",
        "Why Successes Matter",
        "Privacy",
        "No Guessing",
        "Questions",
    ],
)
def test_every_launch_section_is_present(heading):
    assert f">{heading}</h2>" in HTML


def test_network_effect_statement_is_on_the_page():
    assert "Agent B benefits from evidence it never generated itself." in HTML


def test_mcp_endpoint_and_four_tools_are_prominent():
    assert 'id="mcp-endpoint"' in HTML
    for tool in (
        "check_tool_failure",
        "report_tool_failure",
        "report_tool_success",
        "report_recovery_outcome",
    ):
        assert f"<code>{tool}</code>" in HTML
    # ...and the endpoint is a rendered token, never a hardcoded localhost.
    assert "{{PUBLIC_URL}}/mcp" in HTML
    assert "localhost" not in HTML
    assert "publicOrigin()" in JS


def test_all_three_integration_paths_are_copyable():
    for snippet_id in ("code-mcp", "code-rest", "code-python"):
        assert f'id="{snippet_id}"' in HTML
        assert f'data-copy-target="{snippet_id}"' in HTML
    assert 'data-copy-target="mcp-endpoint"' in HTML
    for link in ("/docs", "/openapi.json", "/llms.txt"):
        assert f'href="{link}"' in HTML


def test_privacy_section_lists_what_we_refuse():
    for item in (
        "prompts",
        "API keys",
        "tool arguments",
        "tool results",
        "request bodies",
        "customer data",
        "secrets",
    ):
        assert f"<li>{item}</li>" in HTML
    assert "Shared intelligence without shared workloads." in HTML
    assert "Raw error text is discarded after normalization." in HTML
    assert "Reporter IDs are optional and hashed before storage." in HTML
    assert "No model is used to generate the confidence score." in HTML


# ---------------------------------------------------------------------------
# honesty: real vs demo, and empty states
# ---------------------------------------------------------------------------


def test_real_and_demo_counters_are_separate_elements():
    for element_id in (
        "stat-real-24h",
        "stat-real-reporters",
        "stat-real-fingerprints",
        "stat-real-incidents",
        "stat-demo-agent",
        "stat-synthetic",
    ):
        assert f'id="{element_id}"' in HTML
    # Demo counters read demo fields; real counters read real fields. No sums.
    assert "stats.real_observations_24h" in JS
    assert "stats.demo_agent_observations" in JS
    assert "+ stats.synthetic_observations" not in JS


def test_hidden_elements_actually_hide():
    """Regression: `.banner { display: flex }` beat the UA [hidden] rule, and a
    production instance rendered the DEMO MODE banner with demo mode off."""
    assert "[hidden] { display: none !important; }" in CSS
    for element in ("demo-mode-banner", "demo-mode-badge", "real-empty",
                    "demo-stats-block"):
        assert f'id="{element}"' in HTML and "hidden" in HTML


def test_the_zero_is_shown_not_hidden():
    assert "No real telemetry yet." in HTML
    assert "one of the first" in HTML and "real echoes" in HTML
    assert 'show("real-empty", stats.real_observations_24h === 0)' in JS


def test_empty_states_exist_for_every_live_section():
    """Empty must look deliberate, never broken."""
    assert "No real telemetry yet." in HTML
    assert "No active incidents." in JS
    assert "No recovery echo has enough evidence yet." in JS


def test_recovery_echo_shows_the_whole_evidence_basis():
    """Action alone is not evidence: attempts, rate, confidence, reporters."""
    for field in (
        "entry.action",
        "entry.successes",
        "entry.attempts",
        "entry.success_rate",
        "entry.confidence",
        "entry.unique_reporters",
    ):
        assert field in JS
    assert "Recovery echo" in JS
    assert "Wilson lower bound" in JS
    assert "No model-generated advice." in HTML


def test_demo_rows_are_badged(client):
    assert 'class="tag">DEMO<' in JS
    assert "row.demo_data" in JS
    assert "entry.demo_data" in JS


def test_demo_badge_data_is_actually_served(client):
    """The frontend can only badge demo rows if the API says which are demo."""
    from tests.conftest import insert_observation

    for _ in range(12):
        insert_observation(source="synthetic", fingerprint="f" * 32)
    row = client.get("/v1/services").json()[0]
    assert row["demo_data"] is True

    stats = client.get("/v1/stats").json()
    assert stats["real_active_failures"] == 0
    assert stats["active_failures"] >= 0


def test_real_active_incidents_counts_real_rows_only(client):
    from tests.conftest import insert_observation, observe

    insert_observation(source="synthetic", fingerprint="f" * 32, minutes_ago=0)
    observe(client)  # real
    stats = client.get("/v1/stats").json()
    assert stats["real_active_failures"] == 1
    assert stats["active_failures"] == 2


# ---------------------------------------------------------------------------
# accessibility
# ---------------------------------------------------------------------------


def test_semantic_landmarks_and_labels():
    assert '<html lang="en">' in HTML
    assert "<main id=\"main\">" in HTML
    assert "<header" in HTML and "<footer" in HTML
    assert HTML.count("aria-labelledby=") >= 7
    assert 'class="skip-link"' in HTML
    assert 'scope="col"' in HTML


def test_interactive_elements_are_real_buttons_with_labels():
    assert HTML.count('type="button"') == 4, "endpoint + three snippets"
    assert HTML.count("data-copy-target=") == 4
    assert HTML.count("aria-label=") >= 4
    assert ":focus-visible" in CSS


def test_status_is_never_communicated_by_colour_alone():
    # Status renders a word plus a distinct shape: filled, half, hollow, dashed.
    assert "status.replace(/_/g" in JS
    assert 'class="status-mark" aria-hidden="true"' in JS


def test_live_regions_announce_updates():
    assert 'aria-live="polite"' in HTML
    assert 'role="status"' in HTML


def test_reduced_motion_is_respected():
    assert "prefers-reduced-motion" in CSS
    assert CSS.count("prefers-reduced-motion") >= 2


def test_palette_is_committed_and_explicit():
    """One deliberate dark palette, painted explicitly rather than inherited."""
    assert 'name="color-scheme" content="dark"' in HTML
    assert 'name="theme-color"' in HTML
    assert "--bg:" in CSS and "background: var(--bg)" in CSS
    # Brand red is an accent, not the page: it must not paint a background
    # anywhere except the primary action and the demo banner tint.
    assert CSS.count("background: var(--red-deep)") <= 1


# ---------------------------------------------------------------------------
# weight and dependencies
# ---------------------------------------------------------------------------


def test_one_h1_and_it_is_the_brand():
    """Search engines should read one heading, and it should say FailEcho."""
    assert HTML.count("<h1") == 1
    assert 'id="hero-title"' in HTML
    heading = HTML[HTML.index("<h1") : HTML.index("</h1>")]
    assert 'alt="FailEcho"' in heading


def test_no_framework_no_cdn_no_webfont():
    combined = HTML + CSS + JS
    for forbidden in (
        "react",
        "vue",
        "svelte",
        "tailwind",
        "cdn.",
        "unpkg",
        "jsdelivr",
        "googleapis",
        "@font-face",
        "analytics",
    ):
        assert forbidden not in combined.lower(), f"{forbidden} must not appear"
    # One behaviour script plus the JSON-LD block, which is data, not code.
    assert HTML.count("<script") == 2
    assert HTML.count('<script type="application/ld+json">') == 1


def test_static_assets_stay_small():
    """A status page has no excuse to be heavy on a small VPS."""
    assert len(HTML) < 26_000  # includes the FAQ and the JSON-LD block
    assert len(CSS) < 20_000
    assert len(JS) < 12_000
    assert sum(len(x) for x in (HTML, CSS, JS)) < 55_000

    # Brand images are raster (the supplied masters are PNG). What a visitor
    # actually downloads is the markup, the mark and ONE wordmark variant --
    # the other variant and the social card are never fetched by the page.
    per_visit = (
        sum(len(x) for x in (HTML, CSS, JS))
        + (STATIC / "logo.png").stat().st_size
        + (STATIC / "wordmark-light.png").stat().st_size
    )
    assert per_visit < 110_000, f"page weight crept to {per_visit} bytes"
    assert not list(STATIC.glob("*.jpg")), "no photographic assets"


def test_polling_is_conservative():
    assert "var REFRESH_MS = 30000;" in JS


def test_mobile_layout_rules_exist():
    assert "max-width: 480px" in CSS
    assert "overflow-x: auto" in CSS  # tables and code blocks scroll, page does not


def test_polling_pauses_when_the_tab_is_hidden():
    """A forgotten tab used to poll three endpoints forever, inflating both
    the origin load and our own traffic numbers."""
    assert 'addEventListener("visibilitychange"' in JS
    assert "document.hidden" in JS
    assert "clearInterval" in JS
