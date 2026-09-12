"""The homepage: brief, installable at a glance, and honest about the zeros.

The front page used to carry nine sections and do the job of five pages. It
now says what FailEcho is, shows the install, and sends people to /network,
/demo, /setup and /about. These tests hold it to that shape -- a page that
grows back into a brochure is a regression, not an improvement.

The guarantees that moved (dashboard, story, snippets) are tested where they
now live: test_network_page.py, test_demo_page.py, test_setup_page.py.
"""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "web" / "static"
HTML = (STATIC / "index.html").read_text()
CSS = (STATIC / "style.css").read_text()
JS = (STATIC / "app.js").read_text()


# ---------------------------------------------------------------------------
# what the front page must say
# ---------------------------------------------------------------------------


def test_hero_states_the_product_immediately(client):
    """Five to ten seconds: the problem, the category, the tagline, the CTA."""
    body = client.get("/").text
    assert "AI agents shouldn't debug" in body and "the same failure twice." in body
    assert "Live failure and recovery intelligence for autonomous software." in body
    assert "Before you retry, check the echo." in body
    assert "what actually worked, before you retry" in body
    assert "Get started" in body
    assert "See it work" in body
    assert "MCP · REST · OpenAPI · No account required" in body


def test_the_install_is_visible_without_scrolling(client):
    """The point of the rewrite: install in one look, not after a tour."""
    body = client.get("/").text
    hero = body[body.index('class="shell hero'):body.index("</section>")]
    assert "/plugin marketplace add FailEcho/failecho" in hero
    assert "/plugin install failecho@failecho" in hero
    assert 'data-copy-target="code-plugin"' in hero
    assert 'href="/setup"' in hero, "every other client needs somewhere to go"


def test_the_front_page_is_not_a_claude_code_accessory(client):
    """FailEcho is a protocol endpoint. One client must not own the hero."""
    body = client.get("/").text
    hero = body[body.index('class="shell hero'):body.index("</section>")]
    assert "/mcp" in hero and "/v1/query" in hero, "MCP and REST are products too"
    assert hero.count('role="tab"') == 3
    for tab in ("Claude Code", "any MCP client", "Any language"):
        assert tab in hero, tab
    # The chrome names no client at all.
    nav = body[body.index('<nav'):body.index("</nav>")]
    assert "Claude Code" not in nav


def test_only_the_first_install_panel_shows_without_javascript():
    panels = HTML.count('role="tabpanel"')
    assert panels == 3
    assert HTML.count('role="tabpanel"') - HTML.count('role="tabpanel" id="panel-plugin"') == 2
    assert JS.count('setAttribute("aria-selected"') == 1, "tabs are wired, not decorative"


def test_the_front_page_stays_brief():
    """It is a front page, not the manual. Nine sections was the old mistake."""
    assert HTML.count("<h2") <= 4, "more than four sections means it is growing back"
    assert len(HTML) < 12_000


def test_it_routes_to_the_pages_that_hold_the_detail(client):
    body = client.get("/").text
    for href in ('href="/network"', 'href="/demo"', 'href="/setup"', 'href="/about"',
                 'href="/docs"', 'href="/llms.txt"'):
        assert href in body, href


def test_the_loop_is_drawn_as_a_loop():
    """A cycle, not a funnel: the return path is the product."""
    for step in ("Fail", "Report", "Learn", "Recover"):
        assert f'class="flow-name">{step}<' in HTML
    assert 'class="loop-return"' in HTML
    assert "The next agent asks before it retries" in HTML
    assert ".loop-return::after" in CSS, "the return arrow is drawn, not implied"
    assert "Agent B benefits from evidence it never generated itself." in HTML


def test_the_live_line_separates_external_from_our_own(client):
    """Two numbers, and the second says plainly that it is not adoption."""
    body = client.get("/").text
    assert 'id="stat-real-24h"' in body and 'id="stat-first-party"' in body
    assert "observations from independent agents today" in body
    assert "never counted as adoption" in body
    assert "not dressed up" in body
    assert 'href="/network"' in body


def test_the_trust_claims_survive_the_trim(client):
    body = client.get("/").text
    assert "No prompts. No secrets. No tool arguments or results." in body
    assert "INSUFFICIENT_DATA" in body
    assert 'href="/about"' in body


# ---------------------------------------------------------------------------
# accessibility
# ---------------------------------------------------------------------------


def test_semantic_landmarks_and_labels():
    assert '<html lang="en">' in HTML
    assert '<main id="main">' in HTML
    assert "<header" in HTML and "<footer" in HTML
    assert HTML.count("aria-labelledby=") >= 4
    assert 'class="skip-link"' in HTML


def test_interactive_elements_are_real_buttons_with_labels():
    """Three tabs, and a copy button on each panel."""
    assert HTML.count('type="button"') == 6
    assert HTML.count("data-copy-target=") == 3
    assert HTML.count("aria-label=") >= 2
    assert ":focus-visible" in CSS


