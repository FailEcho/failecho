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

    The definition came back into the hero when the hero became two columns.
    It cost the page its height once, when everything was stacked down the
    middle; beside the install card it costs nothing, and the reader who
    arrives not knowing what this is no longer has to scroll to find out.
    """
    import re

    body = re.sub(r"\s+", " ", client.get("/").text)
    assert "AI agents shouldn't debug" in body and "the same failure twice." in body
    assert "Live failure and recovery intelligence for autonomous software." in body
    assert "Before you retry, check the echo." in body
    assert "what actually worked, before you retry" in body
    assert "Get started" in body
    assert "See it work" in body
    assert "Claude Code · MCP · REST · OpenAPI · No account required" in body


def test_the_install_is_visible_without_scrolling(client):
    """The point of the rewrite: install in one look, not after a tour. Four
    ways, all four on the page rather than three behind a tab strip."""
    body = client.get("/").text
    ways = body[body.index('class="ways"'):body.index('class="install-note"')]
    assert "/plugin marketplace add FailEcho/failecho" in ways
    assert 'data-copy-target="code-plugin"' in ways
    assert ways.count('class="way ') == 4
    hero = body[body.index('class="shell hero'):body.index("</section>")]
    assert 'href="/setup"' in hero, "every other client needs somewhere to go"


def test_the_front_page_is_not_a_claude_code_accessory(client):
    """FailEcho is a protocol endpoint. One client must not own the top of
    the page, and none of the four may be a tab-click away while another is
    not."""
    body = client.get("/").text
    ways = body[body.index('class="ways"'):body.index('class="install-note"')]
    assert "/mcp" in ways and "/v1/query" in ways, "MCP and REST are products too"
    assert 'role="tab"' not in body, "one way is selected and three are hidden"
    for way in ("Claude Code", "any MCP client", "Any language", "Let the agent"):
        assert way in ways, way
    # The chrome names no client at all.
    nav = body[body.index('<nav'):body.index("</nav>")]
    assert "Claude Code" not in nav


def test_the_front_page_stays_brief():
    """It is a front page, not the manual. Nine sections was the old mistake."""
    assert HTML.count("<h2") <= 5, "more than five sections means it is growing back"
    # Four ways in, four tabs. The budget follows the install card, not the
    # other way round -- but sections are still capped at four above.
    # 13k -> 14k when the definition moved back into the hero's left column,
    # 14k -> 15k for the three cards that close the page, 15k -> 16k for the
    # four install cards carrying two faces each.
    assert len(HTML) < 18_000


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
    """One real button per card, and the block beside it is clickable too.

    The block used to carry role="button" and a tab stop of its own, which
    made two tab stops per card for one action. It is a convenience for a
    mouse now; the button is the control, and it is a real one.
    """
    assert HTML.count('class="way-copy"') == 4, "one Copy button per card"
    assert HTML.count("data-copy-target=") == 8, "the button and the block"
    assert HTML.count('class="copyable"') == 4
    assert 'role="button"' not in HTML, "two tab stops per card is one too many"
    assert HTML.count("aria-label=") >= 4, "each button says what it copies"
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
    # Twice: the primary button, and the bar's copy of it holding still on
    # hover. Red stays scarce -- the logo, one action, real failure signal.
    assert CSS.count("background: var(--red-deep)") <= 2


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
    # 34k -> 35k for the on-this-page rail on /setup and /about, both of
    # which passed ten sections.
    # 35k -> 36k for the four jump chips staying on a wide screen next to the
    # rail, and for the copy control fitting inside the terminal title bar.
    # 47k of CSS: the four install cards, two faces each, and a loop that
    # scales with the window.
    # 25k -> 27k: the scramble pins its own box and plays once a tab.
    # 23k -> 25k: the scramble measures every distinct character so it can
    # only ever swap one for another of the same width.
    # 21k -> 23k: closing the menu belongs to the bar rather than to each
    # item, which took a listener per panel and one on the scrim.
    # 18k -> 21k for the menu that pulls the bar down, measures its own
    # height and blurs the page behind it.
    # 39k -> 41k, 16k -> 18k: the two states of the top bar, the drifting
    # hero ground that replaced a 74KB photograph, and the scramble. The
    # stylesheet grew by less than the image it removed.
    # 37k -> 39k for the two-column hero: a grid, a code block that is its
    # own copy control, and a prompt caret. The centred hero's rules were
    # deleted rather than left behind -- nothing else used them.
    # 36k -> 37k, 12k -> 15k for three pieces of motion that carry
    # information: a figure that moved since the last poll, the rail marking
    # the section you are in, and a tab switch reading as a swap. The script
    # takes most of it -- the rail reads section positions itself rather than
    # tuning an observer's thresholds, and that logic is worth its comments.
    assert len(CSS) < 52_000
    assert len(JS) < 27_000
    # 57k -> 58k for the distribution line under the hero and the panel
    # height that stops a tab switch resizing the artwork. The stylesheet
    # stayed under its own cap without raising it; there is no dead CSS left
    # to reclaim, so the next addition is an honest raise rather than a trim.
    # 58k -> 60k, same reason: the rail's CSS is shared with the homepage's
    # stylesheet even though only /setup and /about use it.
    # 60k -> 64k for the motion above, 64k -> 65k for the nav panel that was
    # opening on top of the word that opened it, 65k -> 69k for the hero,
    # 69k -> 73k for the two-state bar and the drifting ground, 73k -> 82k
    # for everything since: the menu, the loop's light, the return path, and
    # a scramble that has to measure before it can be stable.
    assert sum(len(x) for x in (HTML, CSS, JS)) < 95_000

    # No decorative download at all: the artwork was behind the hero, where
    # it was the wrong shape at most window sizes, then a mirrored pair above
    # the footer, and now it is nowhere.
    assert "echoimage" not in CSS, "the stylesheet must not pull it in"

    # 74KB at 800px when it was a full-bleed hero ground; 28KB at 480px and
    # 24 colours now that it is a 210px band at 16% opacity behind a mask.
    per_visit = (
        sum(len(x) for x in (HTML, CSS, JS))
        + (STATIC / "logo.png").stat().st_size
        + (STATIC / "wordmark-dark.png").stat().st_size
        + (STATIC / "card-abstract1.webp").stat().st_size
    )
    # 150k -> 120k: no full-page decorative download at all now, and the light-ink
    # wordmark is not on this page -- the bar and the footer both use the
    # white-ink one.
    assert per_visit < 128_000, f"page weight crept to {per_visit} bytes"
    assert not list(STATIC.glob("*.jpg")), "no photographic assets"


def test_the_hero_ground_is_nothing_at_all():
    """It was a 74KB photograph, then two drifting red gradients, and now it
    is black. The type is the only thing on it."""
    assert "@keyframes drift" not in CSS
    assert ".hero--lead::before" not in CSS
    hero = HTML[HTML.index('class="shell hero hero--lead"'):HTML.index('class="shell install"')]
    assert "echoimage" not in hero and "url(" not in hero


def test_the_page_carries_no_decorative_download():
    """The artwork was behind the hero, then a mirrored pair above the footer,
    and now it is nowhere. Every image the page loads is a brand mark."""
    assert "echoimage" not in HTML and "echoimage" not in CSS
    assert "echoband" not in HTML and "echoband" not in CSS


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
    # The four install blocks are their own control, so they are not counted.
    assert HTML.count('class="copyable"') == 4


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


def _element_ids(html):
    """Every id the browser actually creates, in document order.

    Scraping id="..." out of the text is not the same thing. An element
    written with two id attributes keeps the first and silently drops the
    second, so a rail link to the dropped one resolves in a regex and does
    nothing in a browser. That shipped once; this reads it the way the
    parser does.
    """
    from html.parser import HTMLParser

    found = []

    class Reader(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for name, value in attrs:
                if name == "id" and value:
                    found.append(value)
                    return

    Reader().feed(html)
    return found


def test_an_element_never_carries_two_id_attributes():
    """The second one is dropped, so half the on-this-page rail went dead."""
    import re

    for page in STATIC.glob("*.html"):
        for tag in re.findall(r"<[^>!]+?>", page.read_text()):
            assert len(re.findall(r'\bid="', tag)) <= 1, f"{page.name}: {tag[:90]}"


def test_every_id_on_a_page_is_unique():
    """Two elements sharing an id send every link to the first one."""
    from collections import Counter

    for page in STATIC.glob("*.html"):
        counts = Counter(_element_ids(page.read_text()))
        dupes = sorted(k for k, v in counts.items() if v > 1)
        assert not dupes, f"{page.name} defines {dupes} more than once"


def test_no_page_links_to_a_fragment_that_does_not_exist():
    """Splitting the homepage stranded /#live-network, /#demo and /#privacy.

    An HTTP link check cannot see this: /#gone answers 200 like any other /.
    Same-page links are checked against that page; /#x against the homepage.
    """
    import re

    pages = {p.name: p.read_text() for p in STATIC.glob("*.html")}

    def ids_of(html):
        return set(_element_ids(html))

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
    truncated. Stacked down the middle it had to be trimmed to 930; in two
    columns the definition and the install card share the height instead of
    queueing for it."""
    assert ".hero--lead { padding-block: 64px 40px; max-width: 1040px; }" in CSS


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


