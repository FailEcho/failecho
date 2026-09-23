"""OpenCode personas: a real agent product, with and without FailEcho.

Every other persona is our own loop around a model. These drive OpenCode
(opencode.ai), headless, inside the sandbox VM, on OpenCode Zen's free
models -- the tier is licensed for use from within OpenCode, and OpenCode
is what runs. The twins get the same task, the same model, the same
project; the ask twin has FailEcho's MCP server in its ``opencode.json``
and one paragraph in ``AGENTS.md`` saying what it is for, the blind twin
has neither. Everything else is OpenCode's own behaviour.

OpenCode is pointed at the fleet's own providers (NVIDIA, Mistral, xKiro)
as OpenAI-compatible endpoints, and at OpenCode Zen's free models when
that tier answers (on 18 Sep it answered "Rate limit exceeded" to every
free model before the first request, and its paid default for titles
"Insufficient account funds"; no paid request is ever made).

What is measured: whether the task's artefact was produced and passes its
check, seconds, tokens as OpenCode reports them, tool calls, and how many
of those were FailEcho tools. Nothing here reports on the agent's behalf:
the agent asks (or does not), and the network sees only what it sends.

The guest is throwaway and fenced; a provider key reaches that provider only.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

from failecho_sandbox import Sandbox, SandboxError, available

#: (prompt, regex the result file must match). Each task ends in a file the
#: harness can read; "run it" alone cannot be graded from outside.
OC_TASKS: list[tuple[str, str]] = [
    ("Using the PyPI JSON API (https://pypi.org/pypi/<name>/json), find the latest version of requests, httpx and "
     "urllib3 and write them to result.json as {\"requests\": \"x\", \"httpx\": \"y\", \"urllib3\": \"z\"}. "
     "Write and run a Python script to do it; do not guess the numbers.",
     r'"requests":\s*"\d+\.\d+'),
    ("Fetch https://api.github.com/repos/astral-sh/uv/releases and write result.json with the five newest tags "
     "and their published dates, as a JSON list of {\"tag\": ..., \"published_at\": ...}. Write and run a script.",
     r'"tag":\s*"'),
    ("Call https://httpbingo.org/status/200,429 twenty times with a small Python script; on a 429 sleep one second "
     "and retry once. Write result.json as {\"successes\": n, \"retries\": n, \"seconds\": s}. Run it.",
     r'"successes":\s*\d+'),
    ("Write pkginfo.py: given a PyPI package name it prints the latest version, the license and the number of "
     "releases using https://pypi.org/pypi/<name>/json. Run it for httpx and save its output to result.txt.",
     r"\d+\.\d+"),
    ("From https://api.github.com get the star counts of pallets/flask, psf/requests and encode/httpx and write "
     "result.json as {\"pallets/flask\": n, ...}. Write and run a script; if the API refuses, say so in result.json "
     "under \"error\" instead of inventing numbers.",
     r'"(pallets/flask|error)":'),
    ("Look up the latest version of the npm package express at https://registry.npmjs.org/express and the latest "
     "version of the crate serde at https://crates.io/api/v1/crates/serde (send a User-Agent). Write result.json as "
     "{\"express\": ..., \"serde\": ...}. Write and run a script.",
     r'"express":\s*"\d'),
]

#: The proxy pair's tasks: the same kind of lookups, through an MCP server's
#: tools rather than a script, because what is measured is what a tool
#: failure looks like to the agent. Both twins are told the same thing.
OCP_TASKS: list[tuple[str, str]] = [
    ("Use the packages tools to find the latest PyPI versions of requests, httpx and urllib3, and write "
     "result.json as {\"requests\": \"x\", \"httpx\": \"y\", \"urllib3\": \"z\"}. Do not guess numbers.",
     r'"requests":\s*"\d+\.\d+'),
    ("Use the packages tools to get the GitHub star counts of pallets/flask, psf/requests and encode/httpx and "
     "write result.json as {\"pallets/flask\": n, ...}. If a tool refuses, put the reason under \"error\" in "
     "result.json instead of inventing numbers.",
     r'"(pallets/flask|error)":'),
    ("Use the packages tools to get the latest release tag of astral-sh/uv and astral-sh/ruff and write "
     "result.json as {\"astral-sh/uv\": \"tag\", \"astral-sh/ruff\": \"tag\"}. If a tool refuses, put the reason "
     "under \"error\" instead of inventing tags.",
     r'"(astral-sh/uv|error)":'),
    ("Use the packages tools to get the latest npm version of express and the latest crates.io version of serde, "
     "and write result.json as {\"express\": \"x\", \"serde\": \"y\"}.",
     r'"express":\s*"\d'),
    ("Use the packages tools' service_status on redis, postgres and kafka, and write result.json as "
     "{\"redis\": \"ok\"|\"failed\", \"postgres\": ..., \"kafka\": ...}. The endpoint is flaky; decide "
     "yourself whether to try again, and record what you got.",
     r'"redis":\s*"(ok|failed)"'),
    ("Use the packages tools' quota_check on redis and postgres and write result.json as "
     "{\"redis\": <remaining or \"error\">, \"postgres\": ...}. If the tool keeps refusing, record the "
     "reason rather than inventing a number.",
     r'"redis":'),
]

#: What the proxy pair's guest needs besides OpenCode: the API server, and
#: for the ask twin the proxy and the wrapper it reports with -- the shipped
#: source files, read from this checkout, so the lab runs what users install.
_REPO = Path(__file__).resolve().parents[1]
#: The hook pair's guest needs the shipped plugin file, read from this
#: checkout so the lab runs what users would install.
PLUGIN_FILE = _REPO / "opencode-plugin" / "plugin" / "failecho.js"

PROXY_FILES = {
    "fe/packages_mcp.py": _REPO / "failecho_fleet" / "guest_packages_mcp.py",
    "fe/proxy.py": _REPO / "failecho_mcp" / "proxy.py",
    "fe/failecho_autoreport/__init__.py": _REPO / "failecho_autoreport" / "__init__.py",
}

AGENTS_MD = """# Task rules

