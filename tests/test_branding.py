"""FailEcho brand surfaces: name, logo, domain configuration.

Branding is a public contract too. These tests guard the surfaces a launch
depends on -- and, just as importantly, guard the things that must NOT be
renamed: endpoint paths, MCP tool names and response fields.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import settings
from app.main import public_base_url, render_homepage
from tests.conftest import mcp_call

STATIC = Path(__file__).resolve().parents[1] / "app" / "web" / "static"


# ---------------------------------------------------------------------------
# name
# ---------------------------------------------------------------------------


def test_openapi_is_branded_failecho(client):
    info = client.get("/openapi.json").json()["info"]
    assert info["title"] == "FailEcho API"
    assert info["version"] == "0.1.0"
    assert info["license"]["name"] == "MIT"
    assert "Before you retry, check the echo." in info["description"]
    # No invented legal entity or address.
    assert "address" not in str(info).lower()


def test_docs_page_carries_the_brand(client):
    assert "FailEcho API" in client.get("/docs").text


def test_llms_txt_is_branded_and_leads_with_the_trigger(client):
    body = client.get("/llms.txt").text
    assert body.startswith("# FailEcho")
    assert "Use FailEcho when a tool, API, or MCP operation fails" in body
    assert "for AI agents and autonomous software" in body
    assert "Canonical site" in body
    assert "Before retrying blindly" in body
    for term in ("Failure Echo", "Recovery Echo", "Reporter", "Fingerprint"):
        assert term in body
    assert "Do not send prompts" in body
    assert "tool arguments" in body and "user content" in body


def test_mcp_server_identity(client):
    from app.mcp_server import mcp_server

    assert mcp_server.name == "failecho"
    assert mcp_server.title == "FailEcho"
    assert "FailEcho provides live cross-agent failure intelligence" in (
        mcp_server.instructions or ""
    )
    assert "before retrying blindly" in (mcp_server.instructions or "").lower()


def test_mcp_tool_description_carries_the_trigger_sentence(client):
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"accept": "application/json, text/event-stream"},
    )
    tools = {t["name"]: t for t in response.json()["result"]["tools"]}
    check = tools["check_tool_failure"]["description"]
    assert check.startswith(
        "Use FailEcho when another tool fails, before retrying blindly."
    )


def test_homepage_uses_the_wordmark_correctly(client):
    body = client.get("/").text
    assert "FailEcho" in body
    for wrong in ("FAILECHO", "Fail Echo", "failEcho"):
        assert wrong not in body
    assert "<title>FailEcho — Failure Intelligence for AI Agents</title>" in body


def test_no_stale_product_name_on_public_surfaces(client):
    stale = "Agent Failure Intelligence Network"
    assert stale not in client.get("/").text
    assert stale not in client.get("/llms.txt").text
    assert stale not in str(client.get("/openapi.json").json()["info"])


# ---------------------------------------------------------------------------
# logo and metadata
# ---------------------------------------------------------------------------


BRAND_ASSETS = {
    "logo.png": "image/png",
    "favicon.png": "image/png",
    "wordmark-light.png": "image/png",
    "wordmark-dark.png": "image/png",
    "og-image.png": "image/png",
}


@pytest.mark.parametrize("asset,media_type", sorted(BRAND_ASSETS.items()))
def test_brand_assets_are_served(client, asset, media_type):
    response = client.get(f"/static/{asset}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(media_type)


def test_brand_assets_are_small_enough_for_a_status_page():
    """Raster brand assets are allowed, but not at any price."""
    sizes = {name: (STATIC / name).stat().st_size for name in BRAND_ASSETS}
    assert sizes["favicon.png"] < 8_000
    assert sizes["logo.png"] < 20_000
    # Only one wordmark variant is ever downloaded by a given visitor.
    assert max(sizes["wordmark-light.png"], sizes["wordmark-dark.png"]) < 40_000
    assert sizes["og-image.png"] < 120_000  # never loaded by the page itself


def test_wordmark_ships_a_variant_for_each_theme():
    """The supplied master is white: unusable on the light ground alone."""
    from PIL import Image

    light = Image.open(STATIC / "wordmark-light.png").convert("RGBA")
    dark = Image.open(STATIC / "wordmark-dark.png").convert("RGBA")
    assert light.size == dark.size

    def mean_glyph_luma(image):
        pixels = [
            (r, g, b)
            for r, g, b, a in image.getdata()
            if a > 200 and max(r, g, b) - min(r, g, b) < 40  # neutral glyph pixels
        ]
        assert pixels, "no glyph pixels found"
        return sum(sum(p) / 3 for p in pixels) / len(pixels)

    assert mean_glyph_luma(dark) > 200, "dark-theme wordmark should be near-white"
    assert mean_glyph_luma(light) < 90, "light-theme wordmark should be near-ink"


def test_the_wordmark_matches_the_ground_it_sits_on(client):
    """The bar is black glass at both ends of the scroll, so the white-ink
    master is the one it wears, and so does the footer. Getting this the wrong
    way round makes the mark invisible rather than merely wrong."""
    body = client.get("/").text
    assert body.count("/static/wordmark-dark.png?v=") == 2, "the bar and the footer"
    assert "/static/wordmark-light.png?v=" not in body, "dark ink on a black bar"
    # The brand is the mark alone until you scroll past the headline.
    assert body.count("/static/logo.png?v=") >= 1
    assert 'alt="FailEcho"' in body
    # The light master still ships: the social card and any light context.
    assert (STATIC / "wordmark-light.png").exists()


def test_favicon_is_square_for_search_results():
    """Google shows no favicon at all unless it is square and >= 48px.

    Regression: shipped 72x64, so search results had a blank icon.
    """
    from PIL import Image

    icon = Image.open(STATIC / "favicon.png")
    assert icon.width == icon.height, f"not square: {icon.size}"
    assert icon.width >= 48
    assert icon.width % 48 == 0, "Google prefers a multiple of 48px"


def test_favicon_is_reachable_at_the_legacy_path(client):
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")


def test_page_metadata_is_complete(client):
    body = client.get("/").text
    for tag in (
        'rel="canonical"',
        'property="og:title"',
        'property="og:description"',
        'property="og:url"',
        'property="og:image"',
        'name="theme-color"',
        'rel="icon"',
    ):
        assert tag in body


# ---------------------------------------------------------------------------
# public URL configuration
# ---------------------------------------------------------------------------


def test_public_url_defaults_to_the_requesting_origin(client):
    """Unconfigured: the page and llms.txt describe wherever they are served."""
    assert settings.base_url() == "http://localhost:8000"
    assert "{{PUBLIC_URL}}" not in client.get("/").text
    assert "http://testserver/mcp" in client.get("/setup").text
    assert "http://testserver/mcp" in client.get("/llms.txt").text


def test_configured_public_url_wins_everywhere(client):
    object.__setattr__(settings, "public_url", "https://failecho.com/")
    try:
        assert public_base_url() == "https://failecho.com"
        body = client.get("/").text
        assert 'href="https://failecho.com/"' in body       # canonical
        assert "testserver" not in body
        setup = client.get("/setup").text                   # endpoint + curl
        assert "https://failecho.com/mcp" in setup
        assert "https://failecho.com/v1/query" in setup
        assert "https://failecho.com/mcp" in client.get("/llms.txt").text
    finally:
        object.__setattr__(settings, "public_url", "http://localhost:8000")


def test_the_production_origin_is_never_hardcoded_in_code():
    """One configuration source for the origin.

    Every URL must come from FIN_PUBLIC_URL. Email addresses are exempt: an
    address is not an origin, does not change between environments, and cannot
    be derived from the public URL -- making it configurable would add a knob
    nobody would ever turn.
    """
    import re

    root = Path(__file__).resolve().parents[1]
    checked = list((root / "app").rglob("*.py")) + list(
        (root / "app" / "web" / "static").glob("*")
    )
    # A hardcoded URL: the domain NOT preceded by an "@" (which would make it
    # an email address).
    hardcoded_url = re.compile(r"(?<!@)\bfailecho\.(com|dev)\b")
    for path in checked:
        if path.suffix in (".py", ".html", ".css", ".js"):
            found = hardcoded_url.findall(path.read_text())
            assert not found, f"{path} hardcodes the origin instead of FIN_PUBLIC_URL"


def test_contact_addresses_are_published_consistently(client):
    """The three real addresses, each in the one place it belongs."""
    body = client.get("/").text
    assert "mailto:contact@failecho.com" in body
    assert "mailto:security@failecho.com" in body

    contact = client.get("/openapi.json").json()["info"]["contact"]
    assert contact["email"] == "support@failecho.com"

    security = (Path(__file__).resolve().parents[1] / "SECURITY.md").read_text()
    assert "security@failecho.com" in security
    assert "no security email address yet" not in security.lower()


def test_github_link_is_omitted_until_configured(client):
    body = client.get("/").text
    assert "github.com" not in body, "no invented repository URL"
    assert ">GitHub<" not in body
    assert "{{GITHUB_URL}}" not in body

    rendered = render_homepage("https://failecho.com")
    assert "GitHub" not in rendered

    object.__setattr__(settings, "github_url", "https://github.com/example/failecho")
    try:
        with_link = render_homepage("https://failecho.com")
        assert 'href="https://github.com/example/failecho"' in with_link
        assert "{{GITHUB_URL}}" not in with_link
    finally:
        object.__setattr__(settings, "github_url", "")


# ---------------------------------------------------------------------------
# what branding must NOT touch
# ---------------------------------------------------------------------------


def test_public_endpoints_are_unrenamed(client):
    paths = set(client.get("/openapi.json").json()["paths"])
    assert {
        "/v1/observe",
        "/v1/query",
        "/v1/outcome",
        "/v1/services",
        "/v1/stats",
        "/v1/recovery-intelligence",
        "/health",
    } <= paths
    assert client.get("/health").json() == {"status": "ok"}


def test_mcp_tool_names_are_unrenamed(client):
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"accept": "application/json, text/event-stream"},
    )
    assert {t["name"] for t in response.json()["result"]["tools"]} == {
        "check_tool_failure",
        "report_tool_failure",
        "report_tool_success",
        "report_recovery_outcome",
    }


def test_response_fields_are_unrenamed(client):
    intel = mcp_call(
        client,
        "check_tool_failure",
        {"service": "x", "operation": "y", "error_type": "z"},
    )
    for field in (
        "fingerprint",
        "recommendation",
        "recovery_actions",
        "observations",
        "failure_rate",
        "status",
        "known",
    ):
        assert field in intel


def test_python_client_alias_is_additive():
    """`failecho` is the preferred import; the old name must keep working."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
    import failecho
    import failure_network

    assert failecho.Client is failure_network.Client
    assert failecho.FailEchoError is failure_network.FailureNetworkError