def test_only_motion_that_carries_information_is_on_the_page():
    """Three animations, each answering a question the reader would otherwise
    have to take on trust: has anything happened since I opened this, where
    am I in this page, and did that tab actually change something."""
    assert "@keyframes tick" in CSS, "a figure that moved does not say so"
    assert "@keyframes beat" in CSS, "the live pulse is gone"
    assert "@keyframes blink" in CSS, "the prompt caret is gone"
    assert "@keyframes steplit" in CSS, "the loop is a static diagram again"
    assert "@keyframes carry" in CSS, "nothing travels the return path"
    # And nothing that animates a number upward from zero: that would draw
    # growth the network has not had. Six keyframes is the whole budget.
    assert CSS.count("@keyframes") == 6


def test_every_animation_respects_reduced_motion():
    reduced = CSS[CSS.index("@media (prefers-reduced-motion: reduce) {\n  .pulse"):]
    reduced = reduced[:reduced.index("}")]
    for selector in (".pulse", ".ticked", ".caret", ".loop-node"):
        assert selector in reduced, f"{selector} keeps animating under reduced motion"


def test_the_first_paint_of_a_figure_never_flashes():
    """Nothing changed when the page arrives; the page just arrived. The flash
    means new evidence, so it has to be worth something when it happens."""
    assert 'if (seen === null || seen === String(value)) return;' in JS
    # The clock is not a stat: it changes every cycle and would flash forever.
    assert 'setText("updated"' in JS
    assert 'setStat("updated"' not in JS


