"""The /setup page: the answer to "I looked at the site and could not start".

The homepage sells; this page has one job, which is that a stranger with an
agent gets connected. So the tests check that every route in is present, that
each one says where to type things, and that the promises about data stay
attached to the instructions rather than living only on another page.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETUP = (ROOT / "app" / "web" / "static" / "setup.html").read_text()


def test_setup_is_served(client):
    response = client.get("/setup")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<h1" in response.text and "Set up FailEcho" in response.text


def test_it_covers_every_way_in(client):
    body = client.get("/setup").text
    # Claude Code, with and without the plugin.
    assert "/plugin marketplace add FailEcho/failecho" in body
    assert "/plugin install failecho@failecho" in body
    assert "failecho_hook.py" in body
    assert '"PostToolUseFailure"' in body
    # Any other MCP client, and no client at all.
    assert '"mcpServers"' in body
    assert "/v1/query" in body
    for tool in ("check_tool_failure", "report_tool_failure",
                 "report_tool_success", "report_recovery_outcome"):
        assert tool in body


def test_it_says_where_to_type_the_commands(client):
    """The gap that sent someone away from the homepage empty-handed."""
    body = client.get("/setup").text
    assert "at its prompt" in body and "not in a terminal" in body
    assert "/reload-plugins" in body
    assert "should be listed as <em>enabled</em>" in body
    assert "paste the endpoint into the client's mcp settings" in body.lower()


def test_it_says_how_to_know_it_worked(client):
    body = client.get("/setup").text
    assert "How to tell it is working" in body
    assert "INSUFFICIENT_DATA" in body
    assert "/v1/stats" in body
    assert "evidence_sources" in body


def test_the_data_promise_travels_with_the_instructions(client):
    body = client.get("/setup").text
    assert "What leaves your machine" in body
    for refused in ("prompts", "tool arguments", "tool results"):
        assert f"<li>{refused}</li>" in body
    assert "FAILECHO_HOOK_SEND_ERRORS=1" in body


def test_it_answers_the_obvious_failures(client):
    body = client.get("/setup").text
    assert "If nothing shows up" in body
    # The four causes, by what each one tells the reader to do -- not by the
    # sentence it is currently phrased in.
    assert "Start a new session" in body          # hooks load at session start
    assert "FAILECHO_HOOK_SERVICE_NAMES" in body  # local servers are skipped
    assert "/v1/observe" in body                  # non-MCP failures go direct
    assert "FAILECHO_DISABLED=1" in body          # and how to stop entirely
    assert "nothing is stored about you" in body


def test_page_metadata_and_no_stray_tokens(client):
    body = client.get("/setup").text
    assert "<title>Set up FailEcho — Failure Intelligence for AI Agents</title>" in body
    assert re.search(r'<link rel="canonical" href="http[^"]+/setup">', body)
    assert '<meta property="og:url" content="http' in body
    assert "{{" not in body, "a token reached the page"


def test_the_site_leads_here(client):
    for page in ("/", "/about"):
        body = client.get(page).text
        assert 'href="/setup"' in body, page
    assert "/setup" in client.get("/sitemap.xml").text
    # Agents read llms.txt, and some of them are pointing a human at the setup.
    assert "/setup" in client.get("/llms.txt").text


def test_the_commands_match_what_this_repository_publishes():
    """A rename must not leave the setup page teaching an install that fails."""
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    repository = json.loads((ROOT / "server.json").read_text())["repository"]["url"]
    slug = "/".join(repository.rstrip("/").split("/")[-2:])

    assert f"/plugin marketplace add {slug}" in SETUP
    assert f"/plugin install {marketplace['plugins'][0]['name']}@{marketplace['name']}" in SETUP


def test_the_page_is_complete_without_javascript():
    """The instructions are text. The only script adds copy buttons to the
    code boxes, and the page reads the same if it never runs."""
    assert SETUP.count("<script") == 1
    assert "app.js" in SETUP
    assert "<script>" not in SETUP, "no inline script"
    for snippet in ("/plugin marketplace add", "mcpServers", "curl -X POST"):
        assert snippet in SETUP, snippet


def test_the_directory_listings_are_named_without_claiming_use(client):
    """Being in a directory is a fact. It is not adoption, and the page says so."""
    body = client.get("/setup").text
    for url in ("registry.modelcontextprotocol.io", "glama.ai", "smithery.ai"):
        assert url in body, url
    assert "com.failecho/failecho" in body
    assert "not a measure of use" in body
    # no third-party badge images: those are a request per visit, and a
    # tracking vector, on a site that carries no trackers at all
    assert "img.shields.io" not in body
    assert "badge" not in body.lower()


def test_the_write_path_is_documented_not_just_the_read(client):
    """Recovery outcomes are the scarcest data in the network, and /setup used
    to show only /v1/query -- so the one call that records what actually fixed
    a failure was reachable only by reading the OpenAPI schema or guessing
    field names and collecting a 422."""
    body = client.get("/setup").text
    assert "/v1/observe" in body and "/v1/outcome" in body
    for field in ('"fingerprint"', '"action"', '"successful"'):
        assert field in body, field
    assert '"outcome": "failure"' in body


def test_a_local_process_only_client_has_two_paths(client):
    """Hosts that can only spawn a process cannot use the HTTP endpoint, and
    had nothing until these. Both were run end to end against production
    before being written down: `uvx failecho-mcp` from PyPI, and
    `npx -y mcp-remote https://failecho.com/mcp`, each initializing, listing
    all four tools and relaying a live call."""
    body = client.get("/setup").text
    assert '"uvx"' in body and '"npx"' in body
    assert body.count('"failecho-mcp"') >= 2, "the PyPI and the npm relay"
    assert "Both are ours" in body


def test_the_page_offers_to_let_the_agent_do_it(client):
    """The shortest path on a page aimed at people who run agents all day."""
    body = client.get("/setup").text
    assert "Let your agent set it up" in body
    assert "set yourself up to use FailEcho" in body
    assert 'href="/llms.txt"' in body


def test_a_broken_install_has_somewhere_to_go(client):
    """A quiet failure and nobody trying look identical from the server, so
    the troubleshooting section has to end in a person rather than a shrug."""
    body = client.get("/setup").text
    trouble = body[body.index("If nothing shows up"):]
    assert "mailto:contact@failecho.com" in trouble
    assert "mailto:security@failecho.com" in trouble
    assert "that is a bug and I want to hear about it" in body


def test_the_jump_nav_comes_before_the_sections_it_jumps_to():
    """It is a table of contents. Below the first section it is furniture."""
    body = SETUP
    hero_end = body.index("</section>", body.index('class="shell hero'))
    nav = body.index('class="shell setup-jump"')
    first_section = body.index('id="let-the-agent"')
    assert hero_end < nav < first_section, "the nav has drifted below a section"


def test_the_long_pages_have_an_on_this_page_rail():
    """Both grew past ten sections, which is more than anyone scrolls through
    hoping. Every section the rail lists must exist, or the rail is furniture
    that scrolls to nowhere."""
    import re
    from pathlib import Path

    static = Path(__file__).resolve().parents[1] / "app" / "web" / "static"
    for page in ("setup.html", "about.html"):
        html = (static / page).read_text()
        assert 'class="pagenav"' in html, f"{page} has no rail"
        rail = html[html.index('class="pagenav"'):html.index('class="pagemain"')]
        targets = re.findall(r'href="#([^"]+)"', rail)
        assert len(targets) >= 10, f"{page} rail lists only {len(targets)}"
        from tests.test_frontend import _element_ids

        ids = set(_element_ids(html))
        for t in targets:
            assert t in ids, f"{page} rail points at #{t}, which does not exist"


def test_both_navigations_are_present_and_answer_different_questions():
    """The chips are the four entry points and sit under the hero on every
    width. The rail is every section and needs a wide screen, so it is the
    one that disappears."""
    css = (Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "style.css").read_text()
    assert "@media (min-width: 1101px) { .setup-jump { display: none; } }" not in css
    assert ".pagenav { display: none; }" in css
    for page in ("setup.html", "about.html"):
        html = (Path(__file__).resolve().parents[1] / "app" / "web" / "static" / page).read_text()
        chips = html[html.index('class="shell setup-jump"'):]
        chips = chips[:chips.index("</nav>")]
        assert chips.count('href="#') == 4, f"{page} chip nav is not four chips"
