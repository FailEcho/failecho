"""The README is 1,500 lines. The table of contents is how anyone finds
anything in it, and a table of contents that points at headings which have
been renamed is worse than none at all -- it looks maintained and is not."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()


def _headings() -> set[str]:
    """Every heading, slugified the way GitHub slugifies them."""
    found = set()
    fenced = False
    for line in README.splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = re.match(r"^#{2,6}\s+(.*)$", line)
        if not match:
            continue
        slug = match.group(1).strip().lower()
        slug = re.sub(r"[^\w\s-]", "", slug)
        found.add(re.sub(r"\s+", "-", slug))
    return found


def test_every_contents_link_lands_on_a_heading():
    contents = README[README.index("## Contents"):README.index("**Start here**") + 4000]
    links = re.findall(r"\]\(#([a-z0-9-]+)\)", contents)
    assert len(links) >= 15, f"the contents shrank to {len(links)} entries"

    headings = _headings()
    for anchor in links:
        assert anchor in headings, f"#{anchor} points at no heading"


def test_the_first_screen_says_what_this_is_and_where_to_go():
    """Someone landing here from a directory listing has about ten lines."""
    first = README[:1400]
    assert "wordmark" in first, "no logo"
    assert "failecho.com/setup" in first, "no route to connecting something"
    assert "cross-agent failure intelligence network" in first
    # Both variants, so the wordmark is visible in either GitHub theme: the
    # dark-ink one is invisible on dark and the white one on light.
    assert "prefers-color-scheme: dark" in first
    assert "wordmark-dark.png" in first and "wordmark-light.png" in first


def test_the_logo_files_are_actually_in_the_repository():
    """A README that references an untracked path renders a broken image."""
    for name in ("wordmark-dark.png", "wordmark-light.png"):
        assert (ROOT / "app" / "web" / "static" / name).exists(), name