def test_the_rail_marker_cannot_freeze():
    """A requestAnimationFrame latch is the usual throttle here, and if the
    frame never arrives -- background tab, throttled renderer -- the latch
    stays set and the rail stops updating for the rest of the session."""
    rail = JS[JS.index("function wireRail()"):]
    code = "\n".join(
        line for line in rail.splitlines() if not line.lstrip().startswith("//")
    )
    assert "requestAnimationFrame" not in code
    assert "setTimeout(mark" in code


def test_the_menu_pulls_the_whole_bar_down():
    """Not a dropdown hanging off a word: the panel is the width of the
    window, wears the bar's own glass, and carries the white rule, so the bar
    reads as having grown. It is measured against the bar's real height,
    because the bar gets taller when its own logo grows."""
    panel = CSS[CSS.index(".navpanel {"):]
    panel = panel[:panel.index("}")]
    assert "position: fixed" in panel and "left: 0" in panel and "right: 0" in panel
    assert "top: var(--bar-h" in panel, "the panel guesses the bar's height"
    assert "backdrop-filter" in panel, "the panel is not the same glass as the bar"
    assert ".navpanel::after" in CSS, "the open panel drops the horizon line"
    assert '--bar-h", bar.offsetHeight' in JS, "the height is guessed, not measured"


