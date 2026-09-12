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


def test_the_page_stays_script_free():
    """No polling, no framework: setup instructions are text."""
    assert "<script" not in SETUP