def test_live_regions_announce_updates():
    assert 'aria-live="polite"' in HTML
    assert 'role="status"' in HTML


def test_reduced_motion_is_respected():
    assert "prefers-reduced-motion" in CSS
    assert CSS.count("prefers-reduced-motion") >= 2


def test_palette_is_committed_and_explicit():
    assert 'name="color-scheme" content="dark"' in HTML
    assert 'name="theme-color"' in HTML
    assert "--bg:" in CSS and "background: var(--bg)" in CSS
    assert CSS.count("background: var(--red-deep)") <= 1


def test_hidden_elements_actually_hide():
    """Regression: a class that sets `display` outranks the UA [hidden] rule."""
    assert "[hidden] { display: none !important; }" in CSS
    for element in ("demo-mode-banner", "demo-mode-badge"):
        assert f'id="{element}"' in HTML and "hidden" in HTML


def test_one_h1_stating_the_problem():
    assert HTML.count("<h1") == 1
    assert 'id="hero-title"' in HTML
    heading = HTML[HTML.index("<h1"):HTML.index("</h1>")]
    assert "same failure twice." in heading
    assert "<title>FailEcho" in HTML
    assert "FailEcho is a shared failure intelligence network for AI agents" in HTML


# ---------------------------------------------------------------------------
# weight and dependencies
# ---------------------------------------------------------------------------


def test_no_framework_no_cdn_no_webfont():
    combined = HTML + CSS + JS
    for forbidden in ("react", "vue", "svelte", "tailwind", "cdn.", "unpkg",
                      "jsdelivr", "googleapis", "@font-face", "analytics"):
        assert forbidden not in combined.lower(), f"{forbidden} must not appear"
    assert HTML.count("<script") == 2
    assert HTML.count('<script type="application/ld+json">') == 1


def test_static_assets_stay_small():
    """A status page has no excuse to be heavy on a small VPS."""
    # Raised once, deliberately, for the install tabs, the loop diagram and
    # the stat cards. Trimming comments to defend a number is the wrong trade.
    assert len(CSS) < 33_000
    assert len(JS) < 12_000
    assert sum(len(x) for x in (HTML, CSS, JS)) < 56_000

    # The hero artwork is the single heaviest thing the homepage loads, so it
    # is counted here rather than left out of the number it dominates. It is
    # decorative: quantised hard, and capped so it cannot creep back up.
    art = (STATIC / "echoimage.png").stat().st_size
    assert art < 90_000, f"hero art is {art} bytes; re-quantise it"

    per_visit = (
        sum(len(x) for x in (HTML, CSS, JS))
        + (STATIC / "logo.png").stat().st_size
        + (STATIC / "wordmark-light.png").stat().st_size
        + art
    )
    assert per_visit < 165_000, f"page weight crept to {per_visit} bytes"
    assert not list(STATIC.glob("*.jpg")), "no photographic assets"


def test_the_hero_art_stays_behind_the_hero():
    """Decoration must not reach the bands below, or the text on top of it."""
    assert '.hero--center::before' in CSS
    block = CSS[CSS.index(".hero--center::before"):CSS.index(".hero--center h1")]
    assert "z-index: -1" in block and "pointer-events: none" in block
    assert "overflow: hidden" in CSS[CSS.index(".hero--center {"):CSS.index(".hero--center::before")]


def test_mobile_layout_rules_exist():
    assert "max-width: 480px" in CSS
    assert "overflow-x: auto" in CSS


def test_polling_is_conservative():
    assert "var REFRESH_MS = 30000;" in JS


def test_polling_pauses_when_the_tab_is_hidden():
    assert 'addEventListener("visibilitychange"' in JS
    assert "document.hidden" in JS
    assert "clearInterval" in JS


def test_each_page_fetches_only_what_it_shows():
    """The homepage carries two numbers; only /network needs the tables; and
    /setup and /demo load the script for copy buttons alone, so they must not
    call the API at all."""
    assert 'if (el("services-body"))' in JS
    assert 'if (el("recovery-list"))' in JS
    assert 'if (el("stat-real-24h")) wants.push' in JS


def test_every_code_box_gets_a_copy_button():
    """Built by the script, so a page carries its code and not a dead button."""
    assert 'document.querySelectorAll("pre > code")' in JS
    assert 'box.querySelector("[data-copy-target]")' in JS, "hand-written ones win"
    assert ".copy-btn {" in CSS and ".has-copy > pre {" in CSS


def test_no_unsubstituted_tokens_reach_any_page(client):
    for page in ("/", "/network", "/demo", "/about", "/setup"):
        body = client.get(page).text
        for token in ("{{PUBLIC_URL}}", "{{ASSET_V}}", "{{GITHUB_URL}}"):
            assert token not in body, f"{token} on {page}"