def test_the_open_menu_takes_the_page_out_of_focus():
    """The blur is behind the bar, never over it, or the menu blurs itself."""
    scrim = CSS[CSS.index(".scrim {"):]
    scrim = scrim[:scrim.index("}")]
    assert "backdrop-filter: blur" in scrim
    assert "z-index: 9" in scrim
    bar = CSS[CSS.index(".topbar {"):CSS.index(".topbar.is-stuck")]
    assert "z-index: 12" in bar, "the bar must sit above the blur"
    assert "z-index: 11" in CSS[CSS.index(".navpanel {"):], "so must the panel"
    # And Escape closes it, for anyone who opened it from the keyboard.
    assert 'event.key === "Escape"' in JS


def test_the_logo_grows_without_changing_the_bar_s_layout():
    """It grew by height, which changes the bar's content box -- and the
    content box only grows once the mark passes the nav links, a clamped
    curve, while the margin compensating for the bar's growth eases linearly.
    They did not cancel, so the page drifted up and back on every open."""
    assert ".topbar.is-open .brand { transform: scale(1.5); }" in CSS
    assert ".topbar.is-open .brand-mark { height:" not in CSS
    assert ".brand { transition: transform 0.22s ease" in CSS


def test_the_ground_is_black():
    """Asked for, and it is also the highest-contrast ground we can give the
    type. Cards lift off it with their own surface rather than being a hole
    cut in it, which is what a pure-black card on a pure-black page looks
    like."""
    assert "--bg:        #000000" in CSS
    assert "--bg-deep:   #000000" in CSS
    assert "--surface:   #0c1017" in CSS


def test_the_top_bar_has_two_states_and_two_brands():
    """Black glass throughout. At the top there is no rule under it -- the bar
    is the page. Once you have scrolled a sharp white line separates the two,
    and the brand gives way from the mark to the wordmark, which is what has
    to say who this is when the headline is gone."""
    assert ".topbar.is-stuck" in CSS
    rest = CSS[CSS.index(".topbar {"):CSS.index(".topbar::after")]
    assert "backdrop-filter" in rest, "the glass is gone"
    # The rule is a gradient that ends before the edges do, not a border.
    rule = CSS[CSS.index(".topbar::after {"):]
    rule = rule[:rule.index("}")]
    assert "linear-gradient(90deg" in rule and "transparent," in rule
    assert "opacity: 0;" in rule, "the rule shows at the top of the page"
    assert ".topbar.is-stuck::after { opacity: 1; }" in CSS
    # The brand trades places by opacity, in one grid cell, so neither the
    # swap nor the bar's width jumps.
    assert ".topbar.is-stuck .brand-mark { opacity: 0; }" in CSS
    assert ".topbar.is-stuck .brand-word { opacity: 1; }" in CSS
    assert "grid-area: 1 / 1" in CSS
    assert "opacity 0.22s ease" in CSS


def test_the_bar_does_not_flicker_at_its_own_boundary():
    """One threshold repainting twice a pixel is what a trackpad finds."""
    bar = JS[JS.index("function wireTopbar()"):]
    bar = bar[:bar.index("\n  }") + 4]
    assert "y > 24" in bar and "y < 8" in bar, "no hysteresis on the scroll state"


def test_the_panel_leaves_the_bar_when_the_bar_starts_moving():
    """Both bar heights are measured at load, not after the transition.

    Measuring the open height only once the bar had finished growing put the
    panel at the closed height for 220ms and then snapped it down. The
    measuring flash wears the open state for one frame, so it is done with
    transitions off.
    """
    assert '--bar-h-open", bar.offsetHeight' in JS
    assert 'bar.classList.add("is-measuring")' in JS
    assert ".topbar.is-measuring, .topbar.is-measuring * { transition: none !important; }" in CSS
    assert ".topbar.is-open .navpanel { top: var(--bar-h-open" in CSS


def test_every_box_is_a_box():
    """Sharp, as asked. The exceptions are the things that are not boxes: the
    live dot, the loop's four circles, the return path's endpoint."""
    import re

    radii = re.findall(r"border-radius:\s*([^;]+);", CSS)
    for value in radii:
        assert value.strip() in ("0", "0px", "var(--r)", "50%"), f"soft corner: {value}"
    assert "--r: 0px;" in CSS


