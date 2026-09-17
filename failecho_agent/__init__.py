"""FailEcho's own agent: real tasks, real services, first-party evidence.

CLAUDE.md: "sessions here use it on themselves: that is how the network gets
its first-party evidence while it bootstraps." This is that, on a timer. A
small tool-using agent runs a read-only task against real public services --
PyPI, npm, the GitHub API -- and every tool call is reported through
``failecho_autoreport`` with the operator token, so it lands as
``source: first_party`` and is never counted as adoption.

Two things it is deliberately not:

* **Not a load generator.** A few dozen runs a day, each a handful of calls.
  The point is the shape of real failures -- GitHub's unauthenticated rate
  limit, a package that does not exist, a provider's 429 -- not volume.
* **Not a stranger.** It refuses to start against anything but localhost
  without ``FIN_FIRST_PARTY_TOKEN``, and after each run it reads one of its
  own fingerprints back and checks that ``evidence_sources`` says
  ``first_party``. A mislabelled run exits non-zero so the timer shows red
  rather than the front page showing a lie.

The model is swapped between Groq and Gemini per run, through the same
OpenAI-compatible request shape, so the provider calls themselves -- which
fail like any other shared infrastructure -- are reported too.

Before it retries a failed tool it asks the network, exactly as any other
agent is told to, and follows the recommendation when there is one. When
there is not, it falls back to rules: back off on a rate limit, retry once on
a server error or a timeout, and never retry a 404 or a validation error.
Whatever it tried, it reports the outcome. That is the half of the network
another agent can use.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from failecho_autoreport import FailEcho, classify

__version__ = "0.1.0"
UA = f"failecho-agent/{__version__} (+https://failecho.com)"

# ---------------------------------------------------------------------------
# configuration, all from the environment
# ---------------------------------------------------------------------------

ENDPOINT = (os.environ.get("FAILECHO_ENDPOINT") or "https://failecho.com").rstrip("/")
REPORTER_ID = os.environ.get("FAILECHO_REPORTER_ID") or "operator-agent"
#: The operator token lives in /etc/failecho.env as FIN_FIRST_PARTY_TOKEN. The
#: wrapper also honours FAILECHO_OPERATOR_TOKEN; either name works here.
OPERATOR_TOKEN = os.environ.get("FIN_FIRST_PARTY_TOKEN") or os.environ.get("FAILECHO_OPERATOR_TOKEN")
STATE_DIR = os.environ.get("STATE_DIRECTORY") or os.environ.get("FAILECHO_AGENT_STATE_DIR") or "/tmp/failecho-agent"
MAX_RUNS_PER_DAY = int(os.environ.get("FAILECHO_AGENT_MAX_RUNS_PER_DAY") or 48)
MAX_MODEL_CALLS = int(os.environ.get("FAILECHO_AGENT_MAX_MODEL_CALLS") or 8)
MAX_TOOL_CALLS = int(os.environ.get("FAILECHO_AGENT_MAX_TOOL_CALLS") or 10)
HTTP_TIMEOUT = 20

PROVIDERS = [
    {"name": "groq", "host": "api.groq.com",
     "url": "https://api.groq.com/openai/v1/chat/completions",
     "key": os.environ.get("GROQ_API_KEY"), "models": ["openai/gpt-oss-20b"]},
    {"name": "gemini", "host": "generativelanguage.googleapis.com",
     "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
     "key": os.environ.get("GEMINI_API_KEY"), "models": ["gemini-flash-latest", "gemini-3.7-flash"]},
]

#: The only hosts the fetch tool may touch. Read-only public registries and
#: APIs; nothing that takes a credential, nothing that writes.
ALLOWED_HOSTS = {"pypi.org", "registry.npmjs.org", "api.github.com", "raw.githubusercontent.com"}


def log(msg: str) -> None:
    print(f"[failecho-agent] {msg}", flush=True)


# ---------------------------------------------------------------------------
# the tools: real calls to real services
# ---------------------------------------------------------------------------


def _get_json(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.load(r)


class Tools:
    """Each public method is a tool. Wrapped for reporting in Agent.__init__."""

    def pypi_latest_version(self, package: str) -> dict:
        d = _get_json(f"https://pypi.org/pypi/{urllib.parse.quote(package)}/json")
        return {"package": package, "version": d["info"]["version"],
                "requires_python": d["info"].get("requires_python")}

    def npm_latest_version(self, package: str) -> dict:
        d = _get_json(f"https://registry.npmjs.org/{urllib.parse.quote(package, safe='@/')}")
        return {"package": package, "version": d["dist-tags"]["latest"]}

    def github_repo(self, owner: str, repo: str) -> dict:
        d = _get_json(f"https://api.github.com/repos/{owner}/{repo}")
        return {"full_name": d["full_name"], "stars": d["stargazers_count"],
                "open_issues": d["open_issues_count"], "pushed_at": d["pushed_at"]}

    def github_latest_release(self, owner: str, repo: str) -> dict:
        d = _get_json(f"https://api.github.com/repos/{owner}/{repo}/releases/latest")
        return {"tag": d["tag_name"], "published_at": d["published_at"]}

    def fetch_text(self, url: str) -> dict:
        host = urllib.parse.urlparse(url).hostname or ""
        if host not in ALLOWED_HOSTS:
            raise ValueError(f"host not allowed: {host}")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            body = r.read(20_000).decode("utf-8", errors="ignore")
        return {"url": url, "chars": len(body), "head": body[:1200]}


TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "pypi_latest_version", "description": "Latest version of a package on PyPI.",
     "parameters": {"type": "object", "properties": {"package": {"type": "string"}}, "required": ["package"]}}},
    {"type": "function", "function": {"name": "npm_latest_version", "description": "Latest version of a package on npm.",
     "parameters": {"type": "object", "properties": {"package": {"type": "string"}}, "required": ["package"]}}},
    {"type": "function", "function": {"name": "github_repo", "description": "Stars, open issues and last push for a GitHub repository.",
     "parameters": {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}}, "required": ["owner", "repo"]}}},
    {"type": "function", "function": {"name": "github_latest_release", "description": "Latest release tag of a GitHub repository.",
     "parameters": {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}}, "required": ["owner", "repo"]}}},
    {"type": "function", "function": {"name": "fetch_text", "description": "Fetch a URL on pypi.org, registry.npmjs.org, api.github.com or raw.githubusercontent.com and return the first part of the body.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
]
TOOL_SERVICE = {"pypi_latest_version": "pypi.org", "npm_latest_version": "registry.npmjs.org",
                "github_repo": "api.github.com", "github_latest_release": "api.github.com",
                "fetch_text": "fetch"}

#: Read-only, realistic, and chosen to hit real edges: a repository with no
#: releases (404), a package that does not exist (404), and enough GitHub
#: calls to meet the unauthenticated rate limit some of the time.
TASKS = [
    "What are the latest versions of the PyPI packages requests, httpx and fastapi? Answer in one line each.",
    "Compare the GitHub star counts of modelcontextprotocol/python-sdk and modelcontextprotocol/typescript-sdk, and say which was pushed to most recently.",
    "What is the latest release tag of FailEcho/failecho on GitHub, and how many open issues does it have?",
    "Which is newer, the latest npm version of express or the latest PyPI version of flask? Give both version strings.",
    "Find the latest version of the PyPI package 'failecho-mcp' and the npm package 'failecho-mcp'. Are they the same?",
    "How many open issues do langchain-ai/langchain and run-llama/llama_index have on GitHub right now?",
    "What is the latest release of astral-sh/uv on GitHub, and the latest version of 'uv' on PyPI? Do they match?",
    "Look up the PyPI package 'definitely-not-a-real-package-xyz-123' and tell me whether it exists.",
    "What is the latest release tag of FailEcho/failecho, and what does raw.githubusercontent.com/FailEcho/failecho/main/glama.json contain?",
    "Report the latest versions of the npm packages react, vue and svelte.",
]

SYSTEM = (
    "You are a small research agent. Use the tools to answer the user's question with real data. "
    "Call tools rather than guessing. If a tool reports an error, you may try a different approach once, "
    "then answer with what you have. Keep the final answer under 80 words."
)


# ---------------------------------------------------------------------------
# the agent
# ---------------------------------------------------------------------------


class Agent:
    def __init__(self, fe: FailEcho, providers: list[dict]):
        self.fe = fe
        self.providers = [p for p in providers if p.get("key")]
        self.tools = Tools()
        # every tool call is reported: service is the host the tool talks to
        # every tool here is a read; declaring it keeps the network from
        # having to guess from the name
        for name, service in TOOL_SERVICE.items():
            setattr(self.tools, name,
                    fe.watch(service=service, operation=name, mutates=False)(getattr(self.tools, name)))
        self.model_calls = 0
        self.tool_calls = 0
        self.failures: list[tuple[str, str, str, str | None]] = []  # (service, op, error_type, code)

    # -- the model, reported like any other tool -------------------------

    def chat(self, provider: dict, model: str, messages: list, tools: list) -> dict:
        @self.fe.watch(service=provider["host"], operation="chat.completions")
        def call():
            body = {"model": model, "messages": messages, "tools": tools, "tool_choice": "auto", "max_tokens": 600}
            req = urllib.request.Request(provider["url"], data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "User-Agent": UA,
                                                  "Authorization": f"Bearer {provider['key']}"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        self.model_calls += 1
        return call()

    def pick_provider(self, run_index: int):
        if not self.providers:
            raise SystemExit("no provider key set (GROQ_API_KEY / GEMINI_API_KEY)")
        order = self.providers[run_index % len(self.providers):] + self.providers[:run_index % len(self.providers)]
        return order

    # -- before retrying: ask, then act ------------------------------------

    def ask_network(self, service: str, operation: str, error_type: str, code: str | None) -> dict | None:
        body = {"service": service, "operation": operation, "error_type": error_type}
        if code:
            body["error_code"] = code
        req = urllib.request.Request(f"{ENDPOINT}/v1/query", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": UA,
                                              "X-Reporter-ID": REPORTER_ID})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.load(r)
        except Exception:
            return None  # the network being down must never break the agent

    def decide(self, error_type: str, advice: dict | None) -> str | None:
        """What to try after a failure. A recommendation from the network wins;
        otherwise the same rules the docs give any agent."""
        rec = (advice or {}).get("recommendation") or {}
        if rec.get("action") in ("backoff", "retry", "refresh_schema", "skip"):
            return rec["action"]
        return {"rate_limit": "backoff", "server_error": "retry", "timeout": "retry",
                "connection_error": "retry"}.get(error_type)

    def run_tool(self, name: str, args: dict) -> tuple[str, bool]:
        """Run one tool with the ask-then-recover discipline. Returns the text
        the model sees and whether it ultimately succeeded."""
        fn = getattr(self.tools, name, None)
        if fn is None:
            return json.dumps({"error": f"unknown tool {name}"}), False
        service = TOOL_SERVICE[name]
        self.tool_calls += 1
        try:
            return json.dumps(fn(**args)), True
        except Exception as exc:  # noqa: BLE001 - every failure is data here
            error_type, code = classify(exc)
            self.failures.append((service, name, error_type, code))
            action = self.decide(error_type, self.ask_network(service, name, error_type, code))
            if action is None:
                return json.dumps({"error": error_type, "code": code, "retried": False}), False
            if action == "skip":
                # the network: every recent recovery attempt failed; do not spend another
                return json.dumps({"error": error_type, "code": code, "retried": False, "skipped": True}), False
            if action == "backoff":
                time.sleep(random.uniform(2, 6))
            # refresh_schema means nothing for a REST call; treat as a plain retry
            self.tool_calls += 1
            try:
                out = fn(**args)
            except Exception as exc2:  # noqa: BLE001
                et2, _ = classify(exc2)
                self.fe.recovered(service, name, action, successful=False)
                return json.dumps({"error": et2, "retried": True, "recovery": action, "recovered": False}), False
            self.fe.recovered(service, name, action, successful=True)
            return json.dumps({"result": out, "recovery": action, "recovered": True}), True

    # -- one task ------------------------------------------------------------

    def run_task(self, task: str, run_index: int) -> str:
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
        providers = self.pick_provider(run_index)
        for provider in providers:
            for model in provider["models"]:
                try:
                    return self._loop(provider, model, messages)
                except urllib.error.HTTPError as e:
                    et, code = classify(e)
                    log(f"provider {provider['name']}/{model} failed: {et}/{code}; trying next")
                    self.failures.append((provider["host"], "chat.completions", et, code))
                except Exception as e:  # noqa: BLE001
                    et, _ = classify(e)
                    log(f"provider {provider['name']}/{model} failed: {et}; trying next")
        return "(no provider answered)"

    def _loop(self, provider: dict, model: str, messages: list) -> str:
        while self.model_calls < MAX_MODEL_CALLS:
            resp = self.chat(provider, model, messages, TOOL_SCHEMAS)
            msg = resp["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            if not calls:
                return (msg.get("content") or "").strip()
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for c in calls:
                if self.tool_calls >= MAX_TOOL_CALLS:
                    result = json.dumps({"error": "tool budget exhausted"})
                else:
                    try:
                        args = json.loads(c["function"].get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    result, _ok = self.run_tool(c["function"]["name"], args)
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
        return "(model budget exhausted)"


# ---------------------------------------------------------------------------
# the run: budget, safety, verification
# ---------------------------------------------------------------------------


def _state_path() -> str:
    os.makedirs(STATE_DIR, exist_ok=True)
    return os.path.join(STATE_DIR, "state.json")


def _take_run_slot() -> int | None:
    """Increment today's run counter; None when the daily budget is spent."""
    path = _state_path()
    today = dt.date.today().isoformat()
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}
    if state.get("date") != today:
        state = {"date": today, "runs": 0, "total": state.get("total", 0)}
    if state["runs"] >= MAX_RUNS_PER_DAY:
        return None
    state["runs"] += 1
    state["total"] = state.get("total", 0) + 1
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    return state["total"]


