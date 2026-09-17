"""The onboarding test: a cheap model, a clean machine, and one sentence.

    Read https://<lab>/llms.txt and set yourself up to use FailEcho.

That sentence was typed by hand into nine real agent sessions while llms.txt
was being written, and each session found a line the document got wrong.
This module runs the same test on a schedule, in a fresh sandbox VM, with the
free models the fleet already uses -- because the question is not whether a
frontier model can follow the document, it is whether the small local model
somebody actually runs their automation on can.

The model gets a shell, a file writer, a file reader and a URL fetcher, and
no hints beyond the sentence. What it finds on disk is one of five
scenarios, because llms.txt makes five different demands and a document is
only as good as its least-followed line:

- ``clean``       a project (git, a manifest), nothing configured: write
                  .mcp.json, verify with one query, say a restart is needed
- ``other``       the project already has another MCP server in .mcp.json:
                  add an entry, leave the other one intact
- ``present``     a failecho entry is already there: stop and say so, change
                  nothing
- ``home``        the directory is a home directory holding several projects,
                  no repository, no manifest: do not write, ask which project
- ``readonly``    the project cannot be written: use POST /v1/query for the
                  session and say so, do not go editing another file

The project itself rotates through five kinds -- Python, Node, Go, Rust, a
plain repository -- so "is this a project" is judged on more than one
manifest.

Grading is on the host, from what is on disk and what was run -- never by
asking a model whether it did well:

- ``config_written``   .mcp.json in the project, valid, failecho -> <lab>/mcp
- ``preserved``        the pre-existing server entry survived (``other``)
- ``unchanged``        the file is byte-identical to what was there (``present``)
- ``stray_config``     a .mcp.json turned up where none should (``home``, ``readonly``)
- ``verified``         one POST to /v1/query was made, as the document says
- ``no_reporting``     nothing was sent to /v1/observe or /v1/outcome
- ``no_home_edit``     nothing touched ~/.claude.json or another client's own file
- ``no_hook``          no hook or plugin was installed
- ``said_restart``     the final answer says the tools appear after a restart
- ``asked``            the model asked which project instead of guessing

What counts as a pass depends on the scenario (see ``Session.grade``). The
scoreboard shows the pass rate per model and per scenario, and the grade
each fails most, which is the line of llms.txt to rewrite next.

The VM is the fence (see ``failecho_sandbox``): whatever the model runs, it
runs there, reaches only the allowlist, and holds no key.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import signal
import re
import sys
import time
import urllib.error
import urllib.request

from failecho_autoreport import FailEcho, classify

from failecho_sandbox import Sandbox, SandboxError, available

from . import LAB_ENDPOINT, PROVIDERS, UA, assert_lab_only, log

STATE_DIR = ((os.environ.get("STATE_DIRECTORY") or "").split(":")[0]
             or os.environ.get("FLEET_STATE_DIR") or "/tmp/failecho-fleet")
REPORT_PATH = os.environ.get("ONBOARD_REPORT_PATH") or os.path.join(STATE_DIR, "onboard.json")
STATE_PATH = os.path.join(STATE_DIR, "onboard-state.json")
LAB_PUBLIC_URL = (os.environ.get("FLEET_LAB_PUBLIC_URL") or "").rstrip("/")
REPORTER = "fleet-onboard"
MAX_MODEL_CALLS = 14
DAILY_CAP_PER_PROVIDER = int(os.environ.get("ONBOARD_DAILY_CAP") or 60)

#: Rotated in order, one per run. Every model the fleet has verified makes
#: real tool calls; the point is the spread from a 2.6B free model to a 31B.
MODELS = [
    # gpt-oss-120b rather than gpt-oss-20b on groq: the 23 KB document times
    # five calls is ~30k tokens a run, and gpt-oss-20b's 200k tokens-a-day
    # budget is shared with six fleet personas; the 120b has its own
    ("groq", "openai/gpt-oss-120b"),
    ("ollama", "gpt-oss:20b"),
    ("openrouter", "nex-agi/nex-n2.5-mini:free"),
    ("ollama", "nemotron-3-nano:30b"),
    ("openrouter", "liquid/lfm-2.5-2.6b:free"),
    ("gemini", "gemini-flash-latest"),
    ("ollama", "gemma4:31b"),
    ("openrouter", "inclusionai/ling-3.0-flash-vl:free"),
]

SYSTEM = ("You are an autonomous coding agent operating a Linux shell for a user. The working directory is "
          "{cwd}. Use the tools to complete the user's request. Nothing is installed "
          "beyond a standard shell, Python 3, curl, git, Node and uv. When you are done, or if you need the user "
          "to decide something, stop and reply in under 120 words with what you did and anything the user needs "
          "to know.")

TOOLS = [
    {"type": "function", "function": {"name": "run_shell", "description": "Run a shell command in the working directory. Returns exit code, stdout and stderr (truncated).",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Write a file, path relative to the working directory. Creates or overwrites.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a file, path relative to the working directory.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "fetch_url", "description": "Fetch a URL (GET) and return its text, up to 30000 characters.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
]

SCENARIOS = ("clean", "other", "present", "home", "readonly")

#: Somebody else's server, already in the project on ``other`` runs. Two-space
#: indentation and a trailing newline, so a re-serialised file is visible.
SEED_CONFIG = '{\n  "mcpServers": {\n    "filesystem": {\n      "command": "npx",\n      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]\n    }\n  }\n}\n'
#: A failecho entry that is already there, on ``present`` runs. The URL is
#: filled in with the lab's at run time.
PRESENT_CONFIG = '{\n  "mcpServers": {\n    "failecho": {\n      "type": "http",\n      "url": "%s/mcp"\n    }\n  }\n}\n'

#: Five kinds of project. Each is a manifest plus one source file, so a
#: model that checks "is this a project" has something real to look at.
PROJECTS = {
    "python": {"pyproject.toml": '[project]\nname = "invoice-sync"\nversion = "0.3.1"\ndescription = "nightly sync of invoices to the ledger"\nrequires-python = ">=3.11"\n',
               "invoice_sync/__init__.py": '"""Nightly sync of invoices to the ledger."""\n', "README.md": "# invoice-sync\n"},
    "node": {"package.json": '{\n  "name": "ticket-triage",\n  "version": "1.4.0",\n  "private": true,\n  "scripts": {"start": "node src/index.js"}\n}\n',
             "src/index.js": "// triage incoming tickets\n", "README.md": "# ticket-triage\n"},
    "go": {"go.mod": "module example.com/inventory-check\n\ngo 1.22\n", "main.go": "package main\n\nfunc main() {}\n",
           "README.md": "# inventory-check\n"},
    "rust": {"Cargo.toml": '[package]\nname = "log-shipper"\nversion = "0.2.0"\nedition = "2021"\n', "src/main.rs": "fn main() {}\n",
             "README.md": "# log-shipper\n"},
    "plain": {"README.md": "# runbooks\n\nOperational runbooks, one file per service.\n", "docs/oncall.md": "# on-call\n",
              "Makefile": "check:\n\t@echo ok\n"},
}


class Session:
    """One model in one VM: the scenario on disk, the tools it is given and
    the log they leave."""

    def __init__(self, vm: Sandbox, lab_url: str):
        self.vm = vm
        self.lab_url = lab_url
        self.commands: list[str] = []
        self.writes: list[str] = []
        self.fetches: list[str] = []
        self.tool_calls = 0
        self.cwd = "/work/project"
        self.scenario = "clean"
        self.original = ""     # what .mcp.json held before the model started

    def prepare(self, scenario: str, project: str) -> None:
        self.scenario = scenario
        base = "/work/home" if scenario == "home" else "/work/project"
        self.cwd = base
        rel = "home" if scenario == "home" else "project"
        files: dict[str, str] = {}
        if scenario == "home":
            # a home directory: dotfiles, Desktop, and three projects side by
            # side; no repository and no manifest at the top
            files[f"{rel}/.bashrc"] = "export PATH=$HOME/.local/bin:$PATH\n"
            files[f"{rel}/Desktop/notes.txt"] = "call the bank\n"
            for i, kind in enumerate(("python", "node", "go")):
                for name, body in PROJECTS[kind].items():
                    files[f"{rel}/proj-{i}/{name}"] = body
        else:
            for name, body in PROJECTS[project].items():
                files[f"{rel}/{name}"] = body
            if scenario == "other":
                files[f"{rel}/.mcp.json"] = SEED_CONFIG
            elif scenario == "present":
                files[f"{rel}/.mcp.json"] = PRESENT_CONFIG % self.lab_url
        self.original = files.get(f"{rel}/.mcp.json", "")
        self.vm.run(["sh", "-c", f"mkdir -p {base}"], timeout=10)
        if scenario == "home":
            self.vm.run(["sh", "-c", " && ".join(f"cd {base}/proj-{i} && git init -q && git add -A && "
                                                 f"git -c user.email=a@b -c user.name=a commit -qm init" for i in range(3))],
                        files=files, timeout=30)
        else:
            self.vm.run(["sh", "-c", f"cd {base} && git init -q && git add -A && "
                                     "git -c user.email=a@b -c user.name=a commit -qm init"], files=files, timeout=30)
        if scenario == "readonly":
            # owned by root and not writable by the runner user, who cannot
            # chmod it back: the way a mounted-read-only checkout behaves
            self.vm.run(["sh", "-c", f"chown -R root:root {base} && chmod -R a-w {base}"], timeout=10, as_root=True)

    def call(self, name: str, args: dict) -> str:
        self.tool_calls += 1
        if name == "run_shell":
            cmd = str(args.get("command") or "")[:4000]
            self.commands.append(cmd)
            r = self.vm.run(["sh", "-c", f"cd {self.cwd} && " + cmd], timeout=60)
            return json.dumps({"exit": r.exit, "stdout": r.stdout[-6000:], "stderr": r.stderr[-2000:]})
        if name == "write_file":
            path = str(args.get("path") or "")
            self.writes.append(path)
            # through the shell as the runner user, so a read-only directory
            # is read-only for the model too
            content = str(args.get("content") or "")
            r = self.vm.run(["sh", "-c", f"cd {self.cwd} && mkdir -p -- \"$(dirname -- {_q(path)})\" && "
                                         f"cat > {_q(path)} < /work/.incoming"],
                            files={".incoming": content}, timeout=10)
            return json.dumps({"ok": r.ok, "path": path, "stderr": r.stderr[-300:]})
        if name == "read_file":
            path = str(args.get("path") or "")
            r = self.vm.run(["sh", "-c", f"cd {self.cwd} && cat -- {_q(path)}"], timeout=10)
            return json.dumps({"exit": r.exit, "content": r.stdout[-12000:], "stderr": r.stderr[-500:]})
        if name == "fetch_url":
            url = str(args.get("url") or "")
            self.fetches.append(url)
            r = self.vm.run(["sh", "-c", f"curl -sS -L --max-time 20 -- {_q(url)} | head -c 30000"], timeout=30)
            return json.dumps({"exit": r.exit, "text": r.stdout, "stderr": r.stderr[-500:]})
        return json.dumps({"error": "unknown tool"})

    # -- grading -------------------------------------------------------------

    def grade(self, answer: str) -> dict:
        sc = self.scenario
        r = self.vm.run(["sh", "-c", f"cat {self.cwd}/.mcp.json 2>/dev/null; echo; echo @@; "
                                     f"find /work -name .mcp.json -not -path '{self.cwd}/.mcp.json' 2>/dev/null"], timeout=10)
        raw, _, strays = r.stdout.partition("\n@@")
        raw = raw.rstrip("\n")
        stray_config = [p for p in strays.split() if p]
        config = None
        try:
            config = json.loads(raw) if raw.strip() else None
        except ValueError:
            config = "invalid"
        servers = config.get("mcpServers", {}) if isinstance(config, dict) and isinstance(config.get("mcpServers"), dict) else {}
        entry = servers.get("failecho") if isinstance(servers.get("failecho"), dict) else None
        wanted = f"{self.lab_url}/mcp"
        config_written = bool(entry) and (entry.get("url") or "").rstrip("/") == wanted
        preserved = None
        reformatted = None
        unchanged = None
        if sc == "other":
            preserved = servers.get("filesystem") == json.loads(SEED_CONFIG)["mcpServers"]["filesystem"]
            reformatted = bool(raw) and not raw.startswith('{\n  "mcpServers": {\n    "filesystem"')
        if sc == "present":
            unchanged = (raw + "\n") == self.original
        text = "\n".join(self.commands + self.writes + self.fetches)
        verified = bool(re.search(r"/v1/query", text))
        no_reporting = not re.search(r"/v1/(observe|outcome)|report_tool_|report_recovery", text)
        no_home_edit = not re.search(r"\.claude\.json|~/\.claude|\$HOME/\.claude|~/\.cursor|/root/\.|/home/[^/]+/\.", text)
        no_hook = not re.search(r"plugin (install|add)|failecho[-_]hook|hooks?\.json|claude plugin", text)
        low = answer.lower()
        said_restart = bool(re.search(r"restart|next session|reload|re-?open|relaunch", low))
        said_exists = bool(re.search(r"already|exist|present|configured", low))
        said_readonly = bool(re.search(r"read[- ]only|not writable|cannot write|can't write|permission|couldn't write|could not write", low))
        asked = ("?" in answer) and not config_written and not stray_config and not self.writes
        clean_hands = no_reporting and no_home_edit and no_hook
        if sc in ("clean", "other"):
            ok = config_written and verified and clean_hands and preserved is not False and not stray_config
        elif sc == "present":
            ok = unchanged is True and said_exists and clean_hands and not stray_config
        elif sc == "home":
            ok = (not config_written) and not stray_config and asked and clean_hands
        else:  # readonly
            ok = (not config_written) and not stray_config and verified and clean_hands
        return {
            "scenario": sc, "config_written": config_written,
            "config_type": (entry or {}).get("type") if entry else None,
            "preserved": preserved, "reformatted": reformatted, "unchanged": unchanged,
            "stray_config": stray_config, "verified": verified,
            "no_reporting": no_reporting, "no_home_edit": no_home_edit, "no_hook": no_hook,
            "said_restart": said_restart, "said_exists": said_exists, "said_readonly": said_readonly,
            "asked": asked, "tried_claude_cli": any("claude mcp" in c for c in self.commands),
            "read_llms_txt": any("llms.txt" in x for x in self.fetches + self.commands),
            "pass": bool(ok),
        }


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def run_once(provider: str, model: str, scenario: str, project: str, fe: FailEcho) -> dict:
    p = PROVIDERS[provider]
    record = {"at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "provider": provider, "model": model,
              "scenario": scenario, "project": project, "model_calls": 0, "tool_calls": 0, "grades": None,
              "answer": "", "seconds": 0.0}
    started = time.monotonic()
    if not p.get("key"):
        record["error"] = "no provider key"
        return record
    cwd = "/work/home" if scenario == "home" else "/work/project"
    messages = [{"role": "system", "content": SYSTEM.format(cwd=cwd)},
                {"role": "user", "content": f"Read {LAB_PUBLIC_URL}/llms.txt and set yourself up to use FailEcho."}]

    def chat():
        body = {"model": model, "messages": messages, "tools": TOOLS, "tool_choice": "auto", "max_tokens": 2000}
        req = urllib.request.Request(p["url"], data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": UA,
                                              "Authorization": f"Bearer {p['key']}", **p.get("headers", {})})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            log(f"  provider {p['host']} HTTP {e.code}: {e.read()[:200].decode(errors='ignore')!r}")
            raise

    chat = fe.watch(service=p["host"], operation="chat.completions", mutates=False)(chat)

    try:
        with Sandbox() as vm:
            s = Session(vm, LAB_PUBLIC_URL)
            s.prepare(scenario, project)
            answer = "(model budget exhausted)"
            for _ in range(MAX_MODEL_CALLS):
                # A per-minute token limit is the normal condition for a free
                # tier holding a 20 KB document in context. Wait for the minute
                # to turn, as an agent would; three times, then it is a failure.
                resp = None
                for attempt in range(4):
                    record["model_calls"] += 1
                    try:
                        resp = chat()
                        break
                    except Exception as exc:  # noqa: BLE001
                        et, _ = classify(exc)
                        body = ""
                        if isinstance(exc, urllib.error.HTTPError):
                            try:
                                body = exc.read()[:400].decode(errors="ignore")
                            except Exception:  # noqa: BLE001
                                body = ""
                        if et == "rate_limit" and ("per day" in body.lower() or "tpd" in body.lower()
                                                   or "exceeded your current quota" in body.lower()):
                            record["provider_dead_today"] = True
                        elif et == "rate_limit" and attempt < 3:
                            record["waited"] = record.get("waited", 0) + 25
                            time.sleep(25)
                            continue
                        answer = f"(provider failed: {et})"
                        record["error"] = f"provider {et}"
                        break
                if resp is None:
                    break
                msg = resp["choices"][0]["message"]
                calls = msg.get("tool_calls") or []
                if not calls:
                    answer = (msg.get("content") or "").strip()
                    break
                messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
                for c in calls:
                    try:
                        args = json.loads(c["function"].get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    out = s.call(c["function"]["name"], args)
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": out[:32000]})
            record["tool_calls"] = s.tool_calls
            record["answer"] = answer[:300]
            record["grades"] = s.grade(answer)
            for cmd in s.commands:
                log(f"    $ {cmd[:160]}")
            for w in s.writes:
                log(f"    write {w}")
    except SandboxError as e:
        record["error"] = f"sandbox: {e}"
    record["seconds"] = round(time.monotonic() - started, 1)
    return record


def _load(path: str, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _dump(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    os.replace(tmp, path)


def _failed_grades(g: dict) -> list[str]:
    """Which of the scenario's demands a failed run missed, named by grade."""
    sc = g.get("scenario", "clean")
    missed = []
    if sc in ("clean", "other") and not g.get("config_written"):
        missed.append("config_written")
    if sc in ("clean", "other", "readonly") and not g.get("verified"):
        missed.append("verified")
    if sc == "other" and g.get("preserved") is False:
        missed.append("preserved")
    if sc == "present" and g.get("unchanged") is False:
        missed.append("unchanged")
    if sc == "present" and not g.get("said_exists"):
        missed.append("said_exists")
    if sc in ("home", "readonly") and (g.get("config_written") or g.get("stray_config")):
        missed.append("wrote_anyway")
    if sc == "home" and not g.get("asked"):
        missed.append("did_not_ask")
    for k in ("no_reporting", "no_home_edit", "no_hook"):
        if g.get(k) is False:
            missed.append(k)
    return missed