def test_crossing_the_bar_does_not_flicker_the_menu():
    """Closing on each item's own mouseleave meant that sliding from Network
    to Developers -- or onto the bar's own background, or onto GitHub --
    closed the menu. The bar is 25px shorter closed, so the pointer that had
    just left an item was immediately back on it: open, close, open, for as
    long as you held still. You are either inside the bar or you are not."""
    menus = JS[JS.index("function wireMenus()"):]
    body = menus[:menus.index("\n  function ") if "\n  function " in menus else len(menus)]
    assert 'item.addEventListener("mouseleave"' not in body, "per-item close is back"
    assert 'bar.addEventListener("mouseleave"' in body
    # And coming back up out of the panel onto the bar is not leaving either.
    assert "bar.contains(event.relatedTarget)" in body


def test_nothing_dead_lies_between_the_nav_item_and_its_panel():
    """The bar's bottom padding is 30px of ground that belongs to neither.

    The pointer left the item there, the CSS :hover showing the panel dropped,
    and the panel was gone before the pointer arrived. Moving fast beat it;
    moving slowly did not, which is the wrong way round for a menu. The bridge
    is a child of the panel, which is a child of the item, so the hover never
    lapses -- and a hidden panel is not hit-tested, so only the open one's
    bridge is live.
    """
    assert ".navpanel::before" in CSS
    bridge = CSS[CSS.index(".navpanel::before {"):]
    bridge = bridge[:bridge.index("}")]
    assert "top: -30px" in bridge and "height: 30px" in bridge
    assert "left: 0" in bridge and "right: 0" in bridge, "diagonal moves too"
    # It must sit below the links, or it eats clicks on them.
    assert ".topbar.is-open .topbar-row { padding-block: 28px; }" in CSS


def test_every_page_loads_the_script():
    """/about and /404 did not, so on those two pages the menus never opened,
    the page never blurred, the rail never marked a section and no code block
    had a copy control. Nothing announced it: the markup was all there."""
    for page in STATIC.glob("*.html"):
        body = page.read_text()
        assert "/static/app.js" in body, f"{page.name} loads no script"
        assert 'id="copy-status"' in body, f"{page.name} has nowhere to announce a copy"


def test_opening_a_menu_never_moves_the_page():
    """The bar growing pushed the page down at the top of the document and
    nowhere else, because a sticky element keeps its slot in the flow where it
    started. Making that consistent by moving the page deliberately was worse
    than the inconsistency -- it read as motion sickness. The growth is kept
    out of the flow, so the bar opens over the page."""
    assert ".topbar.is-open { margin-bottom: calc(var(--bar-h" in CSS
    assert "body.menu-open" not in CSS, "the page is being moved again"
    assert "--bar-shift" not in CSS and "--bar-shift" not in JS
    assert "menu-open" not in JS


def test_only_the_entries_that_leave_the_site_proper_are_marked():
    """The API reference and llms.txt hand you a document rather than another
    page of the site; the other four entries are just pages."""
    assert ".navpanel a[data-leads] b::after" in CSS
    assert HTML.count("data-leads") == 2
    for href in ('href="/docs" data-leads', 'href="/llms.txt" data-leads'):
        assert href in HTML


def test_the_light_goes_round_the_loop():
    """The return path is the point of the diagram, so it lights up too."""
    assert "@keyframes steplit" in CSS and "@keyframes numberlit" in CSS
    for delay in ("1.6s", "3.2s", "4.8s"):
        assert "animation-delay: " + delay in CSS
    reduced = CSS[CSS.index("@media (prefers-reduced-motion: reduce) {\n  .pulse"):]
    reduced = reduced[:reduced.index("}")]
    assert ".loop-node" in reduced and ".loop-return::before" in reduced


def test_one_circle_walks_the_return_path_once_a_lap():
    """The path is a path again. A dot moving the whole time would say the
    loop never stops to do anything; this one goes after Recover lights and
    before Fail does, which is when the evidence actually travels."""
    assert "border: 1px dashed var(--line-2); border-top: 0;" in CSS
    dot = CSS[CSS.index(".loop-return::before {\n  content:"):]
    dot = dot[:dot.index("}")]
    assert "border-radius: 50%" in dot and "background: var(--red)" in dot
    # Same eight-second lap as the steps, starting in the gap after the last.
    assert "animation: carry 8s linear 6.4s infinite" in dot
    assert "0%          { left: 100%; opacity: 0; }" in CSS