def _is_local(endpoint: str) -> bool:
    host = urllib.parse.urlparse(endpoint).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1")


def verify_first_party(fe: FailEcho, failures: list) -> bool:
    """Read one of our own fingerprints back. If the network does not say
    first_party, the token is wrong or missing and this run has been counted
    as somebody else's -- the one outcome that must never pass silently."""
    if not failures:
        return True  # nothing was reported as a failure; nothing to check
    service, op, error_type, code = failures[0]
    body = {"service": service, "operation": op, "error_type": error_type}
    if code:
        body["error_code"] = code
    req = urllib.request.Request(f"{ENDPOINT}/v1/query", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
    except Exception as e:  # noqa: BLE001
        log(f"could not verify label: {type(e).__name__}")
        return False
    sources = d.get("evidence_sources") or []
    return "first_party" in sources


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not OPERATOR_TOKEN and not _is_local(ENDPOINT):
        log(f"refusing to run against {ENDPOINT} without FIN_FIRST_PARTY_TOKEN: "
            "every report would be counted as independent adoption")
        return 2

    slot = _take_run_slot()
    if slot is None:
        log(f"daily budget of {MAX_RUNS_PER_DAY} runs spent; not running")
        return 0

    fe = FailEcho(endpoint=ENDPOINT, reporter_id=REPORTER_ID, operator_token=OPERATOR_TOKEN)
    agent = Agent(fe, PROVIDERS)
    task = TASKS[(slot - 1) % len(TASKS)] if "--task" not in argv else argv[argv.index("--task") + 1]
    started = time.monotonic()
    answer = agent.run_task(task, slot)
    elapsed = time.monotonic() - started

    drained = fe.flush(timeout=20)
    log(f"run {slot}: {agent.model_calls} model calls, {agent.tool_calls} tool calls, "
        f"{len(agent.failures)} failures, {elapsed:.1f}s")
    for service, op, et, code in agent.failures:
        log(f"  failure: {service} {op} {et}{'/' + code if code else ''}")
    log(f"  reported: sent={fe.sent} failed={fe.failed} unmatched={fe.unmatched} drained={drained}")
    log(f"  answer: {answer[:160]!r}")

    if not _is_local(ENDPOINT) and agent.failures and not verify_first_party(fe, agent.failures):
        log("LABEL CHECK FAILED: this run's evidence is not marked first_party. "
            "Check FIN_FIRST_PARTY_TOKEN. Exiting non-zero so the timer shows it.")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
