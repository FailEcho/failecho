"""The fleet: twelve of FailEcho's own agents, one at a time, against the lab.

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

Nothing is manufactured. The failures are whatever PyPI, npm, GitHub and two
free-tier model providers actually do when twelve agents share one IP for two
days.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from failecho_autoreport import FailEcho, classify

__version__ = "0.1.0"
UA = "failecho-fleet/0.1 (+https://failecho.com; lab)"

LAB_ENDPOINT = (os.environ.get("FAILECHO_ENDPOINT") or "").rstrip("/")
STATE_DIR = os.environ.get("STATE_DIRECTORY") or os.environ.get("FLEET_STATE_DIR") or "/tmp/failecho-fleet"
REPORT_PATH = os.environ.get("FLEET_REPORT_PATH") or os.path.join(STATE_DIR, "fleet.json")
LAB_DB = os.environ.get("FLEET_LAB_DB") or ""
PRODUCTION_HOSTS = ("failecho.com", "www.failecho.com")
MAX_RUNS_PER_DAY = int(os.environ.get("FLEET_MAX_RUNS_PER_DAY") or 600)

PROVIDERS = {
    "groq": {"host": "api.groq.com", "url": "https://api.groq.com/openai/v1/chat/completions",
             "key": os.environ.get("GROQ_API_KEY"), "model": "openai/gpt-oss-20b"},
    "gemini": {"host": "generativelanguage.googleapis.com",
               "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
               "key": os.environ.get("GEMINI_API_KEY"), "model": "gemini-flash-latest"},
}

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

PERSONAS = [
    # reporter,           path,        provider, asks,  workload
    ("fleet-decor-ask-a",  "decorator", "groq",   True,  PYPI_NPM),
    ("fleet-decor-ask-b",  "decorator", "gemini", True,  GITHUB),
    ("fleet-decor-blind-a","decorator", "groq",   False, PYPI_NPM),
    ("fleet-decor-blind-b","decorator", "gemini", False, GITHUB),
    ("fleet-auto-ask-a",   "auto",      "groq",   True,  MIXED),
    ("fleet-auto-ask-b",   "auto",      "gemini", True,  MIXED),
    ("fleet-auto-blind-a", "auto",      "groq",   False, MIXED),
    ("fleet-auto-blind-b", "auto",      "gemini", False, MIXED),
    ("fleet-mcp-ask",      "mcp",       "groq",   True,  GITHUB),
    ("fleet-mcp-blind",    "mcp",       "gemini", False, GITHUB),
    ("fleet-cron-a",       "decorator", None,     True,  CRON_CALLS),
    ("fleet-cron-b",       "decorator", None,     False, CRON_CALLS),
]


def log(msg: str) -> None:
    print(f"[fleet] {msg}", flush=True)


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


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


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


TOOLS = {"pypi_latest_version": pypi_latest_version, "npm_latest_version": npm_latest_version,
         "github_repo": github_repo, "github_latest_release": github_latest_release}
TOOL_SERVICE = {"pypi_latest_version": "pypi.org", "npm_latest_version": "registry.npmjs.org",
                "github_repo": "api.github.com", "github_latest_release": "api.github.com"}
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
        self.failures: list[dict] = []   # one per failure: shape, asked, recommended, attempts, recovered
        self.tools = {}
        for name, fn in TOOLS.items():
            if path == "decorator":
                self.tools[name] = self.fe.watch(service=TOOL_SERVICE[name], operation=name, mutates=False)(fn)
            else:
                self.tools[name] = fn  # auto and mcp report by other means
        if path == "auto":
            from failecho_autoreport import auto
            auto._fe = self.fe
            auto.enable()

    # -- asking and reporting through the three paths ------------------------

    def ask(self, service: str, operation: str, error_type: str, code: str | None) -> dict | None:
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

    def call(self, name: str, args: dict) -> tuple[str, bool]:
        fn = self.tools[name]
        service = TOOL_SERVICE[name]
        self.tool_calls += 1
        try:
            out = fn(**args)
            self.report_success(service, name)
            return json.dumps(out), True
        except Exception as exc:  # noqa: BLE001 - every failure is data
            et, code = classify(exc)
            self.report_failure(service, name, exc)
            rec = {"service": service, "operation": name, "error_type": et, "error_code": code,
                   "asked": False, "recommended": None, "attempts": 1, "recovered": False}
            self.failures.append(rec)
            fingerprint = None
            action = {"rate_limit": "backoff", "server_error": "retry", "timeout": "retry",
                      "connection_error": "retry"}.get(et)
            if self.asks:
                rec["asked"] = True
                advice = self.ask(service, name, et, code)
                if advice:
                    fingerprint = advice.get("fingerprint")
                    r = advice.get("recommendation") or {}
                    if r.get("action"):
                        rec["recommended"] = r["action"]
                        action = r["action"] if r["action"] in ("backoff", "retry", "refresh_schema") else action
                        if r.get("decaying"):
                            rec["recommended"] += " (decaying)"
            if action is None:
                return json.dumps({"error": et, "code": code}), False
            if action == "backoff":
                time.sleep(3)
            rec["attempts"] += 1
            self.tool_calls += 1
            try:
                out = fn(**args)
            except Exception:  # noqa: BLE001
                self.report_recovery(service, name, action, False, fingerprint)
                return json.dumps({"error": et, "retried": True, "recovered": False}), False
            rec["recovered"] = True
            self.report_recovery(service, name, action, True, fingerprint)
            return json.dumps({"result": out, "recovered": True}), True

    # -- the two kinds of workload ---------------------------------------------

    def run_cron(self, calls: list) -> str:
        for kind, arg in calls:
            if kind == "pypi":
                self.call("pypi_latest_version", {"package": arg})
            elif kind == "npm":
                self.call("npm_latest_version", {"package": arg})
            elif kind == "github":
                o, r = arg.split("/"); self.call("github_repo", {"owner": o, "repo": r})
            elif kind == "github_release":
                o, r = arg.split("/"); self.call("github_latest_release", {"owner": o, "repo": r})
        return f"cron: {len(calls)} calls"

    def run_model(self, task: str) -> str:
        p = PROVIDERS[self.provider]
        if not p["key"]:
            return "(no provider key)"
        messages = [{"role": "system", "content": "Use the tools to answer with real data. If a tool errors you may "
                                                   "try once more, then answer with what you have. Under 60 words."},
                    {"role": "user", "content": task}]

        def chat():
            body = {"model": p["model"], "messages": messages, "tools": TOOL_SCHEMAS, "tool_choice": "auto", "max_tokens": 500}
            req = urllib.request.Request(p["url"], data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "User-Agent": UA,
                                                  "Authorization": f"Bearer {p['key']}"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                # a provider rejecting our request is data, and if it is OUR
                # request shape that is wrong, the journal is where we find out
                log(f"  provider {p['host']} HTTP {e.code}: {e.read()[:200].decode(errors='ignore')!r}")
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
                et, code = classify(exc)
                self.failures.append({"service": p["host"], "operation": "chat.completions", "error_type": et,
                                      "error_code": code, "asked": False, "recommended": None, "attempts": 1, "recovered": False})
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
    cohorts = {"ask": {"runs": 0, "failures": 0, "attempts": 0, "recovered": 0, "asked": 0, "recommended": 0},
               "blind": {"runs": 0, "failures": 0, "attempts": 0, "recovered": 0, "asked": 0, "recommended": 0}}
    for r in runs:
        p = by_persona.setdefault(r["reporter"], {"reporter": r["reporter"], "path": r["path"], "provider": r["provider"] or "none",
                                                   "asks": r["asks"], "runs": 0, "tool_calls": 0, "failures": 0, "last": ""})
        p["runs"] += 1; p["tool_calls"] += r["tool_calls"]; p["failures"] += len(r["failures"]); p["last"] = r["at"]
        c = cohorts["ask" if r["asks"] else "blind"]
        c["runs"] += 1
        for f in r["failures"]:
            c["failures"] += 1; c["attempts"] += f["attempts"]; c["recovered"] += int(f["recovered"])
            c["asked"] += int(f["asked"]); c["recommended"] += int(bool(f["recommended"]))

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

    report = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "totals": {"runs": len(runs), "personas": len(by_persona),
                   "tool_calls": sum(r["tool_calls"] for r in runs),
                   "failures": sum(len(r["failures"]) for r in runs), **totals_db},
        "cohorts": [{"cohort": k, **v, "attempts_per_failure": (v["attempts"] / v["failures"]) if v["failures"] else None}
                    for k, v in cohorts.items()],
        "repeats": repeats, "naming": naming,
        "personas": sorted(by_persona.values(), key=lambda p: p["reporter"]),
    }
    os.makedirs(os.path.dirname(REPORT_PATH) or ".", exist_ok=True)
    tmp = REPORT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report, fh)
    os.replace(tmp, REPORT_PATH)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    assert_lab_only()
    state = _state()
    today = dt.date.today().isoformat()
    if state.get("day") != today:
        state["day"], state["runs_today"] = today, 0
    if state["runs_today"] >= MAX_RUNS_PER_DAY:
        log("daily budget spent"); return 0

    idx = int(argv[argv.index("--persona") + 1]) if "--persona" in argv else state["next"] % len(PERSONAS)
    reporter, path, provider, asks, workload = PERSONAS[idx]
    run = Run(reporter, path, provider, asks)
    started = time.monotonic()
    if provider is None:
        answer = run.run_cron(workload)
    else:
        task = workload[(state["runs_today"] // len(PERSONAS)) % len(workload)]
        answer = run.run_model(task)
    run.fe.flush(timeout=20)

    record = {"at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "reporter": reporter, "path": path,
              "provider": provider, "asks": asks, "tool_calls": run.tool_calls, "model_calls": run.model_calls,
              "failures": run.failures, "seconds": round(time.monotonic() - started, 1)}
    state["runs"].append(record)
    state["runs"] = state["runs"][-5000:]
    state["next"] = idx + 1
    state["runs_today"] += 1
    _save(state)
    write_report(state)

    log(f"{reporter} [{path}/{provider or 'none'}/{'ask' if asks else 'blind'}] "
        f"{run.tool_calls} tool calls, {run.model_calls} model calls, {len(run.failures)} failures, "
        f"sent={run.fe.sent} failed={run.fe.failed}, {record['seconds']}s")
    for f in run.failures:
        log(f"  {f['service']} {f['operation']} {f['error_type']}{'/' + f['error_code'] if f['error_code'] else ''}"
            f" asked={f['asked']} rec={f['recommended']} attempts={f['attempts']} recovered={f['recovered']}")
    log(f"  answer: {answer[:120]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
