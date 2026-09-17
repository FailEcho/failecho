"""The fleet: eighteen of FailEcho's own agents, one at a time, against the lab.

See docs/fleet-test-plan.md for what this is for. In one line: half the fleet
asks the network before retrying and half retries blind, on identical
workloads, so "does asking help" becomes a number instead of an argument.

Every run is one persona, chosen round-robin from the state file, executed
as one process and then gone. The scheduler is the systemd timer; this module
only knows how to run persona N and how to write the scoreboard afterwards.

Three things this refuses to do, each checked before any request is made:

* run against anything but the lab -- the endpoint's host is asserted at
  startup, and production's hostname is refused by name;
* run with production's operator token in the environment -- it is not
  needed for the lab and its presence means the environment is wrong;
* hold more than one persona in memory -- there is no loop here, one run and
  exit, and the unit caps memory.

Nothing is manufactured. The failures are whatever PyPI, npm, GitHub and four
free-tier model providers actually do when eighteen agents share one IP for two
days.
"""

from __future__ import annotations

import contextvars
import datetime as dt
import json
import os
import signal
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from failecho_autoreport import FailEcho, classify

from .builder import BUILDER_SCHEMAS, BUILDER_TASKS, SYSTEM_PROMPT as BUILDER_PROMPT, Builder, fetch_doc

__version__ = "0.1.0"
UA = "failecho-fleet/0.1 (+https://failecho.com; lab)"

LAB_ENDPOINT = (os.environ.get("FAILECHO_ENDPOINT") or "").rstrip("/")
# systemd joins several StateDirectory= paths with ":"; the first is ours
STATE_DIR = ((os.environ.get("STATE_DIRECTORY") or "").split(":")[0]
             or os.environ.get("FLEET_STATE_DIR") or "/tmp/failecho-fleet")
REPORT_PATH = os.environ.get("FLEET_REPORT_PATH") or os.path.join(STATE_DIR, "fleet.json")
LAB_DB = os.environ.get("FLEET_LAB_DB") or ""
PRODUCTION_HOSTS = ("failecho.com", "www.failecho.com")
MAX_RUNS_PER_DAY = int(os.environ.get("FLEET_MAX_RUNS_PER_DAY") or 600)
#: What the sandbox guest is told to report to. Unset means the in-guest
#: wrapper stays off; there is deliberately no default.
LAB_PUBLIC_URL = (os.environ.get("FLEET_LAB_PUBLIC_URL") or "").rstrip("/") or None

PROVIDERS = {
    "groq": {"host": "api.groq.com", "url": "https://api.groq.com/openai/v1/chat/completions",
             "key": os.environ.get("GROQ_API_KEY"), "models": ["openai/gpt-oss-20b"], "daily_cap": 700,
             "alt_models": ["llama-3.3-70b-versatile"]},
    "gemini": {"host": "generativelanguage.googleapis.com",
               "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
               "key": os.environ.get("GEMINI_API_KEY"), "models": ["gemini-flash-latest"], "daily_cap": 400,
               "alt_models": ["gemini-flash-lite-latest"]},
    # Free-tier key, zero spend, no limit set -- checked before this was added.
    # Three free models verified to make real tool calls; rotated per run so
    # no single one carries the persona. Free models are rate-limited hard,
    # and those 429s are real data. The daily cap keeps us a polite tenant.
    "openrouter": {"host": "openrouter.ai", "url": "https://openrouter.ai/api/v1/chat/completions",
                   "key": os.environ.get("OPEN_ROUTER_API_KEY"),
                   "models": ["nex-agi/nex-n2.5-mini:free", "liquid/lfm-2.5-2.6b:free",
                              "inclusionai/ling-3.0-flash-vl:free"],
                   "headers": {"HTTP-Referer": "https://failecho.com", "X-Title": "FailEcho fleet lab"},
                   "daily_cap": 120, "alt_models": ["liquid/lfm-2.5-2.6b:free", "nex-agi/nex-n2.5-mini:free"]},
    # Ollama's cloud, free tier: three models verified for tool calls, two
    # others answered 402 "requires a subscription", which is where the tier
    # ends and where the fleet stops. gpt-oss:20b here is the same model as
    # Groq's gpt-oss-20b -- the one returning "parsing failed" 400s -- so the
    # fleet will show whether that failure belongs to the model or the host.
    "ollama": {"host": "ollama.com", "url": "https://ollama.com/v1/chat/completions",
               "key": os.environ.get("LLAMA_API_KEY"),
               "models": ["gpt-oss:20b", "nemotron-3-nano:30b", "gemma4:31b"], "daily_cap": 150,
               "alt_models": ["nemotron-3-nano:30b", "gpt-oss:20b"]},
}

#: What an agent can do about a model provider failing, beyond retrying. The
#: explorers try these in order, one per failure, skipping any the network
#: already has evidence for, and report what happened; askers then inherit
#: whichever the network recommends. Blind personas give up on a provider
#: failure, which is what the fleet did on its first day.
PROVIDER_ACTIONS = {
    "rate_limit": ["wait_until_reset", "backoff", "switch_model"],
    "validation_error": ["retry_without_tool_choice", "switch_model", "retry"],
    "server_error": ["retry", "switch_model", "backoff"],
    "timeout": ["retry", "switch_model"],
    "connection_error": ["retry", "backoff"],
}
KNOWN_ACTIONS = ("backoff", "retry", "refresh_schema", "skip", "wait_until_reset", "switch_model",
                 "retry_without_tool_choice", "conditional_request")

# ---------------------------------------------------------------------------
# personas
# ---------------------------------------------------------------------------

PYPI_NPM = [
    "Latest versions of the PyPI packages requests, httpx and fastapi, one line each.",
    "Which is newer, npm express or PyPI flask? Give both versions.",
    "Latest versions of npm react, vue and svelte.",
    "Does the PyPI package 'definitely-not-a-real-package-xyz-123' exist? And what is the latest 'uv'?",
]
GITHUB = [
    "Star counts for modelcontextprotocol/python-sdk and modelcontextprotocol/typescript-sdk.",
    "Latest release tag of FailEcho/failecho and its open issue count.",
    "Open issues on langchain-ai/langchain and run-llama/llama_index.",
    "Latest release of astral-sh/uv on GitHub, and does 'uv' on PyPI match it?",
    "Latest release tag of FailEcho/failecho-does-not-exist. If it does not exist, say so.",
]
MIXED = PYPI_NPM[:2] + GITHUB[:3]

#: The six calls a nightly job makes, every night, in the same order. No
#: model. The pattern the Reddit post described.
CRON_CALLS = [
    ("pypi", "requests"), ("pypi", "fastapi"), ("npm", "express"),
    ("github", "FailEcho/failecho"), ("github", "modelcontextprotocol/python-sdk"),
    ("github_release", "astral-sh/uv"),
]

