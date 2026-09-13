"""Canonical service names, so two agents naming the same thing agree.

The fingerprint is the join key of the network, and ``service`` is part of it.
Until now that field was taken as written apart from trimming and case
folding, which meant ``github-mcp``, ``mcp-server-github`` and
``@modelcontextprotocol/server-github`` were three unrelated failures. Nobody
ever matched anybody, quietly.

Two things happen here, and the split matters:

* **Packaging affixes are stripped.** ``mcp-server-fetch`` and ``fetch-mcp``
  are the same server wearing two naming conventions. Only affixes that are
  unambiguously packaging get stripped: a bare ``-server`` does not, because
  ``my-internal-server`` is a name, not a wrapper around ``my-internal``.
* **An explicit table handles the rest.** It is a list, not a heuristic, so
  every merge is a decision somebody made and can be argued with.

What is deliberately *not* done: hostnames are never folded into server names.
``api.github.com`` stays itself and does not become ``github``. A rate limit
hit through the REST API and an error from an MCP server wrapping it are
plausibly the same upstream, but they are different surfaces with different
failure modes, and quietly merging them would put evidence under a name the
reporter never used.
"""

from __future__ import annotations

import re

#: Wrappers that carry no meaning: the same server, packaged or named three
#: ways. Order matters -- longest first, so "mcp-server-" wins over "server-".
_PREFIXES = (
    "@modelcontextprotocol/server-",
    "@modelcontextprotocol/",
    "mcp-server-",
    "mcp_server_",
    "modelcontextprotocol/",
    "mcp-",
)

_SUFFIXES = (
    "-mcp-server",
    "_mcp_server",
    "-mcp",
    "_mcp",
)

#: Genuine aliases: different words for one thing. Keep this short, keep every
#: entry justifiable, and never add one to make a demo look better.
_ALIASES = {
    "gh": "github",
    "githubmcp": "github",
    "filesystem-server": "filesystem",
    "fs": "filesystem",
    "postgresql": "postgres",
    "pg": "postgres",
    "gdrive": "google-drive",
    "googledrive": "google-drive",
    "gmaps": "google-maps",
    "seq-thinking": "sequential-thinking",
    "sequentialthinking": "sequential-thinking",
    "bravesearch": "brave-search",
    "puppeteer-mcp-server": "puppeteer",
}

_HOSTLIKE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")
_SEPARATORS = re.compile(r"[\s_]+")
_REPEATED_DASH = re.compile(r"-{2,}")


def canonical_service(value: str | None) -> str:
    """Return the name this service should be recorded and matched under.

    Idempotent: ``canonical_service(canonical_service(x)) == canonical_service(x)``.
    """
    if value is None:
        return ""
    name = str(value).strip().casefold().strip("/")
    if not name:
        return ""

    # A hostname is already canonical, give or take the bits that never matter.
    if _HOSTLIKE.match(name):
        return name.removeprefix("www.").rstrip(".")

    name = _SEPARATORS.sub("-", name)
    name = _REPEATED_DASH.sub("-", name).strip("-")

    changed = True
    while changed:
        changed = False
        for prefix in _PREFIXES:
            stripped = prefix.replace("_", "-")
            if name.startswith(stripped) and len(name) > len(stripped):
                name = name[len(stripped):]
                changed = True
                break
        for suffix in _SUFFIXES:
            stripped = suffix.replace("_", "-")
            if name.endswith(stripped) and len(name) > len(stripped):
                name = name[: -len(stripped)]
                changed = True
                break

    name = name.strip("-")
    return _ALIASES.get(name, name)