Work only inside this directory. Python 3 and Node are installed; use the
system python (python3) and write files here. Finish by producing the file the
task names. Do not ask questions; decide and act. Keep it under ten steps.
"""

#: The MCP path's instruction, made as strong as an instruction can be
#: (22 Sep 18:0x). Until now it was one polite paragraph and the twin called
#: FailEcho 0.12 times a run while paying for four tool definitions in every
#: step -- and finished fewer tasks than the twin without it. A tool a model
#: must choose loses to the task in front of it. Before deciding that the
#: paste-the-URL path cannot work, it deserves its best shot: a rule, stated
#: as a constraint, with the order of actions spelled out. Whatever this
#: gives is what we will tell users to expect, in either direction.
AGENTS_MD_FAILECHO = AGENTS_MD + """
# FailEcho: rules for failed calls

A FailEcho MCP server is connected. These rules are not optional.

1. **Never retry a failed tool or HTTP call before calling
   `check_tool_failure`.** That call is always your first action after an
   error, before any retry, workaround or apology.
2. Call it with `service` and `operation` as the *server* names them --
   `api.github.com` and `create_issue`, not your own alias -- plus the error
   class and code.
3. Do what it says. `skip` means nothing has fixed this lately: do not retry,
   record why in your answer and move on. A recommendation means try that
   action once.
4. After you try a fix, call `report_recovery_outcome` with the action and
   whether it worked. Both outcomes matter.
5. Metadata only: never a prompt, an argument, a result, a body or a key.