#: The test-service job: the same eleven calls every run. Enough 429s, 503s
#: and timeouts to give the recovery loop something to do, and three writes
#: that never fail so the unverified-success flag has something to fire on.
TEST_CALLS = [
    ("tool", "flaky_read"), ("tool", "flaky_read"), ("tool", "flaky_read"),
    ("tool", "throttled_read"), ("tool", "throttled_read"),
    ("tool", "always_broken"), ("tool", "slow_read"),
    ("tool", "echo_write"), ("tool", "echo_write"), ("tool", "echo_write"),
    ("pypi", "requests"),
]

#: Twenty real GitHub calls per run. The unauthenticated limit is 60 an hour
#: per IP, shared by the whole fleet -- which is exactly how a real fleet
#: behind one NAT experiences it. Some hours this crosses the line and the
#: 403s are real.
GH_REPOS = ["FailEcho/failecho", "modelcontextprotocol/python-sdk", "modelcontextprotocol/typescript-sdk",
            "langchain-ai/langchain", "run-llama/llama_index", "astral-sh/uv", "pallets/flask",
            "psf/requests", "encode/httpx", "fastapi/fastapi"]
GH_HEAVY = [("github", r) for r in GH_REPOS] + [("github_release", r) for r in GH_REPOS]

#: Real limits, honestly crossed: six crates.io calls back to back (policy is
#: one a second), four Stack Exchange calls (300 a day per IP, shared by the
#: fleet), three GitHub searches (10 a minute unauthenticated), then two
#: repos. Every run, in this order.
LIMIT_CALLS = [
    ("crate", "serde"), ("crate", "tokio"), ("crate", "reqwest"), ("crate", "clap"), ("crate", "anyhow"), ("crate", "rand"),
    ("so", "python"), ("so", "rust"), ("so", "javascript"), ("so", "go"),
    ("gh_search", "mcp server"), ("gh_search", "failure intelligence agents"), ("gh_search", "retry backoff library"),
    ("github", "FailEcho/failecho"), ("github", "astral-sh/uv"),
]

PERSONAS = [
    # reporter,           path,        provider, asks,  workload
    ("fleet-decor-ask-a",  "decorator", "groq",   True,  PYPI_NPM),
    ("fleet-decor-ask-b",  "decorator", "ollama", True,  GITHUB),
    ("fleet-decor-blind-a","decorator", "groq",   False, PYPI_NPM),
    ("fleet-decor-blind-b","decorator", "ollama", False, GITHUB),
    ("fleet-auto-ask-a",   "auto",      "openrouter", True,  MIXED),
    ("fleet-auto-ask-b",   "auto",      "gemini",     True,  MIXED),
    ("fleet-auto-blind-a", "auto",      "openrouter", False, MIXED),
    ("fleet-auto-blind-b", "auto",      "gemini",     False, MIXED),
    ("fleet-mcp-ask",      "mcp",       "groq",       True,  GITHUB),
    ("fleet-mcp-blind",    "mcp",       "groq",       False, GITHUB),
    ("fleet-cron-a",       "decorator", None,     True,  CRON_CALLS),
    ("fleet-cron-b",       "decorator", None,     False, CRON_CALLS),
    # test targets: a service that returns what it is asked for
    ("fleet-test-ask",     "decorator", None,     True,  TEST_CALLS),
    ("fleet-test-blind",   "decorator", None,     False, TEST_CALLS),
    # real target, real limit: twenty GitHub calls a run from a shared IP
    ("fleet-gh-ask",       "decorator", None,     True,  GH_HEAVY),
    ("fleet-gh-blind",     "decorator", None,     False, GH_HEAVY),
    # builders: write code, run it in a throwaway VM, fix it, run it again.
    # Added 2026-09-16 15:40 UTC, after the first sixteen had run for six
    # hours; the twins share a provider so the only difference is asking.
    ("fleet-build-ask",    "builder",   "groq",   True,  BUILDER_TASKS),
    ("fleet-build-blind",  "builder",   "groq",   False, BUILDER_TASKS),
    # explorers: on a provider failure they try an action nobody has evidence
    # for yet and report the outcome. They pay; askers inherit. Added
    # 2026-09-17 05:00 UTC.
    ("fleet-explore-a",    "decorator", "groq",   True,  PYPI_NPM),
    ("fleet-explore-b",    "decorator", "gemini", True,  GITHUB),
    # real limits, real fixes (2026-09-17 06:00 UTC): twins on services that
    # push back under honest use, and a third explorer with no model that
    # meets GitHub's 403 and the limits every run and tries what nobody has
    ("fleet-limits-ask",   "decorator", None,     True,  LIMIT_CALLS),
    ("fleet-limits-blind", "decorator", None,     False, LIMIT_CALLS),
    ("fleet-explore-c",    "decorator", None,     True,  GH_HEAVY + LIMIT_CALLS),
]
BUILD_PERSONAS = {"fleet-build-ask", "fleet-build-blind"}
EXPLORER_PERSONAS = {"fleet-explore-a", "fleet-explore-b", "fleet-explore-c"}

#: Ask/blind twins by reporter. The round-robin ran every ask twin before
#: its blind twin, two minutes apart, and on GitHub's hourly budget the twin
#: that runs second meets the 403s the first one used up the budget for:
#: gh-ask 0 failures, gh-blind 47, identical work. Since 2026-09-17 06:30 UTC
#: twins swap order every cycle, and the proof table counts only runs from
#: then on.
TWINS = [("fleet-decor-ask-a", "fleet-decor-blind-a"), ("fleet-decor-ask-b", "fleet-decor-blind-b"),
         ("fleet-auto-ask-a", "fleet-auto-blind-a"), ("fleet-auto-ask-b", "fleet-auto-blind-b"),
         ("fleet-mcp-ask", "fleet-mcp-blind"), ("fleet-cron-a", "fleet-cron-b"),
         ("fleet-test-ask", "fleet-test-blind"), ("fleet-gh-ask", "fleet-gh-blind"),
         ("fleet-build-ask", "fleet-build-blind"), ("fleet-limits-ask", "fleet-limits-blind")]
FAIR_ORDER_SINCE = "2026-09-17T06:30:00"


def persona_index(position: int) -> int:
    """Which persona runs at this position of the round-robin: on odd cycles
    the twins trade places, so neither side always runs second."""
    n = len(PERSONAS)
    idx, cycle = position % n, position // n
    if cycle % 2 == 0:
        return idx
    names = [p[0] for p in PERSONAS]
    swap = {}
    for a, b in TWINS:
        if a in names and b in names:
            swap[names.index(a)] = names.index(b)
            swap[names.index(b)] = names.index(a)
    return swap.get(idx, idx)

#: What an agent can do about a tool call failing, beyond the default.
#: Explorers try these in order, skipping what the network has evidence for.
TOOL_ACTIONS = {
    "rate_limit": ["backoff", "wait_until_reset", "conditional_request"],
    # GitHub's secondary limit answers a plain 403 Forbidden, which the
    # classifier files as auth_error; from an unauthenticated agent it is a
    # limit all the same
    "auth_error": ["wait_until_reset", "conditional_request"],
    "server_error": ["retry", "backoff"],
    "timeout": ["retry"],
    "connection_error": ["retry"],
}
TEST_PERSONAS = {"fleet-test-ask", "fleet-test-blind"}


