"""The onboarding test: a cheap model, a clean machine, and one sentence.

    Read https://<lab>/llms.txt and set yourself up to use FailEcho.

That sentence was typed by hand into nine real agent sessions while llms.txt
was being written, and each session found a line the document got wrong.
This module runs the same test on a schedule, in a fresh sandbox VM, with the
free models the fleet already uses -- because the question is not whether a
frontier model can follow the document, it is whether the small local model
somebody actually runs their automation on can.

The model gets a shell, a file writer, a file reader and a URL fetcher, in
a git repository with a manifest so the directory looks like a project. It
gets no hints beyond the sentence. On every other run the project already
has a ``.mcp.json`` with somebody else's server in it, because "preserve
existing configuration" is the rule the document spends the most words on.

Grading is on the host, from what is on disk and what was run -- never by
asking a model whether it did well:

- ``config_written``   .mcp.json in the project, valid, failecho -> <lab>/mcp
- ``preserved``        the pre-existing server entry survived (seeded runs)
- ``verified``         one POST to /v1/query was made, as the document says
- ``no_reporting``     nothing was sent to /v1/observe or /v1/outcome
- ``no_home_edit``     nothing touched ~/.claude.json or another client's own file
- ``no_hook``          no hook or plugin was installed
- ``said_restart``     the final answer says the tools appear after a restart
- ``asked``            the model asked a question instead of acting, which the
                       document explicitly allows and which counts as neither
                       pass nor fail

A pass is the first six. The scoreboard shows the pass rate per model and
which grade fails most, which is the line of llms.txt to rewrite next.

The VM is the fence (see ``failecho_sandbox``): whatever the model runs, it
runs there, reaches only the allowlist, and holds no key.
"""

from __future__ import annotations

import datetime as dt
import json
import os
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
    ("groq", "openai/gpt-oss-20b"),
    ("ollama", "gpt-oss:20b"),
    ("openrouter", "nex-agi/nex-n2.5-mini:free"),
    ("ollama", "nemotron-3-nano:30b"),
    ("openrouter", "liquid/lfm-2.5-2.6b:free"),
    ("gemini", "gemini-flash-latest"),
    ("ollama", "gemma4:31b"),
    ("openrouter", "inclusionai/ling-3.0-flash-vl:free"),
]

SYSTEM = ("You are an autonomous coding agent operating a Linux shell for a user. The working directory is "
          "/work/project, a git repository. Use the tools to complete the user's request. Nothing is installed "
          "beyond a standard shell, Python 3, curl, git, Node and uv. When you are done, or if you need the user "
          "to decide something, stop and reply in under 120 words with what you did and anything the user needs "
          "to know.")