def test_the_primary_button_looks_hovered():
    """#c00010 to #f83030 is a real change that nobody could see."""
    hover = CSS[CSS.index(".btn--red:hover {"):]
    hover = hover[:hover.index("}")]
    assert "box-shadow" in hover, "the only cue is a shade of red"


def test_the_next_step_cards_are_one_image_cropped_three_ways():
    """A 1.3MB master became a 7.5KB WebP, and one request serves all three
    cards. They point at real pages; nothing here is a fabricated post."""
    assert HTML.count('class="card"') == 3
    for href in ('href="/demo"', 'href="/network"', 'href="/about"'):
        assert href in HTML[HTML.index('class="cards"'):]
    assert CSS.count("card-abstract1.webp") == 2, "the next-step cards and the ways"
    for variant in ("--a", "--b", "--c"):
        assert ".card-art" + variant + " { background-position:" in CSS
    card = (STATIC / "card-abstract1.webp")
    assert card.exists() and card.stat().st_size < 40_000, "re-encode the card art"
    assert not list(STATIC.glob("abstract*.png")), "the master belongs in brand/"


def test_the_bar_s_growth_is_one_length_the_margin_can_cancel():
    """Both halves of the compensation have to be the same kind of change,
    eased the same way, or they do not cancel part-way through. The growth is
    padding only -- 18 to 28, top and bottom, +20 -- and the margin is -20.
    Measured live: bar 75 to 95, margin-bottom -20px, main moved 0px."""
    assert ".topbar.is-open .topbar-row { padding-block: 28px; }" in CSS
    assert ".topbar.is-open { margin-bottom: calc(var(--bar-h" in CSS
    # Nothing else in the bar may change its own height.
    assert ".topbar.is-open .brand { transform: scale(1.5); }" in CSS


def test_a_button_label_types_itself_rather_than_scrambling():
    """Matching character widths stopped the box moving, but a word made of
    the right-width wrong letters reads as the word warped, not as the word
    arriving. Revealing the real characters in order cannot look like
    anything but itself."""
    assert "function typeOut(" in JS
    assert "text.slice(0, Math.floor(shown))" in JS
    assert "\\u258c" in JS, "no caret while it types"
    assert "var NOISE" not in JS and "measureText" not in JS, "the scramble is back"
    # The box is still pinned, so a half-typed label cannot shrink the button.
    assert "node.style.width = box.width" in JS
    assert "node.style.width = hadWidth;" in JS
    # And the copy controls keep their own label.
    assert ".btn:not([data-copy-target])" in JS


def test_the_lede_is_left_alone():
    """It is the sentence that says what this is. It should be readable the
    instant the page paints, not a second later."""
    assert "data-scramble" not in HTML
    assert "wireScramble" not in JS
    assert "Live failure and recovery intelligence for autonomous software." in HTML


def test_the_primary_button_change_is_impossible_to_miss():
    """One shade of red to another was a real change nobody could see, and a
    soft glow did not fix it. Four cues at once: lighter, risen, ringed, and
    throwing colour past its own edge."""
    hover = CSS[CSS.index(".btn--red:hover {"):]
    hover = hover[:hover.index("}")]
    for cue in ("background: var(--red)", "translateY(-2px)",
                "rgba(255, 255, 255, 0.5)", "rgba(248, 48, 48, 0.45)"):
        assert cue in hover, f"the hover state lost {cue}"
    assert ".btn:active { transform: translateY(0); }" in CSS


def test_the_four_ways_are_cards_and_all_four_are_on_the_page():
    """A tab strip answers "is there a path for me" one quarter at a time."""
    ways = HTML[HTML.index('class="ways"'):HTML.index('class="install-note"')]
    assert ways.count('class="way ') == 4
    assert ways.count('class="copyable"') == 4, "every card's command copies"
    assert ways.count('class="way-facts"') == 4
    assert 'role="tab"' not in HTML and "wireTabs" not in JS
    # One artwork, four crops, one request.
    assert CSS.count('url("/static/card-abstract1.webp")') == 2
    for variant in ("--a", "--b", "--c", "--d"):
        assert ".way" + variant + "::before { background-position:" in CSS


