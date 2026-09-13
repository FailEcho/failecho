"""The /about page: what FailEcho is, for humans and for search engines.

A new brand's biggest problem is that nobody -- person or crawler -- knows what
the name refers to. This page is the canonical answer, so the tests check that
the definition, the network-effect explanation and the privacy contract are
actually present and correctly linked, not that every sentence matches.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import settings

ABOUT = (Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "about.html").read_text()


def test_about_is_served(client):
    response = client.get("/about")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_it_says_what_failecho_is(client):
    body = client.get("/about").text
    assert "<h1" in body and "About FailEcho" in body
    assert (
        "FailEcho is a live failure intelligence network for AI agents and"
        in body
    )
    assert "Model Context Protocol (MCP)" in body


def test_the_network_effect_is_explained(client):
    body = client.get("/about").text
    assert "How the network works" in body
    assert "Agent B benefits from evidence it never generated itself." in body


def test_privacy_contract_is_stated(client):
    body = client.get("/about").text
    assert "Privacy by design" in body
    for refused in ("prompts", "API keys", "tool arguments", "tool results", "secrets"):
        assert f"<li>{refused}</li>" in body
    assert "Raw error text is normalized and discarded" in body
    assert "hashed before storage" in body


def test_intelligence_is_described_as_evidence(client):
    body = client.get("/about").text
    assert "Evidence, not generated advice" in body
    assert "does not use a language model to invent recovery" in body
    assert "INSUFFICIENT_DATA" in body


def test_page_metadata(client):
    body = client.get("/about").text
    assert "<title>About FailEcho — Failure Intelligence for AI Agents</title>" in body
    description = re.search(r'<meta name="description" content="([^"]+)"', body)
    assert description and "AI agents and autonomous software" in description.group(1)
    for tag in ('property="og:title"', 'property="og:description"',
                'property="og:url"', 'rel="canonical"'):
        assert tag in body
    assert "noindex" not in body.lower()


def test_canonical_uses_the_public_origin(client):
    object.__setattr__(settings, "public_url", "https://failecho.com")
    try:
        body = client.get("/about").text
        assert '<link rel="canonical" href="https://failecho.com/about">' in body
        assert 'content="https://failecho.com/about"' in body
        for leak in ("localhost", "127.0.0.1", "testserver"):
            assert leak not in body, f"{leak} leaked into production HTML"
    finally:
        object.__setattr__(settings, "public_url", "http://localhost:8000")


def test_sitemap_includes_about(client):
    from xml.etree import ElementTree

    root = ElementTree.fromstring(client.get("/sitemap.xml").text)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [el.text for el in root.findall(".//s:loc", ns)]
    assert any(loc.endswith("/about") for loc in locs), locs


def test_it_links_to_the_rest_of_the_site(client):
    body = client.get("/about").text
    for href in ('href="/"', 'href="/docs"', 'href="/llms.txt"',
                 'href="/openapi.json"', 'mailto:contact@failecho.com'):
        assert href in body, href
    assert "/mcp" in body


def test_homepage_and_footer_link_to_about(client):
    home = client.get("/").text
    assert home.count('href="/about"') >= 2, "navigation and footer at minimum"
    assert 'href="/about"' in client.get("/about").text


def test_no_invented_company_details():
    """An about page is where fake credibility usually creeps in."""
    lowered = ABOUT.lower()
    for invented in ("founded in", "our team", "headquarters", "customers trust",
                     "trusted by", "employees", "series a", "testimonial"):
        assert invented not in lowered, invented


def test_brand_spelling_is_consistent():
    for wrong in ("Fail Echo", "FAILECHO", "failEcho"):
        assert wrong not in ABOUT, wrong
    assert ABOUT.count("AI agents") >= 3


def test_the_privacy_section_has_a_linkable_anchor(client):
    """It is the URL given to directories as the privacy policy, so it is a
    stable address rather than a heading id that could be renamed."""
    body = client.get("/about").text
    assert 'id="privacy"' in body
    assert "Privacy by design" in body


def test_the_benefits_are_split_by_when_they_are_true(client):
    """Half of what FailEcho gives you needs other people and half does not.
    Listing them together would promise a cold-start user things the empty
    network cannot do."""
    body = client.get("/about").text
    section = body[body.index('id="what-you-get"'):]
    assert "On day one, with nobody else connected" in section
    assert "Once other agents are reporting" in section
    # The day-one list has to come first: it is the half that is true now.
    assert section.index("On day one") < section.index("Once other agents")
    # And the limits are stated in the same breath, not on another page.
    assert "no use at all for a failure only your stack can produce" in section
