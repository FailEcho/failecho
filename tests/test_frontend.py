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
    """Five to ten seconds: the problem, the category, the tagline, the CTA."""
    body = client.get("/").text
    assert "AI agents shouldn't debug" in body and "the same failure twice." in body
    assert "Live failure and recovery intelligence for autonomous software." in body
    assert "Before you retry, check the echo." in body
    assert "what actually worked, before you retry" in body
    assert "Connect MCP" in body
    assert "See it work" in body
    assert "MCP · REST · OpenAPI · No account required" in body


@pytest.mark.parametrize(
    "heading",
    [
        "Live Network",
        "See It Happen",
        "Recovery Echoes",
        "How FailEcho Works",
        "Connect an Agent",
        "For Developers",
        "Built to be checked",
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


def test_trust_section_keeps_the_load_bearing_promises():
    """Compressed to three principles, but the specific claims must survive."""
    for principle in ("Privacy-safe", "Evidence-based", "Deterministic"):
        assert f"<h3>{principle}</h3>" in HTML
    assert "No prompts. No secrets. No tool arguments or results." in HTML
    assert "Raw error text is discarded after normalization" in HTML
    assert "reporter IDs are optional and hashed before storage" in HTML
    assert "No model produces a recovery recommendation or a confidence score." in HTML
    assert "INSUFFICIENT_DATA" in HTML
    # The full privacy contract now lives on /about, and is linked.
    assert 'href="/about"' in HTML


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
    """Empty must read as bootstrapping, never as broken -- and never as fake."""
    assert "Public network is bootstrapping." in HTML
    assert "No real external telemetry has been reported today." in HTML
    assert "Be one of the first agents contributing to the network." in HTML
    assert 'show("real-empty", stats.real_observations_24h === 0)' in JS
    # The metric elements remain, so the real zeros are still rendered.
    for element in ("stat-real-24h", "stat-real-reporters",
                    "stat-real-fingerprints", "stat-real-incidents"):
        assert f'id="{element}"' in HTML


def test_empty_states_exist_for_every_live_section():
    """Empty must look deliberate, never broken."""
    assert "Public network is bootstrapping." in HTML
    assert "No active incidents." in HTML
    assert 'show("incidents-table", rows.length > 0)' in JS
    assert "No recovery echo has enough real evidence yet." in JS


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


def test_one_h1_stating_the_problem():
    """One heading. It names the problem; the brand is in the title, the header
    wordmark and the entity sentence, so the h1 does not have to repeat it."""
    assert HTML.count("<h1") == 1
    assert 'id="hero-title"' in HTML
    heading = HTML[HTML.index("<h1") : HTML.index("</h1>")]
    assert "same failure twice." in heading
    # The brand still has to be unmissable for a crawler.
    assert "<title>FailEcho" in HTML
    assert "FailEcho is a shared failure intelligence network for AI agents" in HTML


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
    assert len(HTML) < 30_000  # includes the demo story, FAQ and JSON-LD
    assert len(CSS) < 26_000
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
    assert per_visit < 120_000, f"page weight crept to {per_visit} bytes"
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


def test_python_example_does_not_advertise_a_package_that_is_not_published():
    """The client is not on PyPI, so the page must not imply pip install."""
    snippet = HTML[HTML.index('id="code-python"') : HTML.index("</code></pre>", HTML.index('id="code-python"'))]
    assert "pip install failecho" not in snippet
    assert "Not published to PyPI yet" in snippet
    assert 'sys.path.insert(0, "client")' in snippet, "the setup that actually works"


def test_no_unsubstituted_tokens_reach_the_page(client):
    """Regression: {{GITHUB_URL}} sat in a code comment outside the block that
    strips it, so an unconfigured instance would have shipped the raw token."""
    body = client.get("/").text
    for token in ("{{PUBLIC_URL}}", "{{ASSET_V}}", "{{GITHUB_URL}}"):
        assert token not in body, token


def test_demo_output_is_behind_a_disclosure():
    """The proof stays available; it just no longer dominates the page."""
    assert "<details class=\"disclosure\">" in HTML
    assert "View full demo output" in HTML
    assert "6ed9ef705ff4037af2c977306b8b9f92" in HTML, "the real fingerprint"


def test_the_demo_story_states_the_payoff():
    assert "Agent B recovered using evidence it never generated itself." in HTML
    assert "Agent B benefits from evidence it never generated itself." in HTML


def test_status_legend_matches_the_backend_thresholds():
    from app.core.config import settings

    assert settings.healthy_max_failure_rate == 0.05
    assert settings.degraded_max_failure_rate == 0.30
    assert settings.min_observations_for_status == 10
    assert "under 5% failures" in HTML
    assert "5–30%" in HTML
    assert "above 30%" in HTML
    assert "at least 10 observations" in HTML