def log(msg: str) -> None:
    print(f"[fleet] {msg}", flush=True)


def _daily_quota(exc: BaseException) -> bool:
    """Did the provider say the *daily* budget is gone (groq's TPD, Gemini's
    quota)? Then a minute's wait is pointless."""
    body = (getattr(exc, "failecho_body", "") or "").lower()
    return "per day" in body or "tpd" in body or "rpd" in body or "exceeded your current quota" in body


def _seconds(value: str) -> float:
    """A Retry-After or ratelimit-reset header as seconds: '7', '2.5s', '1m3s'."""
    value = str(value).strip().lower()
    try:
        return float(value)
    except ValueError:
        pass
    total, num = 0.0, ""
    for ch in value:
        if ch.isdigit() or ch == ".":
            num += ch
        elif ch in "hms" and num:
            total += float(num) * {"h": 3600, "m": 60, "s": 1}[ch]
            num = ""
    return total


# ---------------------------------------------------------------------------
# safety: the lab and only the lab
# ---------------------------------------------------------------------------


def assert_lab_only() -> None:
    host = (urllib.parse.urlparse(LAB_ENDPOINT).hostname or "").lower()
    if not LAB_ENDPOINT or not host:
        raise SystemExit("fleet: FAILECHO_ENDPOINT is not set; refusing to guess")
    if host in PRODUCTION_HOSTS or host.endswith(".failecho.com") and host not in ("lab.failecho.com",):
        raise SystemExit(f"fleet: endpoint {host} is production; the fleet runs against the lab only")
    if os.environ.get("FIN_FIRST_PARTY_TOKEN") or os.environ.get("FAILECHO_OPERATOR_TOKEN"):
        raise SystemExit("fleet: an operator token is in the environment; the lab does not use one "
                         "and its presence means this is not the lab's environment")


# ---------------------------------------------------------------------------
# the tools (the same real calls the first-party agent makes)
# ---------------------------------------------------------------------------


#: Set by Run.call for one retry when the recovery action is
#: conditional_request: send If-None-Match with the ETag from the last good
#: answer. GitHub serves 304 to a matching ETag and a 304 does not count
#: against the rate limit -- the fix almost no agent knows. The ETag cache
#: is per persona, on disk, so a later run has something to condition on.
CONDITIONAL: contextvars.ContextVar[bool] = contextvars.ContextVar("conditional", default=False)
ETAGS: contextvars.ContextVar[dict] = contextvars.ContextVar("etags", default={})


def _get_json(url: str):
    headers = {"User-Agent": UA, "Accept": "application/json"}
    cache = ETAGS.get()
    cached = cache.get(url)
    if CONDITIONAL.get() and cached:
        headers["If-None-Match"] = cached["etag"]
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.load(r)
            etag = r.headers.get("ETag")
            if etag and isinstance(cache, dict):
                cache[url] = {"etag": etag, "body": body}
            return body
    except urllib.error.HTTPError as e:
        if e.code == 304 and cached:
            return cached["body"]
        raise


def pypi_latest_version(package: str) -> dict:
    d = _get_json(f"https://pypi.org/pypi/{urllib.parse.quote(package)}/json")
    return {"package": package, "version": d["info"]["version"]}


def npm_latest_version(package: str) -> dict:
    d = _get_json(f"https://registry.npmjs.org/{urllib.parse.quote(package, safe='@/')}")
    return {"package": package, "version": d["dist-tags"]["latest"]}


def github_repo(owner: str, repo: str) -> dict:
    d = _get_json(f"https://api.github.com/repos/{owner}/{repo}")
    return {"full_name": d["full_name"], "stars": d["stargazers_count"], "open_issues": d["open_issues_count"]}


def github_latest_release(owner: str, repo: str) -> dict:
    d = _get_json(f"https://api.github.com/repos/{owner}/{repo}/releases/latest")
    return {"tag": d["tag_name"]}


# Real services with real, documented limits that honest use at fleet scale
# crosses: GitHub search (10/min unauthenticated), crates.io (one request a
# second, 429 above), Stack Exchange (300/day per IP, then a 400
# "throttle_violation"). Nothing synthetic: every failure is a policy being
# enforced on us, the way it would be on any agent behind one address.
def github_search(query: str) -> dict:
    d = _get_json(f"https://api.github.com/search/repositories?q={urllib.parse.quote(query)}&per_page=3")
    return {"total": d["total_count"], "top": [i["full_name"] for i in d["items"][:3]]}


def crates_io_latest(crate: str) -> dict:
    d = _get_json(f"https://crates.io/api/v1/crates/{urllib.parse.quote(crate)}")
    return {"crate": crate, "version": d["crate"]["max_stable_version"] or d["crate"]["max_version"]}


def stackexchange_questions(tag: str) -> dict:
    d = _get_json("https://api.stackexchange.com/2.3/questions?order=desc&sort=activity&pagesize=3"
                  f"&site=stackoverflow&tagged={urllib.parse.quote(tag)}")
    return {"tag": tag, "titles": [q["title"] for q in d["items"][:3]], "quota_remaining": d.get("quota_remaining")}


# httpbingo.org exists to return whatever status you ask for. Calling it is its
# intended use. These are the failure classes the real targets rarely produce,
# and one write that "succeeds" forever while persisting nothing -- the
# fake-success case from the r/AI_Agents thread, as a live service.
def _bingo(path: str, method: str = "GET", timeout: int = 20) -> dict:
    req = urllib.request.Request(f"https://httpbingo.org{path}", method=method,
                                 data=b"{}" if method == "POST" else None,
                                 headers={"User-Agent": UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return {"status": r.status}


def flaky_read() -> dict:            # 503 one time in three; retry genuinely helps
    return _bingo("/status/200,200,503")


def throttled_read() -> dict:        # 429 half the time; backoff genuinely helps
    return _bingo("/status/200,429")


def always_broken() -> dict:         # 503 every time; nothing helps, and the network should learn that
    return _bingo("/status/503")


def slow_read() -> dict:             # 10s server, 8s client: a timeout every time
    return _bingo("/delay/10", timeout=8)


def echo_write() -> dict:            # 200 forever, persists nothing: a write that cannot be verified
    return _bingo("/post", method="POST")


TOOLS = {"pypi_latest_version": pypi_latest_version, "npm_latest_version": npm_latest_version,
         "github_repo": github_repo, "github_latest_release": github_latest_release,
         "github_search": github_search, "crates_io_latest": crates_io_latest,
         "stackexchange_questions": stackexchange_questions,
         "flaky_read": flaky_read, "throttled_read": throttled_read, "always_broken": always_broken,
         "slow_read": slow_read, "echo_write": echo_write}
TOOL_SERVICE = {"pypi_latest_version": "pypi.org", "npm_latest_version": "registry.npmjs.org",
                "github_repo": "api.github.com", "github_latest_release": "api.github.com",
                "github_search": "api.github.com", "crates_io_latest": "crates.io",
                "stackexchange_questions": "api.stackexchange.com",
                "flaky_read": "httpbingo.org", "throttled_read": "httpbingo.org", "always_broken": "httpbingo.org",
                "slow_read": "httpbingo.org", "echo_write": "httpbingo.org"}
TOOL_MUTATES = {"echo_write": True}   # everything else is a read
TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "pypi_latest_version", "description": "Latest version of a PyPI package.",
     "parameters": {"type": "object", "properties": {"package": {"type": "string"}}, "required": ["package"]}}},
    {"type": "function", "function": {"name": "npm_latest_version", "description": "Latest version of an npm package.",
     "parameters": {"type": "object", "properties": {"package": {"type": "string"}}, "required": ["package"]}}},
    {"type": "function", "function": {"name": "github_repo", "description": "Stars and open issues of a GitHub repository.",
     "parameters": {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}}, "required": ["owner", "repo"]}}},
    {"type": "function", "function": {"name": "github_latest_release", "description": "Latest release tag of a GitHub repository.",
     "parameters": {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}}, "required": ["owner", "repo"]}}},
]