# ---------------------------------------------------------------------------
# production configuration: no localhost may leak into a deployed page
# ---------------------------------------------------------------------------


def test_production_config_leaks_no_localhost(client):
    """With FIN_PUBLIC_URL set, nothing public may still say localhost."""
    object.__setattr__(settings, "public_url", "https://failecho.com")
    try:
        for path in ("/", "/network", "/demo", "/setup", "/llms.txt", "/robots.txt"):
            body = client.get(path).text
            assert "localhost" not in body, f"{path} leaks localhost"
            assert "127.0.0.1" not in body, f"{path} leaks a loopback address"
            assert "testserver" not in body, f"{path} leaks the request origin"

        page = client.get("/setup").text
        for url in (
            "https://failecho.com/",
            "https://failecho.com/mcp",
            "https://failecho.com/v1/query",
        ):
            assert url in page

        llms = client.get("/llms.txt").text
        for url in (
            "https://failecho.com/mcp",
            "https://failecho.com/openapi.json",
            "https://failecho.com/docs",
        ):
            assert url in llms
    finally:
        object.__setattr__(settings, "public_url", "http://localhost:8000")


def test_local_default_stays_local(client):
    """Unconfigured, the page describes the origin it was actually served from."""
    import re

    assert settings.base_url() == "http://localhost:8000"
    body = client.get("/").text
    assert "http://testserver/mcp" in client.get("/setup").text
    # Mailto addresses are fixed and allowed; production URLs are not.
    assert not re.search(r"(?<!@)\bfailecho\.com\b", body)