One check costs a second. Retrying blindly into a rate limit costs the task.
"""


#: OpenCode provider blocks, by fleet provider name. Zen is OpenCode's own
#: provider; the others are OpenAI-compatible endpoints OpenCode is pointed
#: at, with the key read from the guest's environment for that one run.
OC_PROVIDERS = {
    "zen": {"id": "opencode", "env": "ZEN_API_KEY", "base_url": None},
    "nvidia": {"id": "nvidia", "env": "NVIDIA_API_KEY", "base_url": "https://integrate.api.nvidia.com/v1"},
    "mistral": {"id": "mistral", "env": "MISTRAL_API_KEY", "base_url": "https://api.mistral.ai/v1"},
    "xkiro": {"id": "xkiro", "env": "XKIRO_API_KEY", "base_url": "https://api.xkiro.com/v1"},
}


def packages_mcp(proxied: bool, lab_public_url: str | None, reporter: str) -> dict:
    """The packages server as an OpenCode local MCP entry; through the
    FailEcho proxy for the ask twin, directly for the blind one."""
    server = ["python3", "/work/fe/packages_mcp.py"]
    env = {"PYTHONPATH": "/work/fe"}
    if proxied:
        # each tool's API host, as a user would declare it, so a GitHub 403
        # can draw on the evidence agents filed under api.github.com
        upstream = ["--upstream", "github_*=api.github.com", "--upstream", "pypi_*=pypi.org",
                    "--upstream", "npm_*=registry.npmjs.org", "--upstream", "crates_*=crates.io",
                    "--upstream", "service_status=httpbingo.org", "--upstream", "quota_check=httpbingo.org"]
        server = ["python3", "/work/fe/proxy.py", *upstream, "--", *server]
        env.update(FAILECHO_ENDPOINT=lab_public_url or "", FAILECHO_REPORTER_ID=reporter)
    return {"type": "local", "command": server, "enabled": True, "environment": env}


def opencode_config(provider: str, model: str, lab_mcp_url: str | None, reporter: str) -> dict:
    oc = OC_PROVIDERS[provider]
    block: dict = {"options": {"apiKey": "{env:" + oc["env"] + "}"}}
    if oc["base_url"]:
        block.update(npm="@ai-sdk/openai-compatible", name=provider, models={model: {"name": model, "tool_call": True}})
        block["options"]["baseURL"] = oc["base_url"]
    full = f"{oc['id']}/{model}"
    cfg: dict = {
        "$schema": "https://opencode.ai/config.json",
        "model": full,
        # the title/summary side-calls too, or OpenCode reaches for a paid
        # default (gpt-5.4-nano on Zen: "Insufficient account funds")
        "small_model": full,
        "provider": {oc["id"]: block},
        # external_directory: a script writing to /tmp was auto-rejected
        # mid-task (18 Sep 13:24, blind twin) -- the harness failing the
        # task, not the agent; the guest is throwaway
        "permission": {"edit": "allow", "bash": "allow", "webfetch": "allow", "external_directory": "allow"},
        "share": "disabled",
        "autoupdate": False,
    }
    if lab_mcp_url:
        cfg["mcp"] = {"failecho": {"type": "remote", "url": lab_mcp_url, "enabled": True,
                                   "headers": {"X-Reporter-ID": reporter}}}
    return cfg


class Events:
    """What ``opencode run --format json`` printed, reduced to counts."""

    def __init__(self) -> None:
        self.events = 0
        self.tool_calls = 0
        self.failecho_calls = 0
        #: tool outputs that carried the proxy's advice line
        self.advice_seen = 0
        self.tool_names: dict[str, int] = {}
        self.steps = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.text: list[str] = []
        self.errors: list[str] = []

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line.startswith("{"):
            return
        try:
            ev = json.loads(line)
        except ValueError:
            return
        self.events += 1
        self._walk(ev)

    def _walk(self, node, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(node, dict):
            t = node.get("type")
            if t == "tool" or ("tool" in node and isinstance(node.get("tool"), str) and "state" in node):
                name = str(node.get("tool") or node.get("name") or "?")
                self.tool_calls += 1
                self.tool_names[name] = self.tool_names.get(name, 0) + 1
                if "failecho" in name:
                    self.failecho_calls += 1
            if depth == 0 and t == "step_finish":   # the part inside says "step-finish" too
                self.steps += 1
            tok = node.get("tokens")
            if isinstance(tok, dict):
                self.tokens_in += int(tok.get("input") or 0)
                self.tokens_out += int(tok.get("output") or 0)
            out = node.get("output")
            # "FailEcho: try ..." and "FailEcho (evidence from api.github.com): ..."
            # are both advice; matching only the first counted zero while the
            # proxy was in fact annotating every GitHub 403 (22 Sep)
            if isinstance(out, str) and "FailEcho" in out:
                self.advice_seen += 1
            if t == "text" and isinstance(node.get("text"), str):
                self.text.append(node["text"])
            if t == "error" or ("error" in node and isinstance(node.get("error"), (str, dict)) and depth == 0):
                self.errors.append(str(node.get("error") or node)[:200])
            for v in node.values():
                if isinstance(v, (dict, list)):
                    self._walk(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                self._walk(v, depth + 1)


#: 768 until 22 Sep: the proxy pair runs OpenCode *and* the proxy *and* the
#: packages server in one guest, and the guest kernel began killing runs
#: mid-flight, losing their reports (10:30: 11 tool calls, 1 report). The host
#: has the room now that its other projects are stopped. 896 from 22 Sep
#: 12:00: the user prefers slower tests to a bigger host.
#: OpenCode (a bun binary) is killed inside a 384 MB guest; at 640 MB an
#: eight-step task finished and was then killed on exit (137).
OC_MEM_MIB = 896


def guest_env(provider: str, key: str, reporter: str, reporting: bool,
               lab_public_url: str | None) -> dict:
    """The environment the guest runs with.

    The endpoint is always explicit when the plugin is installed. A client
    library that starts a process with a minimal environment is how eight rows
    of test traffic reached production as independent adoption on 19 Sep, and
    the plugin's default is production. Where there is no lab URL to point at,
    it gets a dead local port rather than a guess.
    """
    env = {OC_PROVIDERS[provider]["env"]: key, "OPENCODE_DISABLE_AUTOUPDATE": "1", "CI": "1",
           "TERM": "dumb", "NO_COLOR": "1"}
    if reporting:
        env["FAILECHO_ENDPOINT"] = lab_public_url or "http://127.0.0.1:9"
        env["FAILECHO_REPORTER_ID"] = reporter
    return env


def run_opencode(*, reporter: str, asks: bool, task: tuple[str, str], provider: str, model: str, key: str,
                 lab_public_url: str | None, timeout: int = 300, proxy: bool = False,
                 hook: bool = False) -> dict:
    """One headless OpenCode run in a fresh VM. Returns the ledger entry."""
    out: dict = {"reporter": reporter, "asks": asks, "provider": provider, "model": model, "task": task[0][:160],
                 "completed": False, "tool_calls": 0, "failecho_calls": 0, "steps": 0, "tokens_in": 0, "tokens_out": 0,
                 "seconds": 0.0, "answer": "", "error": None, "tool_names": {}, "exit": None}
    why = available()
    if why:
        out["error"] = f"sandbox unavailable: {why}"
        return out
    prompt, check = task
    if hook:
        # The hook pair: no FailEcho tools and no FailEcho paragraph, because
        # the whole point is that the model is never asked to do anything.
        # The ask twin's only difference is a plugin file in the project, and
        # OpenCode fires it around every tool the agent runs.
        cfg = opencode_config(provider, model, None, reporter)
        files = {"project/opencode.json": json.dumps(cfg, indent=2), "project/AGENTS.md": AGENTS_MD}
        if asks:
            files["project/.opencode/plugin/failecho.js"] = PLUGIN_FILE.read_text(encoding="utf-8")
    elif proxy:
        # the proxy pair: no FailEcho tools, no FailEcho paragraph -- the ask
        # twin's only difference is the proxy in front of its API server
        cfg = opencode_config(provider, model, None, reporter)
        cfg["mcp"] = {"packages": packages_mcp(asks, lab_public_url, reporter)}
        files = {"project/opencode.json": json.dumps(cfg, indent=2), "project/AGENTS.md": AGENTS_MD}
        files.update({k: v.read_text(encoding="utf-8") for k, v in PROXY_FILES.items()})
    else:
        lab_mcp = f"{lab_public_url}/mcp" if (asks and lab_public_url) else None
        cfg = opencode_config(provider, model, lab_mcp, reporter)
        files = {"project/opencode.json": json.dumps(cfg, indent=2),
                 "project/AGENTS.md": AGENTS_MD_FAILECHO if asks else AGENTS_MD}
    env = guest_env(provider, key, reporter, hook and asks, lab_public_url)
    # `timeout` inside too: after a stream error OpenCode has been seen to
    # sit rather than exit, and the harness timeout would lose the output
    # -k: OpenCode has ignored the TERM before, and then the guest's own
    # kill took every event with it (14 of 41 runs a twin, 18-19 Sep)
    cmd = ("cd /work/project && timeout -k 10 " + str(max(timeout - 20, 30)) + " opencode run --format json --dir /work/project "
           + shlex.quote(prompt) + " 2>/work/opencode.err; echo EXIT=$?; echo END=$(date +%s); "
             # OpenCode kills its MCP servers on the way out, and the guest is
             # destroyed as soon as this command returns. The proxy flushes its
             # queued reports on exit, and without this pause they were lost
             # whenever a run ended on the clock (22 Sep: 6 tool calls, 2
             # reports). Three seconds against a run of minutes.
             "sleep 3; "
             "echo RESULT_MTIME=$(stat -c %Y result.json result.txt 2>/dev/null | head -1); echo '---RESULT---'; "
             "cat result.json result.txt 2>/dev/null | head -c 2000; echo; echo '---ERR---'; "
             "grep -v 'level=INFO' /work/opencode.err | tail -c 1500; echo '---MCP---'; grep -ci 'mcp' /work/opencode.err; "
             # what the MCP side said, and whether the guest kernel killed
             # anything: the proxy pair's lost reports and 137s (19 Sep)
             "echo '---MCPLOG---'; { cat /work/opencode.err; "
             # OpenCode logs to a file of its own, not stderr (19 Sep: stderr
             # held no MCP lines while the ask twin stalled 6 runs to 1)
             "cat $(ls -t \"$HOME\"/.local/share/opencode/log/*.log 2>/dev/null | head -1) 2>/dev/null; } "
             "| grep -i 'mcp\\|packages\\|proxy\\|failecho\\|timeout\\|level=ERROR\\|level=WARN' | tail -c 1500; "
             "echo '---OOM---'; if dmesg >/dev/null 2>&1; then dmesg | grep -ci 'killed process'; else echo na; fi")
    try:
        with Sandbox(mem_mib=OC_MEM_MIB, scratch_mib=1536) as vm:
            # files land as root; the agent runs as the runner user
            vm.run(["sh", "-c", "chown -R runner:runner /work/project"], files=files, timeout=15, as_root=True)
            r = vm.run(["sh", "-c", cmd], timeout=timeout, env=env)
    except SandboxError as e:
        out["error"] = f"sandbox: {e}"[:200]
        return out
    ev = Events()
    body, _, rest = r.stdout.partition("---RESULT---")
    result, _, err = rest.partition("---ERR---")
    err, _, mcp_lines = err.partition("---MCP---")
    mcp_lines, _, mcp_log = mcp_lines.partition("---MCPLOG---")
    mcp_log, _, oom = mcp_log.partition("---OOM---")
    out["mcp_log_lines"] = int(mcp_lines.strip() or 0) if mcp_lines.strip().isdigit() else 0
    out["mcp_log"] = mcp_log.strip()[-1200:]
    out["guest_oom_kills"] = int(oom.strip()) if oom.strip().isdigit() else None
    for line in body.splitlines():
        ev.feed(line)
    m = re.search(r"EXIT=(\d+)", body)
    out["seconds"] = round(float(r.get("seconds") or 0), 1)
    # How long OpenCode kept running after it wrote its result: separates a
    # process that would not exit from a model that was still working.
    end, mtime = re.search(r"END=(\d+)", body), re.search(r"RESULT_MTIME=(\d+)", body)
    out["idle_after_result"] = int(end.group(1)) - int(mtime.group(1)) if end and mtime else None
    out.update(exit=int(m.group(1)) if m else None, tool_calls=ev.tool_calls, failecho_calls=ev.failecho_calls,
               advice_seen=ev.advice_seen,
               steps=ev.steps, tokens_in=ev.tokens_in, tokens_out=ev.tokens_out, tool_names=ev.tool_names,
               answer=("\n".join(ev.text))[-300:], timed_out=bool(r.get("timed_out")))
    result = result.strip()
    out["result_head"] = result[:200]
    # The grader reads the whole file, the ledger keeps the head. 200
    # characters is plenty to eyeball a run and not enough to grade one: a
    # list of five release tags is about 400, so every such run was being
    # marked invalid for being cut off. Not stored -- the record copies a
    # fixed set of keys, and this is not one of them.
    out["result_text"] = result[:4000]
    out["completed"] = bool(result) and re.search(check, result) is not None
    if ev.errors:
        out["error"] = ev.errors[-1][:200]
    elif not out["completed"] and err.strip():
        out["error"] = err.strip()[-200:]
    return out