# ---------------------------------------------------------------------------
# one persona, one run
# ---------------------------------------------------------------------------


class Run:
    """Everything one run needs to know about itself, and the scoreboard row it
    leaves behind."""

    def __init__(self, reporter: str, path: str, provider: str | None, asks: bool):
        self.reporter, self.path, self.provider, self.asks = reporter, path, provider, asks
        self.fe = FailEcho(endpoint=LAB_ENDPOINT, reporter_id=reporter)
        self.tool_calls = 0
        self.model_calls = 0
        self.model: str | None = None
        self.failures: list[dict] = []   # one per failure: shape, asked, recommended, attempts, recovered
        self.tools = {}
        for name, fn in TOOLS.items():
            if path == "decorator":
                self.tools[name] = self.fe.watch(service=TOOL_SERVICE[name], operation=name,
                                                 mutates=TOOL_MUTATES.get(name, False))(fn)
            else:
                self.tools[name] = fn  # auto and mcp report by other means
        if path == "auto":
            from failecho_autoreport import auto
            auto._fe = self.fe
            auto.enable()
        self.build: dict | None = None   # the builder's ledger, when this is one
        # Every cost a run pays, so ask and blind can be compared on more than
        # attempts: tokens the provider billed, time spent asking the network,
        # time spent waiting on advice, and whether the task got done.
        self.tokens_prompt = 0
        self.tokens_completion = 0
        self.asks_made = 0
        self.ask_seconds = 0.0
        self.wait_seconds = 0.0
        self.completed: bool | None = None
        # this persona's ETag cache (URL -> etag, body), for conditional requests
        self._etag_path = os.path.join(STATE_DIR, f"etags-{reporter}.json")
        try:
            with open(self._etag_path, encoding="utf-8") as fh:
                self.etags = json.load(fh)
        except (OSError, ValueError):
            self.etags = {}
        ETAGS.set(self.etags)

    def save_etags(self) -> None:
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            items = list(self.etags.items())[-200:]
            with open(self._etag_path, "w", encoding="utf-8") as fh:
                json.dump(dict(items), fh)
        except OSError:
            pass

    # -- asking and reporting through the three paths ------------------------

    def ask(self, service: str, operation: str, error_type: str, code: str | None) -> dict | None:
        """One question to the network, timed: asking is the product's cost."""
        started = time.monotonic()
        self.asks_made += 1
        try:
            return self._ask(service, operation, error_type, code)
        finally:
            self.ask_seconds += time.monotonic() - started

    def _ask(self, service: str, operation: str, error_type: str, code: str | None) -> dict | None:
        if self.path == "mcp":
            return self._mcp("check_tool_failure", {"service": service, "operation": operation,
                                                    "error_type": error_type, "error_code": code,
                                                    "reporter_id": self.reporter})
        body = {"service": service, "operation": operation, "error_type": error_type}
        if code:
            body["error_code"] = code
        req = urllib.request.Request(f"{LAB_ENDPOINT}/v1/query", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": UA,
                                              "X-Reporter-ID": self.reporter})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.load(r)
        except Exception:
            return None

    def _count_tokens(self, resp: dict) -> dict:
        usage = resp.get("usage") if isinstance(resp, dict) else None
        if isinstance(usage, dict):
            self.tokens_prompt += int(usage.get("prompt_tokens") or 0)
            self.tokens_completion += int(usage.get("completion_tokens") or 0)
        return resp

    def _sleep(self, seconds: float) -> None:
        self.wait_seconds += seconds
        time.sleep(seconds)

    def _mcp(self, tool: str, args: dict) -> dict | None:
        """One tools/call against the lab's MCP endpoint. Stateless HTTP, so
        no session dance: initialize is not required for a single call."""
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": tool, "arguments": {k: v for k, v in args.items() if v is not None}}}
        req = urllib.request.Request(f"{LAB_ENDPOINT}/mcp", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Accept": "application/json, text/event-stream", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.load(r)
            text = d["result"]["content"][0]["text"]
            return json.loads(text)
        except Exception:
            return None

    def report_failure(self, service, operation, exc):
        if self.path == "mcp":
            et, code = classify(exc)
            self._mcp("report_tool_failure", {"service": service, "operation": operation,
                                              "error_type": et, "error_code": code, "reporter_id": self.reporter})
        # decorator and auto paths report through the wrapper automatically

    def report_success(self, service, operation):
        if self.path == "mcp":
            self._mcp("report_tool_success", {"service": service, "operation": operation, "reporter_id": self.reporter})

    def report_recovery(self, service, operation, action, ok, fingerprint=None):
        if self.path == "mcp" and fingerprint:
            self._mcp("report_recovery_outcome", {"fingerprint": fingerprint, "action": action,
                                                  "successful": ok, "reporter_id": self.reporter})
        else:
            self.fe.recovered(service, operation, action, ok)

    # -- the ask-then-recover discipline --------------------------------------

    def call(self, name: str, args: dict, fn=None, service: str | None = None) -> tuple[str, bool]:
        fn = fn or self.tools[name]
        service = service or TOOL_SERVICE[name]
        self.tool_calls += 1
        started = time.monotonic()
        try:
            out = fn(**args)
            self.report_success(service, name)
            return json.dumps(out), True
        except Exception as exc:  # noqa: BLE001 - every failure is data
            et, code = classify(exc)
            self.report_failure(service, name, exc)
            rec = {"service": service, "operation": name, "error_type": et, "error_code": code,
                   "asked": False, "recommended": None, "attempts": 1, "recovered": False, "seconds": 0.0}
            self.failures.append(rec)
            fingerprint = None
            action = {"rate_limit": "backoff", "server_error": "retry", "timeout": "retry",
                      "connection_error": "retry"}.get(et)
            advice = {}
            if self.asks:
                rec["asked"] = True
                advice = self.ask(service, name, et, code) or {}
                fingerprint = advice.get("fingerprint")
                r = advice.get("recommendation") or {}
                if r.get("action"):
                    rec["recommended"] = r["action"]
                    action = r["action"] if r["action"] in KNOWN_ACTIONS else action
                    if r.get("decaying"):
                        rec["recommended"] += " (decaying)"
                elif self.reporter in EXPLORER_PERSONAS:
                    tried = {a.get("action") for a in advice.get("recovery_actions") or []}
                    untried = [a for a in TOOL_ACTIONS.get(et, []) if a not in tried]
                    if untried:
                        action = untried[0]
                        rec["explored"] = action
            if action == "skip":
                # the network says nothing tried recently has worked: an asker
                # spends no second attempt (since 2026-09-17 04:40 UTC; before
                # that askers retried anyway and tied with blind)
                rec["skipped"] = True
                rec["seconds"] = round(time.monotonic() - started, 2)
                return json.dumps({"error": et, "code": code, "skipped": True,
                                   "why": "the network reports every recent recovery attempt failed"}), False
            if action is None:
                rec["seconds"] = round(time.monotonic() - started, 2)
                return json.dumps({"error": et, "code": code}), False
            token = None
            if action == "backoff":
                self._sleep(3)
            elif action == "wait_until_reset":
                headers = getattr(exc, "headers", None)
                wait = 0.0
                reset = headers.get("x-ratelimit-reset") if headers else None
                if reset and str(reset).isdigit():
                    wait = float(reset) - time.time()
                else:
                    for h in ("retry-after", "x-ratelimit-reset-tokens"):
                        if headers and headers.get(h):
                            wait = _seconds(headers.get(h)); break
                self._sleep(min(max(wait, 1.0), 30.0))
            elif action == "conditional_request":
                token = CONDITIONAL.set(True)
            rec["attempts"] += 1
            self.tool_calls += 1
            try:
                out = fn(**args)
            except Exception:  # noqa: BLE001
                self.report_recovery(service, name, action, False, fingerprint)
                rec["seconds"] = round(time.monotonic() - started, 2)
                return json.dumps({"error": et, "retried": True, "recovered": False}), False
            finally:
                if token is not None:
                    CONDITIONAL.reset(token)
            rec["recovered"] = True
            rec["seconds"] = round(time.monotonic() - started, 2)
            self.report_recovery(service, name, action, True, fingerprint)
            return json.dumps({"result": out, "recovered": True}), True

    # -- a provider failing: ask, explore, or give up ---------------------------

    def recover_provider(self, p: dict, exc: BaseException, chat, state: dict):
        """One recovery attempt after a model provider failed, or None.

        Blind: give up (the first day's behaviour, kept as the control).
        Ask: follow the network's recommendation if it is one of the actions
        this loop knows; `skip` and no advice both mean give up.
        Explore: if the network recommends, follow it; otherwise try the
        first action in PROVIDER_ACTIONS that nobody has evidence for yet,
        and report the outcome either way. Explorers are how the evidence
        askers inherit gets made.
        """
        host = p["host"]
        et, code = classify(exc)
        rec = {"service": host, "operation": "chat.completions", "error_type": et, "error_code": code,
               "asked": False, "recommended": None, "attempts": 1, "recovered": False}
        self.failures.append(rec)
        if not self.asks:
            return None
        rec["asked"] = True
        advice = self.ask(host, "chat.completions", et, code) or {}
        fingerprint = advice.get("fingerprint")
        recommended = (advice.get("recommendation") or {}).get("action")
        action = None
        if recommended:
            rec["recommended"] = recommended
            if recommended == "skip":
                rec["skipped"] = True
                return None
            if recommended in KNOWN_ACTIONS:
                action = recommended
        if action is None and self.reporter in EXPLORER_PERSONAS:
            tried = {a.get("action") for a in advice.get("recovery_actions") or []}
            untried = [a for a in PROVIDER_ACTIONS.get(et, []) if a not in tried]
            action = untried[0] if untried else None
            rec["explored"] = action
        if action is None:
            return None
        # -- apply the action ---------------------------------------------
        if action == "backoff":
            self._sleep(3)
        elif action == "wait_until_reset":
            headers = getattr(exc, "headers", None)
            wait = 0.0
            for name in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
                value = headers.get(name) if headers else None
                if value:
                    wait = _seconds(value)
                    break
            self._sleep(min(max(wait, 1.0), 30.0))
        elif action == "switch_model":
            alts = [m for m in p.get("alt_models", []) if m != state["model"]]
            if not alts:
                return None
            state["model"] = alts[0]
            self.model = f"{self.model}->{alts[0]}"
        elif action == "retry_without_tool_choice":
            state["tools"] = False
        rec["attempts"] += 1
        self.model_calls += 1
        try:
            resp = chat()
        except Exception:  # noqa: BLE001
            self.report_recovery(host, "chat.completions", action, False, fingerprint)
            return None
        rec["recovered"] = True
        self.report_recovery(host, "chat.completions", action, True, fingerprint)
        return resp

    # -- the two kinds of workload ---------------------------------------------

    def run_cron(self, calls: list) -> str:
        for kind, arg in calls:
            if kind == "tool":
                self.call(arg, {})
            elif kind == "pypi":
                self.call("pypi_latest_version", {"package": arg})
            elif kind == "npm":
                self.call("npm_latest_version", {"package": arg})
            elif kind == "github":
                o, r = arg.split("/"); self.call("github_repo", {"owner": o, "repo": r})
            elif kind == "github_release":
                o, r = arg.split("/"); self.call("github_latest_release", {"owner": o, "repo": r})
            elif kind == "crate":
                self.call("crates_io_latest", {"crate": arg})
            elif kind == "so":
                self.call("stackexchange_questions", {"tag": arg})
            elif kind == "gh_search":
                self.call("github_search", {"query": arg})
        return f"cron: {len(calls)} calls"

    def run_model(self, task: str, run_index: int = 0, used_today: int = 0) -> str:
        p = PROVIDERS[self.provider]
        if not p["key"]:
            return "(no provider key)"
        if used_today >= p.get("daily_cap", 10**9):
            return f"(provider {self.provider} daily cap reached; run skipped)"
        model = p["models"][run_index % len(p["models"])]
        self.model = model
        messages = [{"role": "system", "content": "Use the tools to answer with real data. If a tool errors you may "
                                                   "try once more, then answer with what you have. Under 60 words."},
                    {"role": "user", "content": task}]
        state = {"model": model, "tools": True}

        def chat():
            body = {"model": state["model"], "messages": messages, "max_tokens": 500}
            if state["tools"]:
                body.update(tools=TOOL_SCHEMAS, tool_choice="auto")
            req = urllib.request.Request(p["url"], data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "User-Agent": UA,
                                                  "Authorization": f"Bearer {p['key']}", **p.get("headers", {})})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return self._count_tokens(json.load(r))
            except urllib.error.HTTPError as e:
                # a provider rejecting our request is data, and if it is OUR
                # request shape that is wrong, the journal is where we find out
                body = e.read()[:400].decode(errors="ignore")
                e.failecho_body = body
                log(f"  provider {p['host']} HTTP {e.code}: {body[:200]!r}")
                raise

        # The auto path's urllib patch already observes this call by route.
        # Wrapping it again would report every provider call twice, and the
        # network cannot tell a double count from two agents agreeing.
        if self.path != "auto":
            chat = self.fe.watch(service=p["host"], operation="chat.completions", mutates=False)(chat)

        for _ in range(6):
            self.model_calls += 1
            try:
                resp = chat()
            except Exception as exc:  # noqa: BLE001
                resp = self.recover_provider(p, exc, chat, state)
                if resp is None:
                    et, _ = classify(exc)
                    return f"(provider failed: {et})"
            msg = resp["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            if not calls:
                return (msg.get("content") or "").strip()[:200]
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for c in calls:
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                except ValueError:
                    args = {}
                name = c["function"]["name"]
                result, _ = self.call(name, args) if name in self.tools else (json.dumps({"error": "unknown tool"}), False)
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
        return "(model budget exhausted)"

    def run_build(self, task: str, run_index: int = 0, used_today: int = 0) -> str:
        """A builder run: the model writes code and the sandbox runs it.

        Same provider loop as run_model, different tools. run_python and
        resolve_python_deps go to the VM through the Builder; fetch_doc is a
        host read through call(), so it asks or does not exactly as the other
        personas' tools do. The Builder's ledger (VM runs, local failures,
        shared failures) lands on self.build for the scoreboard."""
        p = PROVIDERS[self.provider]
        if not p["key"]:
            return "(no provider key)"
        if used_today >= p.get("daily_cap", 10**9):
            return f"(provider {self.provider} daily cap reached; run skipped)"
        model = p["models"][run_index % len(p["models"])]
        self.model = model
        messages = [{"role": "system", "content": BUILDER_PROMPT}, {"role": "user", "content": task}]

        def chat():
            body = {"model": model, "messages": messages, "tools": BUILDER_SCHEMAS, "tool_choice": "auto",
                    "max_tokens": 2500}
            req = urllib.request.Request(p["url"], data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "User-Agent": UA,
                                                  "Authorization": f"Bearer {p['key']}", **p.get("headers", {})})
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    return self._count_tokens(json.load(r))
            except urllib.error.HTTPError as e:
                body = e.read()[:400].decode(errors="ignore")
                e.failecho_body = body
                log(f"  provider {p['host']} HTTP {e.code}: {body[:200]!r}")
                raise

        chat = self.fe.watch(service=p["host"], operation="chat.completions", mutates=False)(chat)

        with Builder(self.reporter, LAB_PUBLIC_URL, self.fe) as b:
            answer = "(model budget exhausted)"
            for _ in range(10):
                # A builder's context grows with every program and traceback,
                # and groq's free tier is 8,000 tokens a minute: the second or
                # third call of a longer task meets a 429 as a matter of
                # course. Both twins wait the minute out, up to three times --
                # that is what any agent on that tier does -- and the wait is
                # counted. The tool results are trimmed for the same reason.
                resp = None
                for attempt in range(4):
                    self.model_calls += 1
                    try:
                        resp = chat()
                        break
                    except Exception as exc:  # noqa: BLE001
                        et, code = classify(exc)
                        if et == "rate_limit" and attempt < 3:
                            # a per-day quota does not come back in a minute;
                            # the alternate model has its own. Not the network's
                            # call -- a fact about the agent's own account -- so
                            # both twins do it alike.
                            if _daily_quota(exc) and p.get("alt_models"):
                                alts = [m for m in p["alt_models"] if m != state["model"]]
                                if alts:
                                    state["model"] = alts[0]
                                    self.model = f"{self.model}->{alts[0]}"
                                    continue
                            self._sleep(25)
                            continue
                        self.failures.append({"service": p["host"], "operation": "chat.completions", "error_type": et,
                                              "error_code": code, "asked": False, "recommended": None,
                                              "attempts": attempt + 1, "recovered": False})
                        answer = f"(provider failed: {et})"
                        break
                if resp is None:
                    break
                msg = resp["choices"][0]["message"]
                calls = msg.get("tool_calls") or []
                if not calls:
                    answer = (msg.get("content") or "").strip()[:300]
                    break
                messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
                for c in calls:
                    try:
                        args = json.loads(c["function"].get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    name = c["function"]["name"]
                    self.tool_calls += 1
                    if name == "run_python":
                        result = json.dumps(b.run_python(str(args.get("code") or ""), str(args.get("filename") or "task.py"),
                                                         args.get("requirements") if isinstance(args.get("requirements"), list) else None))
                    elif name == "resolve_python_deps":
                        result = json.dumps(b.resolve_python_deps(args.get("requirements") if isinstance(args.get("requirements"), list) else []))
                    elif name == "fetch_doc":
                        self.tool_calls -= 1   # call() counts it
                        url = str(args.get("url") or "")
                        host = (urllib.parse.urlsplit(url).hostname or "").lower() or "invalid"
                        wrapped = self.fe.watch(service=host, operation="fetch_doc", mutates=False)(fetch_doc)
                        result, _ = self.call("fetch_doc", {"url": url}, fn=wrapped, service=host)
                    else:
                        result = json.dumps({"error": "unknown tool"})
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": result[:6000]})
            self.build = b.summary()
            self.build["task_done"] = b.last_ok and not answer.startswith("(")
            return answer


# ---------------------------------------------------------------------------
# scheduling and the scoreboard
# ---------------------------------------------------------------------------


def _state() -> dict:
    os.makedirs(STATE_DIR, exist_ok=True)
    try:
        with open(os.path.join(STATE_DIR, "state.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"next": 0, "runs": [], "day": "", "runs_today": 0}


def _save(state: dict) -> None:
    with open(os.path.join(STATE_DIR, "state.json"), "w", encoding="utf-8") as fh:
        json.dump(state, fh)


def write_report(state: dict) -> None:
    runs = state["runs"]
    by_persona: dict[str, dict] = {}
    blank = lambda: {"runs": 0, "failures": 0, "attempts": 0, "recovered": 0, "asked": 0, "recommended": 0, "skipped": 0,
                     "provider_failures": 0, "provider_recovered": 0, "explored": 0}
    cohorts = {"real / ask": blank(), "real / blind": blank(), "test / ask": blank(), "test / blind": blank(),
               "build / ask": blank(), "build / blind": blank(), "explore": blank()}
    # the builders' second ledger: what happened inside the VM
    blank_build = lambda: {"vm_runs": 0, "local": 0, "shared": 0, "tasks_done": 0, "sandbox_down": 0,
                           "traffic_runs": 0, "unobserved_runs": 0, "connections": 0, "observed_calls": 0}
    build = {"build / ask": blank_build(), "build / blind": blank_build()}
    for r in runs:
        p = by_persona.setdefault(r["reporter"], {"reporter": r["reporter"], "path": r["path"], "provider": r["provider"] or "none",
                                                   "asks": r["asks"], "runs": 0, "tool_calls": 0, "failures": 0, "last": ""})
        p["runs"] += 1; p["tool_calls"] += r["tool_calls"]; p["failures"] += len(r["failures"]); p["last"] = r["at"]
        kind = "test" if r["reporter"] in TEST_PERSONAS else "build" if r["reporter"] in BUILD_PERSONAS else "real"
        key = "explore" if r["reporter"] in EXPLORER_PERSONAS else kind + (" / ask" if r["asks"] else " / blind")
        c = cohorts[key]
        c["runs"] += 1
        if kind == "build" and r.get("build"):
            bl, b = build[key], r["build"]
            bl["vm_runs"] += b.get("vm_runs", 0); bl["local"] += b.get("local_failures", 0)
            bl["shared"] += len(b.get("shared_failures") or []); bl["tasks_done"] += int(bool(b.get("task_done")))
            bl["sandbox_down"] += int(b.get("sandbox", "ok") != "ok")
            for cov in b.get("coverage") or []:
                bl["connections"] += cov["connections"]; bl["observed_calls"] += cov["observed"]
                if cov["connections"] > 0:
                    bl["traffic_runs"] += 1; bl["unobserved_runs"] += int(cov["missed"])
        for f in r["failures"]:
            c["failures"] += 1; c["attempts"] += f["attempts"]; c["recovered"] += int(f["recovered"])
            c["asked"] += int(f["asked"]); c["recommended"] += int(bool(f["recommended"])); c["skipped"] += int(bool(f.get("skipped")))
            if f["operation"] == "chat.completions":
                c["provider_failures"] += 1; c["provider_recovered"] += int(f["recovered"]); c["explored"] += int(bool(f.get("explored")))

    # real targets, per service and cohort: the proof table. Test endpoints
    # and model providers are left out on purpose; this is PyPI, GitHub,
    # crates.io, Stack Exchange answering real agents behind one address.
    real: dict[tuple[str, str], dict] = {}
    for r in runs:
        if r["reporter"] in TEST_PERSONAS or r["reporter"] in BUILD_PERSONAS or r["reporter"] in EXPLORER_PERSONAS:
            continue
        if r["at"] < FAIR_ORDER_SINCE:
            continue   # before the twins alternated order; see TWINS
        side = "ask" if r["asks"] else "blind"
        for f in r["failures"]:
            if f["operation"] == "chat.completions" or f["service"] == "httpbingo.org":
                continue
            row = real.setdefault((f["service"], side), {"service": f["service"], "cohort": side, "failures": 0,
                                                          "attempts": 0, "recovered": 0, "skipped": 0, "seconds": 0.0})
            row["failures"] += 1; row["attempts"] += f["attempts"]; row["recovered"] += int(f["recovered"])
            row["skipped"] += int(bool(f.get("skipped"))); row["seconds"] += float(f.get("seconds") or 0)
    real_targets = sorted(real.values(), key=lambda x: (x["service"], x["cohort"]))

    # Cost and outcome per run, by cohort: every recorded cost, so a change
    # to the product can be judged on all of them, not on the one it moved.
    # Only runs that carry metrics (recorded since 2026-09-17 10:30 UTC).
    costs: dict[str, dict] = {}
    for r in runs:
        m = r.get("metrics")
        if not m:
            continue
        kind = "test" if r["reporter"] in TEST_PERSONAS else "build" if r["reporter"] in BUILD_PERSONAS else "real"
        key = "explore" if r["reporter"] in EXPLORER_PERSONAS else kind + (" / ask" if r["asks"] else " / blind")
        c = costs.setdefault(key, {"cohort": key, "runs": 0, "completed": 0, "tokens": 0, "tokens_prompt": 0,
                                   "tokens_completion": 0, "model_calls": 0, "tool_calls": 0, "seconds": 0.0,
                                   "asks": 0, "ask_seconds": 0.0, "wait_seconds": 0.0, "calls_first_try": 0,
                                   "calls_recovered": 0, "calls_failed": 0, "failure_seconds": 0.0})
        c["runs"] += 1; c["completed"] += int(bool(m.get("completed")))
        c["tokens_prompt"] += m.get("tokens_prompt", 0); c["tokens_completion"] += m.get("tokens_completion", 0)
        c["tokens"] = c["tokens_prompt"] + c["tokens_completion"]
        c["model_calls"] += r.get("model_calls", 0); c["tool_calls"] += r.get("tool_calls", 0)
        c["seconds"] += float(r.get("seconds") or 0); c["asks"] += m.get("asks", 0)
        c["ask_seconds"] += float(m.get("ask_seconds") or 0); c["wait_seconds"] += float(m.get("wait_seconds") or 0)
        c["calls_first_try"] += m.get("calls_first_try", 0); c["calls_recovered"] += m.get("calls_recovered", 0)
        c["calls_failed"] += m.get("calls_failed", 0)
        c["failure_seconds"] += sum(float(f.get("seconds") or 0) for f in r["failures"])
    for c in costs.values():
        n = c["runs"] or 1
        c.update(completed_rate=round(c["completed"] / n, 3), tokens_per_run=round(c["tokens"] / n),
                 tokens_per_completed=(round(c["tokens"] / c["completed"]) if c["completed"] else None),
                 seconds_per_run=round(c["seconds"] / n, 1), model_calls_per_run=round(c["model_calls"] / n, 2),
                 tool_calls_per_run=round(c["tool_calls"] / n, 2), asks_per_run=round(c["asks"] / n, 2),
                 ask_seconds_per_run=round(c["ask_seconds"] / n, 3), wait_seconds_per_run=round(c["wait_seconds"] / n, 2),
                 failure_seconds_per_run=round(c["failure_seconds"] / n, 2))
        for k in ("seconds", "ask_seconds", "wait_seconds", "failure_seconds"):
            c[k] = round(c[k], 1)
    cost_rows = [costs[k] for k in ("real / ask", "real / blind", "test / ask", "test / blind",
                                    "build / ask", "build / blind", "explore") if k in costs]
    for row in real_targets:
        row["attempts_per_failure"] = round(row["attempts"] / row["failures"], 2) if row["failures"] else None
        row["seconds"] = round(row["seconds"], 1)

    repeats, naming, totals_db = [], [], {}
    if LAB_DB and os.path.exists(LAB_DB):
        try:
            c = sqlite3.connect(f"file:{LAB_DB}?mode=ro", uri=True)
            for row in c.execute(
                "select o.service, o.operation, o.error_type, o.error_code, count(distinct o.reporter_hash), count(*), "
                "(select group_concat(action || ' ' || sum_ok || '/' || n, ', ') from (select action, sum(successful) sum_ok, count(*) n "
                " from recovery_outcomes ro where ro.fingerprint = o.fingerprint group by action)) "
                "from observations o where o.outcome='failure' group by o.fingerprint having count(distinct o.reporter_hash) >= 2 "
                "order by 5 desc, 6 desc limit 25"):
                repeats.append({"service": row[0], "operation": row[1], "error_type": row[2], "error_code": row[3],
                                "reporters": row[4], "observations": row[5], "fixes": row[6]})
            for row in c.execute(
                "select service, group_concat(distinct operation), count(distinct fingerprint) from observations "
                "where outcome='failure' group by service order by 3 desc"):
                ops = sorted(set((row[1] or "").split(",")))
                paths = sorted({"route" if o.startswith(("GET ", "POST ")) else "tool" for o in ops})
                naming.append({"service": row[0], "operations": ops, "fingerprints": row[2],
                               "expected": len({o for o in ops if not o.startswith(("GET ", "POST "))}) or 1, "paths": paths})
            totals_db["cross_reporter_fingerprints"] = len(repeats)
            totals_db["recovery_outcomes"] = c.execute("select count(*) from recovery_outcomes").fetchone()[0]
            totals_db["cross_agent_help"] = (c.execute("select coalesce(sum(value),0) from daily_counters where name='cross_agent_help'").fetchone() or [0])[0]
        except sqlite3.Error as e:
            totals_db["db_error"] = str(e)

    # the install canary and the onboarding test write their own files beside
    # the state; shown, not merged
    def _side(name):
        try:
            with open(os.path.join(STATE_DIR, name), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None
    canary, onboard = _side("canary.json"), _side("onboard.json")

    report = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "canary": canary, "onboard": onboard,
        "totals": {"runs": len(runs), "personas": len(by_persona),
                   "tool_calls": sum(r["tool_calls"] for r in runs),
                   "failures": sum(len(r["failures"]) for r in runs), **totals_db},
        "cohorts": [{"cohort": k, **v, "attempts_per_failure": (v["attempts"] / v["failures"]) if v["failures"] else None}
                    for k, v in cohorts.items()],
        "build": [{"cohort": k, **v, "shared_share": (v["shared"] / (v["shared"] + v["local"])) if (v["shared"] + v["local"]) else None}
                  for k, v in build.items()],
        # the live feed: the last builder runs, what was asked and what happened
        "recent_builds": [
            {"at": r["at"], "reporter": r["reporter"], "task": r.get("task"), "seconds": r.get("seconds"),
             "vm_runs": r["build"].get("vm_runs", 0), "local": r["build"].get("local_failures", 0),
             "shared": len(r["build"].get("shared_failures") or []), "done": bool(r["build"].get("task_done")),
             "traffic": sum(c["connections"] for c in r["build"].get("coverage") or []),
             "observed": sum(c["observed"] for c in r["build"].get("coverage") or []),
             "answer": r.get("answer")}
            for r in runs if r.get("build") and r["reporter"] in BUILD_PERSONAS
        ][-12:][::-1],
        "repeats": repeats, "naming": naming, "real_targets": real_targets, "costs": cost_rows,
        "personas": sorted(by_persona.values(), key=lambda p: p["reporter"]),
    }
    os.makedirs(os.path.dirname(REPORT_PATH) or ".", exist_ok=True)
    tmp = REPORT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report, fh)
    os.replace(tmp, REPORT_PATH)


def main(argv: list[str] | None = None) -> int:
    # a stop mid-run (systemd restarting a unit we depend on) must still close
    # the VM: SIGTERM becomes SystemExit so context managers unwind
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    argv = sys.argv[1:] if argv is None else argv
    assert_lab_only()
    state = _state()
    today = dt.date.today().isoformat()
    if state.get("day") != today:
        state["day"], state["runs_today"] = today, 0
    if state["runs_today"] >= MAX_RUNS_PER_DAY:
        log("daily budget spent"); return 0

    idx = int(argv[argv.index("--persona") + 1]) if "--persona" in argv else persona_index(state["next"])
    reporter, path, provider, asks, workload = PERSONAS[idx]
    run = Run(reporter, path, provider, asks)
    started = time.monotonic()
    state.setdefault("provider_calls_today", {})
    if state.get("provider_day") != today:
        state["provider_day"], state["provider_calls_today"] = today, {}
    if provider is None:
        answer = run.run_cron(workload)
    elif path == "builder":
        task = workload[(state["runs_today"] // len(PERSONAS)) % len(workload)]
        answer = run.run_build(task, run_index=state["runs_today"],
                               used_today=state["provider_calls_today"].get(provider, 0))
        state["provider_calls_today"][provider] = state["provider_calls_today"].get(provider, 0) + run.model_calls
    else:
        task = workload[(state["runs_today"] // len(PERSONAS)) % len(workload)]
        answer = run.run_model(task, run_index=state["runs_today"],
                               used_today=state["provider_calls_today"].get(provider, 0))
        state["provider_calls_today"][provider] = state["provider_calls_today"].get(provider, 0) + run.model_calls
    run.fe.flush(timeout=20)
    run.save_etags()
    # Did the run do its job? A model run: a real answer, not a parenthesised
    # failure. A cron run: every call eventually succeeded. A builder: the
    # task came out done. The one number a user of an agent cares about.
    if run.build is not None:
        run.completed = bool(run.build.get("task_done"))
    elif provider is None:
        run.completed = all(f["recovered"] for f in run.failures)
    else:
        run.completed = not answer.startswith("(")

    record = {"at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "reporter": reporter, "path": path,
              "provider": provider, "model": getattr(run, "model", None), "asks": asks, "tool_calls": run.tool_calls, "model_calls": run.model_calls,
              "failures": run.failures, "seconds": round(time.monotonic() - started, 1),
              "metrics": {"tokens_prompt": run.tokens_prompt, "tokens_completion": run.tokens_completion,
                          "asks": run.asks_made, "ask_seconds": round(run.ask_seconds, 3),
                          "wait_seconds": round(run.wait_seconds, 2), "completed": run.completed,
                          "calls_first_try": run.tool_calls - sum(f["attempts"] for f in run.failures),
                          "calls_recovered": sum(1 for f in run.failures if f["recovered"]),
                          "calls_failed": sum(1 for f in run.failures if not f["recovered"])}}
    if run.build is not None:
        record["build"] = run.build
        record["task"] = task[:160] if path == "builder" else None
        record["answer"] = answer[:200]
    state["runs"].append(record)
    state["runs"] = state["runs"][-5000:]
    state["next"] = state["next"] + 1 if "--persona" not in argv else idx + 1
    state["runs_today"] += 1
    _save(state)
    write_report(state)

    log(f"{reporter} [{path}/{provider or 'none'}/{'ask' if asks else 'blind'}] "
        f"{run.tool_calls} tool calls, {run.model_calls} model calls, {len(run.failures)} failures, "
        f"sent={run.fe.sent} failed={run.fe.failed}, {record['seconds']}s")
    for f in run.failures:
        log(f"  {f['service']} {f['operation']} {f['error_type']}{'/' + f['error_code'] if f['error_code'] else ''}"
            f" asked={f['asked']} rec={f['recommended']} attempts={f['attempts']} recovered={f['recovered']}")
    if run.build is not None:
        b = run.build
        log(f"  build: vm_runs={b['vm_runs']} local={b['local_failures']} shared={len(b['shared_failures'])} "
            f"done={b['task_done']} sandbox={b['sandbox']}")
    log(f"  answer: {answer[:120]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