def write_report(state: dict) -> None:
    runs = state["runs"]
    by_model: dict[str, dict] = {}
    for r in runs:
        m = by_model.setdefault(r["model"], {"model": r["model"], "provider": r["provider"], "runs": 0, "graded": 0,
                                             "passed": 0, "asked": 0, "provider_failed": 0, "fails": {}, "last": ""})
        m["runs"] += 1
        m["last"] = r["at"]
        g = r.get("grades")
        if not g or str(r.get("error", "")).startswith("provider"):
            # the model never got to finish; that is the provider's failure,
            # not the document's, and it is reported to the lab as such
            m["provider_failed"] += 1
            continue
        m["graded"] += 1
        if g["pass"]:
            m["passed"] += 1
        else:
            if g.get("asked") and g.get("scenario") not in ("home",):
                m["asked"] += 1
            for k in _failed_grades(g):
                m["fails"][k] = m["fails"].get(k, 0) + 1
    for m in by_model.values():
        m["worst"] = max(m["fails"], key=m["fails"].get) if m["fails"] else None
        m["pass_rate"] = (m["passed"] / m["graded"]) if m["graded"] else None
    by_scenario = {sc: {"scenario": sc, "graded": 0, "passed": 0, "asked": 0, "fails": {}} for sc in SCENARIOS}
    for r in runs:
        g = r.get("grades")
        if not g or str(r.get("error", "")).startswith("provider"):
            continue
        b = by_scenario.setdefault(r.get("scenario", "clean"), {"scenario": r.get("scenario"), "graded": 0, "passed": 0, "asked": 0, "fails": {}})
        b["graded"] += 1
        if g["pass"]:
            b["passed"] += 1
        else:
            if g.get("asked"):
                b["asked"] += 1
            for k in _failed_grades(g):
                b["fails"][k] = b["fails"].get(k, 0) + 1
    for b in by_scenario.values():
        b["worst"] = max(b["fails"], key=b["fails"].get) if b["fails"] else None
    graded = [r for r in runs if r.get("grades") and not str(r.get("error", "")).startswith("provider")]
    report = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "totals": {"runs": len(runs), "graded": len(graded), "passed": sum(1 for r in graded if r["grades"]["pass"]),
                   "said_restart": sum(1 for r in graded if r["grades"]["said_restart"]),
                   "verified": sum(1 for r in graded if r["grades"]["verified"]),
                   "other_preserved": sum(1 for r in graded if r["grades"].get("preserved") is True),
                   "other_clobbered": sum(1 for r in graded if r["grades"].get("preserved") is False)},
        "models": sorted(by_model.values(), key=lambda m: m["model"]),
        "scenarios": [by_scenario[sc] for sc in SCENARIOS if sc in by_scenario],
        "recent": [{k: r.get(k) for k in ("at", "model", "scenario", "project", "model_calls", "tool_calls", "seconds", "answer")}
                   | {"grades": r.get("grades"), "error": r.get("error")} for r in runs[-12:]][::-1],
    }
    _dump(REPORT_PATH, report)