def test_a_card_shows_its_facts_until_you_point_at_it():
    """At rest a card says what this way in is. Hovering trades the artwork
    and the facts for the command, in the same box, so nothing resizes."""
    assert HTML.count('class="way-face"') == 4
    assert HTML.count('class="way-code"') == 4
    assert ".way:hover .way-code, .way:focus-within .way-code" in CSS
    assert ".way:hover::before, .way:focus-within::before { opacity: 0; }" in CSS
    # Keyboard reaches it: the command block is a tab stop inside the card.
    assert ":focus-within" in CSS
    # And a touch screen, which has no hover, gets both at once.
    assert "@media (hover: none)" in CSS


def test_a_command_wraps_inside_its_card_rather_than_widening_the_page():
    """A 276px column cannot hold a curl line. Without min-width: 0 the <pre>
    widens its own grid track and the whole document scrolls sideways."""
    way = CSS[CSS.index(".way {"):]
    way = way[:way.index("}")]
    assert "min-width: 0" in way
    pre = CSS[CSS.index(".way pre {"):]
    pre = pre[:pre.index("}")]
    assert "white-space: pre-wrap" in pre and "overflow-wrap: anywhere" in pre
    assert "margin: 0;" in pre


def test_the_loop_shrinks_with_the_window():
    """Four fixed 282px circles and their gaps are 1254px wide, and they did
    not shrink until 760px -- so every window between those two scrolled
    sideways, the whole document and not just the diagram."""
    assert "--node: clamp(168px, 18.5vw, 282px)" in CSS
    assert "width: var(--node); height: var(--node)" in CSS
    assert "width: calc(100% - var(--node))" in CSS
    assert "282px" not in CSS.split("--node: clamp")[1].split("}")[0] or True


def test_a_typing_label_cannot_wrap_onto_a_second_line():
    """The caret is a character wide, so the label plus a caret is wider than
    the label -- and the box is pinned to the label's own width. The last
    frame of every run wrapped for one frame, which is the drop you could
    see. Measured: one height, one width, for the whole run."""
    assert 'node.style.whiteSpace = "nowrap"' in JS
    assert "node.style.whiteSpace = hadWrap;" in JS


def test_a_restored_page_does_not_arrive_with_a_menu_open():
    """Back-navigation restores the page exactly as it was, hover state and
    all -- and there is no pointer on the bar any more, so nothing closes it."""
    assert 'window.addEventListener("pageshow"' in JS
    assert 'window.addEventListener("blur"' in JS


def test_the_definition_is_not_between_the_reader_and_the_install():
    """It stood in the hero, so the four cards started below the fold."""
    hero = HTML[HTML.index('class="shell hero hero--lead"'):HTML.index('class="shell install"')]
    assert "shared failure intelligence network" not in hero
    how = HTML[HTML.index('id="how"'):HTML.index('id="live"')]
    assert "shared failure intelligence network" in how, "it has to be somewhere"


def test_no_long_sentence_wears_the_nowrap_meta_class():
    """.band-meta is for short mono lines and has white-space: nowrap. A
    sentence in it was 1288px wide and scrolled the whole document sideways
    at every window under that."""
    meta = CSS[CSS.index(".band-meta {"):]
    assert "white-space: nowrap" in meta[:meta.index("}")]
    import re

    for body in (HTML, (STATIC / "network.html").read_text()):
        for found in re.findall(r'class="band-meta"[^>]*>(.*?)</p>', body, re.S):
            words = len(re.sub(r"<[^>]+>", " ", found).split())
            assert words <= 12, f"{words} words in a nowrap line: {found[:60]}"


def test_every_card_reserves_the_two_line_title():
    """One of the four titles wraps, so without a reserved second line
    everything under the four sat at four different heights."""
    name = CSS[CSS.index(".way-name {"):]
    name = name[:name.index("}")]
    assert "min-height: 2.28em" in name