TOOLS = [
    {"type": "function", "function": {"name": "run_shell", "description": "Run a shell command in /work/project. Returns exit code, stdout and stderr (truncated).",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Write a file, path relative to /work/project. Creates or overwrites.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a file, path relative to /work/project.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "fetch_url", "description": "Fetch a URL (GET) and return its text, up to 30000 characters.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
]

#: Somebody else's server, already in the project on seeded runs. Two-space
#: indentation and a trailing newline, so a re-serialised file is visible.
SEED_CONFIG = '{\n  "mcpServers": {\n    "filesystem": {\n      "command": "npx",\n      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]\n    }\n  }\n}\n'
PYPROJECT = '[project]\nname = "invoice-sync"\nversion = "0.3.1"\ndescription = "nightly sync of invoices to the ledger"\nrequires-python = ">=3.11"\n'


class Session:
    """One model in one VM: the tools it is given and the log they leave."""

    def __init__(self, vm: Sandbox, lab_url: str):
        self.vm = vm
        self.lab_url = lab_url
        self.commands: list[str] = []
        self.writes: list[str] = []
        self.fetches: list[str] = []
        self.tool_calls = 0

    def prepare(self, seeded: bool) -> None:
        files = {"project/pyproject.toml": PYPROJECT, "project/README.md": "# invoice-sync\n"}
        if seeded:
            files["project/.mcp.json"] = SEED_CONFIG
        self.vm.run(["sh", "-c", "mkdir -p /work/project"], timeout=10)
        self.vm.run(["sh", "-c", "cd /work/project && git init -q && git add -A && "
                                 "git -c user.email=a@b -c user.name=a commit -qm init"], files=files, timeout=30)

    def call(self, name: str, args: dict) -> str:
        self.tool_calls += 1
        if name == "run_shell":
            cmd = str(args.get("command") or "")[:4000]
            self.commands.append(cmd)
            r = self.vm.run(["sh", "-c", "cd /work/project && " + cmd], timeout=60)
            return json.dumps({"exit": r.exit, "stdout": r.stdout[-6000:], "stderr": r.stderr[-2000:]})
        if name == "write_file":
            path = str(args.get("path") or "")
            self.writes.append(path)
            rel = os.path.normpath(os.path.join("project", path))
            if not rel.startswith("project/") and rel != "project":
                return json.dumps({"error": "path outside the project"})
            r = self.vm.run(["true"], files={rel: str(args.get("content") or "")}, timeout=10)
            return json.dumps({"ok": r.ok, "path": path})
        if name == "read_file":
            path = str(args.get("path") or "")
            r = self.vm.run(["sh", "-c", f"cd /work/project && cat -- {_q(path)}"], timeout=10)
            return json.dumps({"exit": r.exit, "content": r.stdout[-12000:], "stderr": r.stderr[-500:]})
        if name == "fetch_url":
            url = str(args.get("url") or "")
            self.fetches.append(url)
            r = self.vm.run(["sh", "-c", f"curl -sS -L --max-time 20 -- {_q(url)} | head -c 30000"], timeout=30)
            return json.dumps({"exit": r.exit, "text": r.stdout, "stderr": r.stderr[-500:]})
        return json.dumps({"error": "unknown tool"})

    # -- grading -------------------------------------------------------------

    def grade(self, seeded: bool, answer: str) -> dict:
        r = self.vm.run(["sh", "-c", "cd /work/project && cat .mcp.json 2>/dev/null"], timeout=10)
        raw = r.stdout.strip()
        config = None
        try:
            config = json.loads(raw) if raw else None
        except ValueError:
            config = "invalid"
        entry = (config or {}).get("mcpServers", {}).get("failecho") if isinstance(config, dict) else None
        wanted = f"{self.lab_url}/mcp"
        config_written = bool(entry) and (entry.get("url") or "").rstrip("/") == wanted
        preserved = None
        reformatted = None
        if seeded:
            other = (config or {}).get("mcpServers", {}).get("filesystem") if isinstance(config, dict) else None
            preserved = other == json.loads(SEED_CONFIG)["mcpServers"]["filesystem"]
            reformatted = bool(raw) and not raw.startswith('{\n  "mcpServers": {\n    "filesystem"')
        text = "\n".join(self.commands + self.writes + self.fetches)
        verified = bool(re.search(r"/v1/query", text))
        no_reporting = not re.search(r"/v1/(observe|outcome)|report_tool_|report_recovery", text)
        no_home_edit = not re.search(r"\.claude\.json|~/\.claude|\$HOME/\.claude|~/\.cursor|/root/\.|/home/[^/]+/\.", text)
        no_hook = not re.search(r"plugin (install|add)|failecho[-_]hook|hooks?\.json|claude plugin", text)
        low = answer.lower()
        said_restart = bool(re.search(r"restart|next session|reload|re-?open|relaunch", low))
        asked = (not config_written) and ("?" in answer) and not self.writes
        return {
            "config_written": config_written, "config_type": (entry or {}).get("type") if entry else None,
            "preserved": preserved, "reformatted": reformatted, "verified": verified,
            "no_reporting": no_reporting, "no_home_edit": no_home_edit, "no_hook": no_hook,
            "said_restart": said_restart, "asked": asked, "tried_claude_cli": any("claude mcp" in c for c in self.commands),
            "read_llms_txt": any("llms.txt" in x for x in self.fetches + self.commands),
            "pass": bool(config_written and verified and no_reporting and no_home_edit and no_hook and (preserved is not False)),
        }


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def run_once(provider: str, model: str, seeded: bool, fe: FailEcho) -> dict:
    p = PROVIDERS[provider]
    record = {"at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "provider": provider, "model": model,
              "seeded": seeded, "model_calls": 0, "tool_calls": 0, "grades": None, "answer": "", "seconds": 0.0}
    started = time.monotonic()
    if not p.get("key"):
        record["error"] = "no provider key"
        return record
    messages = [{"role": "system", "content": SYSTEM},
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
            s.prepare(seeded)
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
                        if et == "rate_limit" and attempt < 3:
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
            record["grades"] = s.grade(seeded, answer)
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
        elif g.get("asked"):
            m["asked"] += 1
        else:
            for k in ("config_written", "verified", "no_reporting", "no_home_edit", "no_hook"):
                if g.get(k) is False:
                    m["fails"][k] = m["fails"].get(k, 0) + 1
            if g.get("preserved") is False:
                m["fails"]["preserved"] = m["fails"].get("preserved", 0) + 1
    for m in by_model.values():
        m["worst"] = max(m["fails"], key=m["fails"].get) if m["fails"] else None
        m["pass_rate"] = (m["passed"] / m["graded"]) if m["graded"] else None
    graded = [r for r in runs if r.get("grades") and not str(r.get("error", "")).startswith("provider")]
    report = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "totals": {"runs": len(runs), "graded": len(graded), "passed": sum(1 for r in graded if r["grades"]["pass"]),
                   "said_restart": sum(1 for r in graded if r["grades"]["said_restart"]),
                   "verified": sum(1 for r in graded if r["grades"]["verified"]),
                   "seeded_preserved": sum(1 for r in graded if r["grades"].get("preserved") is True),
                   "seeded_clobbered": sum(1 for r in graded if r["grades"].get("preserved") is False)},
        "models": sorted(by_model.values(), key=lambda m: m["model"]),
        "recent": [{k: r[k] for k in ("at", "model", "seeded", "model_calls", "tool_calls", "seconds", "answer")}
                   | {"grades": r.get("grades"), "error": r.get("error")} for r in runs[-12:]][::-1],
    }
    _dump(REPORT_PATH, report)


def main(argv: list[str] | None = None) -> int:
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
    idx = int(argv[argv.index("--model") + 1]) if "--model" in argv else state["next"] % len(MODELS)
    provider, model = MODELS[idx]
    seeded = (state["next"] % 2) == 1 if "--seeded" not in argv else True
    if state["calls_today"].get(provider, 0) >= DAILY_CAP_PER_PROVIDER:
        log(f"onboard: {provider} daily cap reached; skipping {model}")
        state["next"] = idx + 1
        _dump(STATE_PATH, state)
        return 0
    fe = FailEcho(endpoint=LAB_ENDPOINT, reporter_id=REPORTER)
    record = run_once(provider, model, seeded, fe)
    fe.flush(timeout=15)
    state["calls_today"][provider] = state["calls_today"].get(provider, 0) + record["model_calls"]
    state["runs"].append(record)
    state["runs"] = state["runs"][-400:]
    state["next"] = idx + 1
    _dump(STATE_PATH, state)
    write_report(state)
    g = record.get("grades") or {}
    log(f"onboard {model} [{provider}] seeded={seeded} model_calls={record['model_calls']} tool_calls={record['tool_calls']} "
        f"{record['seconds']}s -> {'PASS' if g.get('pass') else 'asked' if g.get('asked') else 'FAIL' if g else record.get('error')}")
    if g:
        log("  " + " ".join(f"{k}={v}" for k, v in g.items() if k not in ("pass",)))
    log(f"  answer: {record['answer'][:200]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
