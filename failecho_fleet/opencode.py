"""OpenCode personas: a real agent product, with and without FailEcho.

Every other persona is our own loop around a model. These drive OpenCode
(opencode.ai), headless, inside the sandbox VM, on OpenCode Zen's free
models -- the tier is licensed for use from within OpenCode, and OpenCode
is what runs. The twins get the same task, the same model, the same
project; the ask twin has FailEcho's MCP server in its ``opencode.json``
and one paragraph in ``AGENTS.md`` saying what it is for, the blind twin
has neither. Everything else is OpenCode's own behaviour.

What is measured: whether the task's artefact was produced and passes its
check, seconds, tokens as OpenCode reports them, tool calls, and how many
of those were FailEcho tools. Nothing here reports on the agent's behalf:
the agent asks (or does not), and the network sees only what it sends.

The guest is throwaway and fenced; the Zen key reaches opencode.ai only.
"""

from __future__ import annotations

import json
import re
import shlex

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

AGENTS_MD = """# Task rules

Work only inside this directory. Python 3 and Node are installed; use the
system python (python3) and write files here. Finish by producing the file the
task names. Do not ask questions; decide and act. Keep it under ten steps.
"""

AGENTS_MD_FAILECHO = AGENTS_MD + """
# FailEcho

A FailEcho MCP server is connected. When an HTTP call or tool fails, call
`check_tool_failure` with the service host and the operation BEFORE retrying:
it says whether other agents hit the same failure and what recovery worked, or
`skip` when nothing has. Follow it. Report what you tried with
`report_recovery_outcome`. Send metadata only, never request or response bodies.
"""


def opencode_config(model: str, zen_env: str, lab_mcp_url: str | None, reporter: str) -> dict:
    cfg: dict = {
        "$schema": "https://opencode.ai/config.json",
        "model": f"opencode/{model}",
        "provider": {"opencode": {"options": {"apiKey": "{env:" + zen_env + "}"}}},
        "permission": {"edit": "allow", "bash": "allow", "webfetch": "allow"},
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
            if t in ("step_finish", "step-finish", "finish"):
                self.steps += 1
            tok = node.get("tokens")
            if isinstance(tok, dict):
                self.tokens_in += int(tok.get("input") or 0)
                self.tokens_out += int(tok.get("output") or 0)
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


def run_opencode(*, reporter: str, asks: bool, task: tuple[str, str], model: str, zen_key: str,
                 lab_public_url: str | None, timeout: int = 300) -> dict:
    """One headless OpenCode run in a fresh VM. Returns the ledger entry."""
    out: dict = {"reporter": reporter, "asks": asks, "model": model, "task": task[0][:160], "completed": False,
                 "tool_calls": 0, "failecho_calls": 0, "steps": 0, "tokens_in": 0, "tokens_out": 0,
                 "seconds": 0.0, "answer": "", "error": None, "tool_names": {}, "exit": None}
    why = available()
    if why:
        out["error"] = f"sandbox unavailable: {why}"
        return out
    prompt, check = task
    lab_mcp = f"{lab_public_url}/mcp" if (asks and lab_public_url) else None
    cfg = opencode_config(model, "ZEN_API_KEY", lab_mcp, reporter)
    files = {"project/opencode.json": json.dumps(cfg, indent=2),
             "project/AGENTS.md": AGENTS_MD_FAILECHO if asks else AGENTS_MD}
    env = {"ZEN_API_KEY": zen_key, "OPENCODE_DISABLE_AUTOUPDATE": "1", "CI": "1", "TERM": "dumb", "NO_COLOR": "1"}
    cmd = ("cd /work/project && opencode run --format json --dir /work/project --model opencode/" + shlex.quote(model)
           + " " + shlex.quote(prompt) + " 2>/work/opencode.err; echo EXIT=$?; echo '---RESULT---'; "
             "cat result.json result.txt 2>/dev/null | head -c 2000; echo; echo '---ERR---'; tail -c 1500 /work/opencode.err")
    try:
        with Sandbox(scratch_mib=1536) as vm:
            r = vm.run(["sh", "-c", cmd], files=files, timeout=timeout, env=env)
    except SandboxError as e:
        out["error"] = f"sandbox: {e}"[:200]
        return out
    ev = Events()
    body, _, rest = r.stdout.partition("---RESULT---")
    result, _, err = rest.partition("---ERR---")
    for line in body.splitlines():
        ev.feed(line)
    m = re.search(r"EXIT=(\d+)", body)
    out.update(exit=int(m.group(1)) if m else None, tool_calls=ev.tool_calls, failecho_calls=ev.failecho_calls,
               steps=ev.steps, tokens_in=ev.tokens_in, tokens_out=ev.tokens_out, tool_names=ev.tool_names,
               answer=("\n".join(ev.text))[-300:], timed_out=bool(getattr(r, "timed_out", False)))
    result = result.strip()
    out["result_head"] = result[:200]
    out["completed"] = bool(result) and re.search(check, result) is not None
    if ev.errors:
        out["error"] = ev.errors[-1][:200]
    elif not out["completed"] and err.strip():
        out["error"] = err.strip()[-200:]
    return out
