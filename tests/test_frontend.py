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
    """Five to ten seconds: the problem, the category, the tagline, the CTA.

    The long definition moved out of the hero and into the section that
    explains the loop -- the hero was too tall for a laptop screen, and a
    paragraph nobody reads before the install card was the part to lose. The
    definition still has to be on the page, for search and for anyone who
    arrives not knowing what this is.
    """
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
    assert hero.count('role="tab"') == 4
    for tab in ("Claude Code", "any MCP client", "Any language", "Let the agent"):
        assert tab in hero, tab
    # The chrome names no client at all.
    nav = body[body.index('<nav'):body.index("</nav>")]
    assert "Claude Code" not in nav


def test_only_the_first_install_panel_shows_without_javascript():
    panels = HTML.count('role="tabpanel"')
    assert panels == 4
    assert HTML.count('role="tabpanel"') - HTML.count('role="tabpanel" id="panel-plugin"') == 3
    assert JS.count('setAttribute("aria-selected"') == 1, "tabs are wired, not decorative"


def test_the_front_page_stays_brief():
    """It is a front page, not the manual. Nine sections was the old mistake."""
    assert HTML.count("<h2") <= 4, "more than four sections means it is growing back"
    # Four ways in, four tabs. The budget follows the install card, not the
    # other way round -- but sections are still capped at four above.
    assert len(HTML) < 13_000


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
    """Four tabs, and a copy button on each panel."""
    assert HTML.count('type="button"') == 8
    assert HTML.count("data-copy-target=") == 4
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
    # 33k -> 34k. Flagged last time that there was no dead CSS left to
    # reclaim and the next addition would be a raise rather than a trim; this
    # is it, for the mobile code size that stops the install card scrolling
    # sideways on a phone.
    assert len(CSS) < 34_000
    assert len(JS) < 12_000
    # 57k -> 58k for the distribution line under the hero and the panel
    # height that stops a tab switch resizing the artwork. The stylesheet
    # stayed under its own cap without raising it; there is no dead CSS left
    # to reclaim, so the next addition is an honest raise rather than a trim.
    assert sum(len(x) for x in (HTML, CSS, JS)) < 58_000

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


# ---------------------------------------------------------------------------
# the pages a visitor reaches by mistake
# ---------------------------------------------------------------------------


def test_a_mistyped_url_gets_a_page_not_a_json_error(client):
    """A wrong link in a comment thread is a first impression too."""
    page = client.get("/typo", headers={"accept": "text/html"})
    assert page.status_code == 404
    assert "text/html" in page.headers["content-type"]
    assert "No page at that address" in page.text
    assert 'content="noindex"' in page.text
    for href in ('href="/network"', 'href="/demo"', 'href="/setup"', 'href="/docs"'):
        assert href in page.text, href


def test_api_clients_still_get_json_from_a_404(client):
    """The split is on what the caller asked for, so agents are unaffected."""
    body = client.get("/v1/typo", headers={"accept": "application/json"})
    assert body.status_code == 404
    assert body.json() == {"detail": "Not Found"}


def test_no_page_links_to_a_fragment_that_does_not_exist():
    """Splitting the homepage stranded /#live-network, /#demo and /#privacy.

    An HTTP link check cannot see this: /#gone answers 200 like any other /.
    Same-page links are checked against that page; /#x against the homepage.
    """
    import re

    pages = {p.name: p.read_text() for p in STATIC.glob("*.html")}

    def ids_of(html):
        return set(re.findall(r'id="([^"]+)"', html))

    home_ids = ids_of(pages["index.html"])
    for name, html in pages.items():
        own = ids_of(html)
        for href in re.findall(r'href="#([^"]+)"', html):
            assert href in own, f"{name} links to #{href}, which it does not define"
        for href in re.findall(r'href="/#([^"]+)"', html):
            assert href in home_ids, f"{name} links to /#{href}; the homepage has no such id"
        # cross-page fragments: /setup#mcp-clients and friends
        for page, frag in re.findall(r'href="/([a-z-]+)#([^"]+)"', html):
            target = pages.get(f"{page}.html")
            assert target is not None, f"{name} links into /{page}, which is not a page"
            assert frag in ids_of(target), f"{name} links to /{page}#{frag}, which does not exist"

    assert "/#" not in JS, "the script must not build a link into a homepage anchor"


def test_the_solo_claim_says_recoveries_not_failures(client):
    """An audit caught this: the page said five repeats of a failure start the
    recommendations. Five failures with no outcomes return a null
    recommendation and an empty action list, correctly -- a failure cannot
    prove a fix. The threshold is five recovery attempts."""
    body = client.get("/").text
    assert "Recover from the same" in body
    assert "Failures on their own are" in body
    assert "Five repeats of your own" not in body, "the inaccurate wording is back"


def test_every_get_started_button_goes_to_setup(client):
    """/about sent Get started to /#how, which explains the product instead of
    starting it. The fragment resolves, so the link checker was happy."""
    import re

    for page in ("/", "/network", "/demo", "/about", "/setup"):
        html = client.get(page).text
        for href in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>Get started<', html):
            assert href in ("/setup", "#claude-code"), f"{page} sends Get started to {href}"


def test_the_hero_fits_a_laptop_screen():
    """It was 1014px tall plus a 76px bar, so on a 1080 viewport the tagline
    and the distribution line fell below the fold and the page looked
    truncated. Trimmed to 930 by taking air out of the padding and the gaps,
    not by removing anything."""
    block = CSS[CSS.index(".hero--center {"):CSS.index(".hero--center::before")]
    assert "padding-block: 72px 60px" in block, "the homepage hero has grown again"


def test_no_install_snippet_is_wider_than_its_box():
    """A code box that scrolls sideways looks broken, and the REST snippet was
    doing it: 760px of content in a 658px box on desktop. Measured after the
    fix at 698/698 on all four tabs at 1440 and 768, and 435/435 at 390 once
    the mobile type came down to 12px."""
    longest = max(
        (len(line) for block in HTML.split("<pre><code")[1:]
         for line in block.split("</code>")[0].splitlines()),
        default=0,
    )
    assert longest <= 66, f"longest snippet line is {longest} chars; it will scroll"
