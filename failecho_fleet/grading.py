"""Grade what an agent produced, against truth fetched independently.

Until now a run counted as completed when its result file matched a pattern:
`"requests": "2.34.2"` and `"requests": "0.0.1"` both passed, and so did three
right answers next to two wrong ones. An outside review said so on 20 Sep, and
it was right -- the completion rows measured shape, not work.

This module fetches the answer itself, from the same public APIs the task
names, and says which of a run's values are actually right. Three outcomes,
reported separately:

* **answered**   -- a result file exists and matches the task's pattern (what
  "runs marked completed" has always meant)
* **valid**      -- every value it holds is the right *kind* of thing
* **correct**    -- every value checked equals the truth, and enough of them
  were checkable to say so

`correct` is deliberately allowed to be ``None``: when GitHub refuses us (the
lab's IP is rate limited most of the day) there is no truth to compare with,
and a grader that guesses in that case is worse than one that abstains.

Truth is cached for TRUTH_TTL so grading a hundred runs does not hammer the
services the fleet is already using, and the cache is on disk so it survives
between runs.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

UA = "failecho-fleet-grader/1.0 (+https://failecho.com)"
TRUTH_TTL = float(os.environ.get("FLEET_TRUTH_TTL") or 1800)
TRUTH_CACHE = os.environ.get("FLEET_TRUTH_CACHE") or "/var/lib/failecho-fleet/truth.json"
#: A star count moves while a run is in flight; anything inside this is right.
STARS_TOLERANCE = 0.02

_memory: dict[str, tuple[float, object]] = {}


def _fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _cache_load() -> dict:
    if _memory:
        return {}
    try:
        with open(TRUTH_CACHE, encoding="utf-8") as fh:
            for key, (at, value) in json.load(fh).items():
                _memory[key] = (float(at), value)
    except (OSError, ValueError, TypeError):
        pass
    return {}


def _cache_save() -> None:
    try:
        tmp = TRUTH_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({k: [at, v] for k, (at, v) in _memory.items()}, fh)
        os.replace(tmp, TRUTH_CACHE)
    except OSError:
        pass


def truth(kind: str, arg: str):
    """The right answer, or None when the service will not tell us.

    None is not a failure of the run being graded; it is a failure of the
    grader to know, and it keeps that run out of the correctness rate.
    """
    _cache_load()
    key = f"{kind}:{arg}"
    hit = _memory.get(key)
    if hit and time.time() - hit[0] < TRUTH_TTL:
        return hit[1]
    try:
        if kind == "pypi":
            value = _fetch(f"https://pypi.org/pypi/{arg}/json")["info"]["version"]
        elif kind == "npm":
            value = _fetch(f"https://registry.npmjs.org/{arg}/latest")["version"]
        elif kind == "crates":
            value = _fetch(f"https://crates.io/api/v1/crates/{arg}")["crate"]["max_version"]
        elif kind == "github_tag":
            value = _fetch(f"https://api.github.com/repos/{arg}/releases/latest")["tag_name"]
        elif kind == "github_stars":
            value = int(_fetch(f"https://api.github.com/repos/{arg}")["stargazers_count"])
        else:
            return None
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, KeyError, ValueError, TypeError):
        return None
    _memory[key] = (time.time(), value)
    _cache_save()
    return value


#: What each task's result file should contain: the JSON key, how to find the
#: truth, and the argument. Keyed by a phrase from the task's own prompt, so a
#: task and its checks cannot drift apart silently.
CHECKS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("latest PyPI versions of requests, httpx and urllib3",
     [("requests", "pypi", "requests"), ("httpx", "pypi", "httpx"), ("urllib3", "pypi", "urllib3")]),
    ("GitHub star counts of pallets/flask",
     [("pallets/flask", "github_stars", "pallets/flask"), ("psf/requests", "github_stars", "psf/requests"),
      ("encode/httpx", "github_stars", "encode/httpx")]),
    ("latest release tag of astral-sh/uv and astral-sh/ruff",
     [("astral-sh/uv", "github_tag", "astral-sh/uv"), ("astral-sh/ruff", "github_tag", "astral-sh/ruff")]),
    ("latest npm version of express and the latest crates.io version of serde",
     [("express", "npm", "express"), ("serde", "crates", "serde")]),
    ("find the latest version of requests, httpx and urllib3",
     [("requests", "pypi", "requests"), ("httpx", "pypi", "httpx"), ("urllib3", "pypi", "urllib3")]),
    ("star counts of pallets/flask",
     [("pallets/flask", "github_stars", "pallets/flask"), ("psf/requests", "github_stars", "psf/requests"),
      ("encode/httpx", "github_stars", "encode/httpx")]),
    ("latest version of the npm package express",
     [("express", "npm", "express"), ("serde", "crates", "serde")]),
]


def checks_for(prompt: str) -> list[tuple[str, str, str]]:
    for phrase, checks in CHECKS:
        if phrase in prompt:
            return checks
    return []


def _values(text: str) -> dict:
    """The result file as a mapping, whatever shape the agent wrote it in."""
    text = (text or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        # a truncated or prose-wrapped file: pull "key": value pairs out of it
        pairs = re.findall(r'"([^"]{1,60})"\s*:\s*(?:"([^"]{0,60})"|(\d+))', text)
        return {k: (v if v else n) for k, v, n in pairs}
    return data if isinstance(data, dict) else {}


def _matches(got, want, kind: str) -> bool:
    if kind == "github_stars":
        try:
            got_n, want_n = float(str(got).replace(",", "")), float(want)
        except (TypeError, ValueError):
            return False
        return want_n and abs(got_n - want_n) / want_n <= STARS_TOLERANCE
    return str(got).strip().lstrip("v=^~") == str(want).strip().lstrip("v")


def grade(prompt: str, result_text: str, answered: bool) -> dict:
    """answered / valid / correct for one run, with the counts behind them."""
    out = {"answered": bool(answered), "valid": None, "correct": None,
           "checked": 0, "matched": 0, "unknown": 0}
    checks = checks_for(prompt or "")
    if not checks:
        return out
    values = _values(result_text)
    if not values:
        out["valid"] = False
        return out
    # "valid" is about shape: every expected key present and not an obvious
    # placeholder. An agent that wrote "unknown" for each version answered the
    # task's pattern and produced nothing.
    placeholders = {"", "unknown", "error", "n/a", "none", "null", "tbd", "0", "0.0.0"}
    present = [k for k, _, _ in checks if k in values]
    out["valid"] = len(present) == len(checks) and all(
        str(values[k]).strip().lower() not in placeholders for k in present)
    for key, kind, arg in checks:
        if key not in values:
            continue
        want = truth(kind, arg)
        if want is None:
            out["unknown"] += 1
            continue
        out["checked"] += 1
        if _matches(values[key], want, kind):
            out["matched"] += 1
    if out["checked"]:
        out["correct"] = out["checked"] == out["matched"] and out["valid"] is not False
    return out
