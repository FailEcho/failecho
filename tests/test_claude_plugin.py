"""The Claude Code plugin: two commands, and both halves actually there.

A plugin is a promise about files that exist at paths Claude Code will look
in. These tests hold the manifests to that promise, so a rename never ships an
install command that fails on someone else's machine.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN_DIR = ROOT / "plugin"
MANIFEST = PLUGIN_DIR / ".claude-plugin" / "plugin.json"
HOOKS = PLUGIN_DIR / "hooks" / "hooks.json"
MCP = PLUGIN_DIR / ".mcp.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_marketplace_points_at_a_real_plugin_directory():
    marketplace = load(MARKETPLACE)
    assert marketplace["name"] == "failecho"
    assert marketplace["owner"]["name"]

    entry = next(p for p in marketplace["plugins"] if p["name"] == "failecho")
    assert entry["source"].startswith("./")
    assert ".." not in entry["source"]

    target = (MARKETPLACE.parent.parent / entry["source"]).resolve()
    assert target == PLUGIN_DIR.resolve()
    assert MANIFEST.exists(), "a plugin directory needs .claude-plugin/plugin.json"


def test_the_plugin_manifest_says_what_it_is():
    manifest = load(MANIFEST)
    assert manifest["name"] == "failecho"
    assert manifest["description"].strip()
    assert manifest["version"]


def test_the_plugin_ships_the_hook_it_configures():
    manifest = load(HOOKS)
    # A standalone hooks.json nests the matchers under "hooks"; with the
    # events at the top level the plugin installs and the hook silently
    # never loads.
    assert set(manifest) == {"hooks"}, manifest.keys()
    for event, entries in manifest["hooks"].items():
        assert event in ("PostToolUse", "PostToolUseFailure"), event
        for entry in entries:
            # Anything but mcp__.* would fire on local tools too: Bash
            # failures are nobody else's failures.
            assert entry["matcher"] == "mcp__.*"
            for command in entry["hooks"]:
                assert command["type"] == "command"
                assert "${CLAUDE_PLUGIN_ROOT}" in command["command"]
                assert command["timeout"] <= 15, "a reporting hook must never stall a session"
                relative = command["command"].split("${CLAUDE_PLUGIN_ROOT}/")[1].strip('"')
                assert (PLUGIN_DIR / relative).exists(), relative


def test_both_halves_are_installed_together():
    """The MCP server answers questions; the hook makes sure they get asked."""
    assert set(load(HOOKS)["hooks"]) == {"PostToolUse", "PostToolUseFailure"}
    assert set(load(MCP)["mcpServers"]) == {"failecho"}


def test_the_plugin_and_the_registry_agree_on_one_network():
    """One network, one URL: server.json, the relay and the plugin must match."""
    import failecho_mcp

    url = load(MCP)["mcpServers"]["failecho"]["url"]
    remotes = {remote["url"] for remote in load(ROOT / "server.json")["remotes"]}
    assert url in remotes
    assert url == failecho_mcp.DEFAULT_URL


def test_the_hook_is_dependency_free_so_the_plugin_needs_no_install_step():
    source = (PLUGIN_DIR / "hooks" / "failecho_hook.py").read_text(encoding="utf-8")
    imports = {
        line.split()[1].split(".")[0]
        for line in source.splitlines()
        if line.startswith(("import ", "from ")) and "__future__" not in line
    }
    stdlib = {
        "hashlib", "ipaddress", "json", "os", "re", "sys", "time", "urllib",
        "uuid", "pathlib", "typing",
    }
    assert imports <= stdlib, imports - stdlib


@pytest.mark.parametrize("path", [MARKETPLACE, MANIFEST, HOOKS, MCP])
def test_manifests_are_valid_json(path):
    assert isinstance(load(path), dict)


@pytest.mark.skipif(shutil.which("claude") is None, reason="Claude Code CLI not installed")
@pytest.mark.parametrize("target", [ROOT, PLUGIN_DIR])
def test_claude_code_itself_accepts_the_manifests(target):
    """The only judge that matters. It caught a hooks.json that installed
    fine and then never loaded the hook."""
    result = subprocess.run(
        ["claude", "plugin", "validate", str(target)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_site_advertises_the_install_this_repository_actually_serves():
    """The homepage, /about and /llms.txt name the marketplace and plugin that
    .claude-plugin/marketplace.json and server.json actually define."""
    marketplace = load(MARKETPLACE)
    plugin = marketplace["plugins"][0]["name"]
    repository = json.loads((ROOT / "server.json").read_text())["repository"]["url"]
    slug = "/".join(repository.rstrip("/").split("/")[-2:])

    install = f"/plugin install {plugin}@{marketplace['name']}"
    add = f"/plugin marketplace add {slug}"

    homepage = (ROOT / "app" / "web" / "static" / "index.html").read_text()
    assert add in homepage and install in homepage
    assert install in (ROOT / "app" / "web" / "static" / "about.html").read_text()

    guide = (ROOT / "app" / "main.py").read_text()
    assert add in guide and install in guide


def test_llms_txt_offers_the_plugin_to_agents_that_read_it(client):
    body = client.get("/llms.txt").text
    assert "/plugin install failecho@failecho" in body
    assert "when a tool, API or MCP operation fails, an agent can ask" in _flat(body)


def _flat(text):
    """llms.txt is hard-wrapped; match prose on words, not on line breaks."""
    import re

    return re.sub(r"\s+", " ", text)
