"""One name for one service.

`service` is part of the fingerprint, so before this existed `github-mcp`,
`mcp-server-github` and `@modelcontextprotocol/server-github` were three
unrelated failures and no two agents naming a server differently ever matched.
The rules are deliberately narrow: mechanical packaging affixes, an explicit
alias list, and nothing clever.
"""

from __future__ import annotations

import pytest

from app.core.aliases import canonical_service
from app.core.fingerprint import compute_fingerprint


@pytest.mark.parametrize(
    "written,canonical",
    [
        ("github-mcp", "github"),
        ("mcp-server-github", "github"),
        ("@modelcontextprotocol/server-github", "github"),
        ("MCP_SERVER_GitHub", "github"),
        ("  github  ", "github"),
        ("gh", "github"),
        ("mcp-server-fetch", "fetch"),
        ("fetch-mcp", "fetch"),
        ("sequentialthinking", "sequential-thinking"),
        ("puppeteer-mcp-server", "puppeteer"),
    ],
)
def test_the_same_server_named_differently_lands_on_one_name(written, canonical):
    assert canonical_service(written) == canonical


@pytest.mark.parametrize(
    "name",
    [
        "api.github.com",
        "hooks.slack.com",
        "my-internal-server",
        "fetch",
        "some-thing-nobody-has-heard-of",
    ],
)
def test_names_that_must_be_left_alone(name):
    """Hostnames are not folded into server names: a REST 429 and an MCP
    server's error are different surfaces, and merging them would file
    evidence under a name the reporter never used. A bare `-server` is a
    name, not a wrapper."""
    assert canonical_service(name) == name


def test_it_is_idempotent():
    for value in ("github-mcp", "mcp-server-fetch", "api.github.com", "gh"):
        once = canonical_service(value)
        assert canonical_service(once) == once


def test_empty_and_missing_are_empty():
    assert canonical_service(None) == ""
    assert canonical_service("   ") == ""


def test_two_agents_naming_it_differently_get_one_fingerprint():
    """The whole point: this is the join key of the network."""
    def fp(service):
        return compute_fingerprint(
            service=service, operation="create_issue",
            error_type="validation_error", error_code="422",
        )

    assert fp("github-mcp") == fp("mcp-server-github") == fp("GitHub")
    assert fp("github") != fp("api.github.com"), "different surfaces stay apart"