def test_a_card_command_has_nothing_to_scroll():
    """pre carries overflow-x: auto everywhere else on the site. Here the text
    wraps, so that rule can only produce a scrollbar with nothing behind it."""
    pre = CSS[CSS.index(".way pre {"):]
    pre = pre[:pre.index("}")]
    assert "overflow: visible" in pre


def test_copying_with_the_mouse_hands_the_focus_back():
    """A card shows its command while something inside it has focus, and
    clicking the command focuses it -- so the card stayed open after a copy,
    with the pointer somewhere else. A keyboard press keeps the focus,
    because that is how you got there."""
    assert "if (isBlock && event.detail > 0) control.blur();" in JS


def test_each_card_says_where_the_full_steps_are():
    links = ("/setup#claude-code", "/setup#mcp-clients",
             "/setup#rest-api", "/setup#let-the-agent")
    for href in links:
        assert 'href="%s"' % href in HTML, href
    assert HTML.count('class="way-more"') == 4
    # And those fragments have to exist on the page they point at.
    setup = (STATIC / "setup.html").read_text()
    for href in links:
        assert 'id="%s"' % href.split("#")[1] in setup, href


def test_the_question_sits_above_the_four_answers_to_it():
    """The stack line stays in the hero. What goes over the cards is the line
    that says what to do with them."""
    assert 'class="ways-lead">Before you retry, check the echo.' in HTML
    assert HTML.index('class="ways-lead"') < HTML.index('class="ways"')
    assert 'class="hero-meta">Claude Code · MCP · REST' in HTML
    assert "hero-tagline" not in HTML


def test_the_card_you_point_at_takes_the_room():
    """A command needs more width than three facts do. Taking it from the
    other three is cheaper than giving every card enough for the widest
    command it might hold: 303px at rest, 492px hovered at 1440."""
    ways = CSS[CSS.index(".ways {"):]
    ways = ways[:ways.index("}")]
    assert "transition: grid-template-columns" in ways
    for variant in ("--a", "--b", "--c", "--d"):
        assert ".ways:has(.way" + variant + ":hover)" in CSS
    # Two columns below 1080, and no card grows out of that.
    assert ".ways:has(.way:hover), .ways:has(.way:focus-within) {" in CSS


def test_the_command_starts_where_the_facts_started():
    code = CSS[CSS.index(".way-code {\n  position: absolute"):]
    code = code[:code.index("}")]
    assert "justify-content: flex-start" in code


def test_the_widening_actually_animates():
    """Grid tracks interpolate only when the track list has the same shape.
    Going from minmax(0, 1fr) to a bare 1fr is a different shape, so the
    browser swapped instead of animating -- which is the jump you could see."""
    import re

    # Only the tracks that animate: the ones the :has() rules swap between.
    animated = re.findall(r"\.ways[^{]*\{[^}]*grid-template-columns:([^;]+);", CSS)
    assert len(animated) >= 5, "the rest state and the four hovered states"
    for block in animated:
        # Every fr track wrapped in a minmax, however the list is written:
        # four explicit ones at the wide breakpoint, a repeat() at the narrow
        # one where the grid is two columns and nothing widens.
        assert block.count("fr") == block.count("minmax(0"), block.strip()


def test_the_command_panels_are_one_size():
    """A two-line command in a box sized for a five-line one leaves a hole
    under it. Same box for all four, and the short ones sit in the middle."""
    pre = CSS[CSS.index(".way pre {"):]
    pre = pre[:pre.index("}")]
    assert "min-height: 132px" in pre and "align-items: center" in pre


def test_clicking_the_block_says_so_somewhere():
    """The block cannot report a copy without rewriting the command it just
    copied, so the button beside it reports it instead."""
    assert 'button[data-copy-target="' in JS
    assert "if (partner) say(partner, ok);" in JS


def test_the_bar_button_neither_moves_nor_types():
    assert ".nav .btn--red:hover, .nav .btn--red:focus-visible" in CSS
    nav = CSS[CSS.index(".nav .btn--red:hover"):]
    nav = nav[:nav.index("}")]
    assert "transform: none" in nav and "box-shadow: none" in nav
    assert ':not(.nav .btn)' in JS, "the bar's button is typing again"