def test_robots_allows_indexing(client):
    body = client.get("/robots.txt").text
    assert "User-agent: *" in body
    assert "Allow: /" in body
    assert "Disallow: /" not in body
    assert "/llms.txt" in body


def test_llms_txt_states_the_terms(client):
    body = client.get("/llms.txt").text
    assert "No account required." in body
    assert "No API key required." in body
    assert "Free during the public MVP." in body


def test_demo_mode_off_shows_no_demo_banner(client):
    """Production runs FIN_DEMO_MODE=0: no badge, no banner, honest zeros."""
    assert settings.demo_mode is False
    stats = client.get("/v1/stats").json()
    assert stats["demo_mode"] is False
    assert stats["demo_data"] is False
    assert stats["real_observations_24h"] == 0
    assert stats["synthetic_observations"] == 0
    assert stats["demo_agent_observations"] == 0


# ---------------------------------------------------------------------------
# search engine surfaces
# ---------------------------------------------------------------------------


def test_title_leads_with_the_brand(client):
    """"FailEcho" reads as a shell command unless the brand comes first."""
    body = client.get("/").text
    assert "<title>FailEcho — Failure Intelligence for AI Agents</title>" in body


def test_meta_description_states_the_category(client):
    import re

    body = client.get("/").text
    match = re.search(r'<meta name="description" content="([^"]+)"', body)
    assert match, "no meta description"
    description = match.group(1)
    assert 50 <= len(description) <= 320, f"{len(description)} chars"
    assert "FailEcho" in description
    assert "AI agents" in description