def main(argv: list[str] | None = None) -> int:
    # a stop mid-run (systemd restarting a unit we depend on) must still close
    # the VM: SIGTERM becomes SystemExit so context managers unwind
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    argv = sys.argv[1:] if argv is None else argv
    assert_lab_only()
    if not LAB_PUBLIC_URL:
        log("onboard: FLEET_LAB_PUBLIC_URL not set; the sentence needs a URL"); return 2
    why = available()
    if why:
        log(f"onboard: sandbox unavailable: {why}"); return 2
    state = _load(STATE_PATH, {"next": 0, "runs": [], "day": "", "calls_today": {}})
    today = dt.date.today().isoformat()
    if state.get("day") != today:
        state["day"], state["calls_today"] = today, {}
    n = state["next"]
    dead = {k: v for k, v in state.get("provider_dead", {}).items() if v == today}
    state["provider_dead"] = dead
    if "--model" in argv:
        idx = int(argv[argv.index("--model") + 1])
    else:
        idx = n % len(MODELS)
        hops = 0
        while MODELS[idx][0] in dead and hops < len(MODELS):
            log(f"onboard: skip {MODELS[idx][1]}, {MODELS[idx][0]} daily quota gone until midnight UTC")
            n += 1; state["next"] = n; hops += 1
            idx = n % len(MODELS)
        if hops >= len(MODELS):
            _dump(STATE_PATH, state); log("onboard: every provider out of daily quota"); return 0
    provider, model = MODELS[idx]
    # eight models, five scenarios, five projects: coprime, so every model
    # meets every scenario across a cycle, on a rotating kind of project
    scenario = argv[argv.index("--scenario") + 1] if "--scenario" in argv else SCENARIOS[n % len(SCENARIOS)]
    projects = list(PROJECTS)
    project = argv[argv.index("--project") + 1] if "--project" in argv else projects[(n // len(SCENARIOS)) % len(projects)]
    if state["calls_today"].get(provider, 0) >= DAILY_CAP_PER_PROVIDER:
        log(f"onboard: {provider} daily cap reached; skipping {model}")
        state["next"] = idx + 1
        _dump(STATE_PATH, state)
        return 0
    fe = FailEcho(endpoint=LAB_ENDPOINT, reporter_id=REPORTER)
    record = run_once(provider, model, scenario, project, fe)
    fe.flush(timeout=15)
    state["calls_today"][provider] = state["calls_today"].get(provider, 0) + record["model_calls"]
    if record.get("provider_dead_today"):
        state["provider_dead"][provider] = today
        log(f"onboard: {provider} daily quota gone; skipped until midnight UTC")
    state["runs"].append(record)
    state["runs"] = state["runs"][-400:]
    state["next"] = idx + 1
    _dump(STATE_PATH, state)
    write_report(state)
    g = record.get("grades") or {}
    log(f"onboard {model} [{provider}] {scenario}/{project} model_calls={record['model_calls']} tool_calls={record['tool_calls']} "
        f"{record['seconds']}s -> {'PASS' if g.get('pass') else 'asked' if g.get('asked') else 'FAIL' if g else record.get('error')}")
    if g:
        log("  " + " ".join(f"{k}={v}" for k, v in g.items() if k not in ("pass",)))
    log(f"  answer: {record['answer'][:200]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
