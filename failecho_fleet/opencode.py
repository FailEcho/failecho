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


#: OpenCode provider blocks, by fleet provider name. Zen is OpenCode's own
#: provider; the others are OpenAI-compatible endpoints OpenCode is pointed
#: at, with the key read from the guest's environment for that one run.
OC_PROVIDERS = {
    "zen": {"id": "opencode", "env": "ZEN_API_KEY", "base_url": None},
    "nvidia": {"id": "nvidia", "env": "NVIDIA_API_KEY", "base_url": "https://integrate.api.nvidia.com/v1"},
    "mistral": {"id": "mistral", "env": "MISTRAL_API_KEY", "base_url": "https://api.mistral.ai/v1"},
    "xkiro": {"id": "xkiro", "env": "XKIRO_API_KEY", "base_url": "https://api.xkiro.com/v1"},
}


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


#: OpenCode (a bun binary) is killed inside a 384 MB guest; at 640 MB an
#: eight-step task finished and was then killed on exit (137).
OC_MEM_MIB = 768


def run_opencode(*, reporter: str, asks: bool, task: tuple[str, str], provider: str, model: str, key: str,
                 lab_public_url: str | None, timeout: int = 300) -> dict:
    """One headless OpenCode run in a fresh VM. Returns the ledger entry."""
    out: dict = {"reporter": reporter, "asks": asks, "provider": provider, "model": model, "task": task[0][:160],
                 "completed": False, "tool_calls": 0, "failecho_calls": 0, "steps": 0, "tokens_in": 0, "tokens_out": 0,
                 "seconds": 0.0, "answer": "", "error": None, "tool_names": {}, "exit": None}
    why = available()
    if why:
        out["error"] = f"sandbox unavailable: {why}"
        return out
    prompt, check = task
    lab_mcp = f"{lab_public_url}/mcp" if (asks and lab_public_url) else None
    cfg = opencode_config(provider, model, lab_mcp, reporter)
    files = {"project/opencode.json": json.dumps(cfg, indent=2),
             "project/AGENTS.md": AGENTS_MD_FAILECHO if asks else AGENTS_MD}
    env = {OC_PROVIDERS[provider]["env"]: key, "OPENCODE_DISABLE_AUTOUPDATE": "1", "CI": "1", "TERM": "dumb",
           "NO_COLOR": "1"}
    # `timeout` inside too: after a stream error OpenCode has been seen to
    # sit rather than exit, and the harness timeout would lose the output
    cmd = ("cd /work/project && timeout " + str(max(timeout - 20, 30)) + " opencode run --format json --dir /work/project "
           + shlex.quote(prompt) + " 2>/work/opencode.err; echo EXIT=$?; echo '---RESULT---'; "
             "cat result.json result.txt 2>/dev/null | head -c 2000; echo; echo '---ERR---'; "
             "grep -v 'level=INFO' /work/opencode.err | tail -c 1500; echo '---MCP---'; grep -ci 'mcp' /work/opencode.err")
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
    out["mcp_log_lines"] = int(mcp_lines.strip() or 0) if mcp_lines.strip().isdigit() else 0
    for line in body.splitlines():
        ev.feed(line)
    m = re.search(r"EXIT=(\d+)", body)
    out["seconds"] = round(float(r.get("seconds") or 0), 1)
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