def test_brand_entity_sentence_is_visible_html(client):
    """The definition must be readable text, not metadata only."""
    body = client.get("/").text
    assert (
        "FailEcho is a shared failure intelligence network for AI agents and"
        in body
    )
    assert "Connect your agent to shared failure and recovery evidence." in body
    # The protocol is named on the front page, and spelled out on /about.
    assert "MCP" in body
    assert "Model Context Protocol (MCP)" in client.get("/about").text


def test_no_noindex_anywhere(client):
    for path in ("/", "/llms.txt", "/robots.txt", "/docs"):
        response = client.get(path)
        assert "noindex" not in response.text.lower(), path
        assert "noindex" not in response.headers.get("x-robots-tag", "").lower()


def test_robots_txt_points_at_the_sitemap(client):
    body = client.get("/robots.txt").text
    assert "User-agent: *" in body
    assert "Allow: /" in body
    assert "Disallow: /" not in body
    assert "/sitemap.xml" in body


def test_sitemap_is_valid_xml_with_the_indexable_pages(client):
    from xml.etree import ElementTree

    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")

    root = ElementTree.fromstring(response.text)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [el.text for el in root.findall(".//s:loc", ns)]
    assert len(locs) == 7
    for path in ("/", "/network", "/demo", "/about", "/setup", "/docs", "/llms.txt"):
        assert any(loc.endswith(path) for loc in locs), path
    # Real timestamps, not invented priorities.
    assert root.findall(".//s:lastmod", ns)
    assert not root.findall(".//s:priority", ns)
    # API endpoints are for machines, not search results.
    assert not any("/v1/" in loc for loc in locs)


def test_sitemap_uses_the_public_origin(client):
    from xml.etree import ElementTree

    object.__setattr__(settings, "public_url", "https://failecho.com")
    try:
        root = ElementTree.fromstring(client.get("/sitemap.xml").text)
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locs = [el.text for el in root.findall(".//s:loc", ns)]
        assert locs == [
            "https://failecho.com/",
            "https://failecho.com/network",
            "https://failecho.com/demo",
            "https://failecho.com/about",
            "https://failecho.com/setup",
            "https://failecho.com/docs",
            "https://failecho.com/llms.txt",
        ]
        assert "https://failecho.com/sitemap.xml" in client.get("/robots.txt").text
    finally:
        object.__setattr__(settings, "public_url", "http://localhost:8000")


def test_structured_data_is_valid_and_honest(client):
    import json
    import re

    body = client.get("/").text
    blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', body, re.S
    )
    assert len(blocks) == 1
    data = json.loads(blocks[0])

    nodes = {node["@type"]: node for node in data["@graph"]}
    # The FAQ moved to /about, and its schema went with it.
    assert set(nodes) == {"SoftwareApplication", "WebSite"}
    app_node = nodes["SoftwareApplication"]
    assert app_node["name"] == "FailEcho"
    assert app_node["isAccessibleForFree"] is True
    assert app_node["applicationCategory"] == "DeveloperApplication"

    # Nothing invented: no ratings, no company, no pricing, no social accounts.
    forbidden = {
        "aggregateRating", "review", "offers", "foundingDate", "address",
        "numberOfEmployees", "sameAs", "priceRange",
    }
    for node in data["@graph"]:
        assert not forbidden & set(node), f"invented property in {node['@type']}"


def test_faq_schema_matches_the_visible_faq(client):
    import json
    import re

    body = client.get("/about").text
    data = json.loads(
        re.search(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
        .group(1)
    )
    faq = data if data.get("@type") == "FAQPage" else next(
        n for n in data["@graph"] if n["@type"] == "FAQPage"
    )
    assert len(faq["mainEntity"]) == 4, "trimmed to the questions that matter"
    for question in faq["mainEntity"]:
        assert f"<dt>{question['name']}</dt>" in body, question["name"]
        # The answer's first sentence must appear verbatim on the page.
        first_sentence = question["acceptedAnswer"]["text"].split(".")[0]
        assert first_sentence in body, first_sentence


def test_docs_page_carries_canonical_and_description(client):
    body = client.get("/docs").text
    assert "FailEcho API" in body
    assert 'rel="canonical"' in body
    assert 'name="description"' in body
    assert "AI agents" in body


def test_openapi_description_leads_with_ai_agents(client):
    description = client.get("/openapi.json").json()["info"]["description"]
    assert description.lstrip().startswith(
        "FailEcho provides live failure intelligence for AI agents"
    )
    for term in ("Model Context Protocol", "tool failures", "recovery"):
        assert term in description


def test_the_square_icons_are_centred():
    """The mark sits in the middle of every square icon.

    Three bugs have lived here. An off-centre master went straight into the
    favicon, because faint anti-aliasing made the whole canvas count as
    content and the build's trim step did nothing. Then the build tried to
    centre by *visual weight* instead of by outline: the measurement said the
    weight sat high and right, so it pushed the mark down and left, and a
    144px icon ended up with 19px of air above the mark and 4px below. That is
    not a subtle optical balance, it is off-centre, and it is what a search
    result shows.

    A favicon is drawn at 16-32px and often cropped to a circle. The only
    property that survives that is equal margins on the visible bounding box,
    so that is what is checked -- to the pixel, with rounding the only
    tolerance.
    """
    import pytest

    Image = pytest.importorskip("PIL.Image")
    from PIL import ImageChops

    static = Path(__file__).resolve().parents[1] / "app" / "web" / "static"
    for name in ("favicon.png", "apple-touch-icon.png"):
        icon = Image.open(static / name).convert("RGBA")
        width, height = icon.size

        # "Visible" against the dark ground these icons are drawn for, which
        # also handles apple-touch-icon.png being opaque already.
        backdrop = Image.new("RGBA", icon.size, (13, 14, 16, 255))
        flat = Image.alpha_composite(backdrop, icon).convert("RGB")
        visible = ImageChops.difference(flat, backdrop.convert("RGB")).convert("L")
        mask = visible.point(lambda v: 255 if v > 16 else 0)
        box = mask.getbbox()
        assert box, f"{name} has no visible mark"
        left, top, right, bottom = box

        off_x = (left + right) / 2 - width / 2
        off_y = (top + bottom) / 2 - height / 2
        assert abs(off_x) <= 1, f"{name} sits {off_x:+.1f}px off horizontally"
        assert abs(off_y) <= 1, f"{name} sits {off_y:+.1f}px off vertically"

        # And the first bug: nothing jammed against an edge, where a rounded
        # crop would take a bite out of it.
        assert min(left, top, width - right, height - bottom) >= 4, (
            f"{name} touches its edge; a rounded crop would clip it"
        )


def test_the_favicon_link_does_not_move_between_deploys(client):
    """Google fetches the favicon on its own schedule and caches it hard. A
    cache-busted URL changes on every deploy, which gives it a new thing to
    fetch each time rather than a stable one to refresh. The file's ETag and
    a four-hour max-age keep browsers current without it."""
    body = client.get("/").text
    assert '<link rel="icon" href="/static/favicon.png" type="image/png"' in body
    assert "favicon.png?v=" not in body, "the icon URL moves on every deploy"
    assert '<link rel="shortcut icon" href="/favicon.ico">' in body
