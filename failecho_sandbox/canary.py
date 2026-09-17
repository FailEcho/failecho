"""The install canary: every advertised install path, from a clean machine, daily.

The setup page says "pip install failecho-autoreport" and "pip install
failecho-mcp" work. They worked on the day they were written, on this box,
with whatever was already installed here. A clean machine is a different
question, and it is the question a stranger's machine asks. So once a day a
fresh sandbox VM -- Ubuntu, Python, nothing of ours -- installs each package
from PyPI through the fence and does what the page tells a reader to do:

1. ``pip install failecho-autoreport`` and import it
2. ``python -m failecho_autoreport check ...`` against the lab
3. ``python -m failecho_autoreport run`` on a two-line script: one call
   observed and reported, the summary line present
4. ``pip install failecho-mcp``, then start ``failecho-mcp`` on stdio and
   complete an MCP handshake: initialize, tools/list, four tools back, the
   same four the HTTP endpoint serves
4b. ``npx -y failecho-mcp`` and ``uvx failecho-mcp``, the two one-line relay
   forms llms.txt offers, each through the same handshake -- Node LTS and uv
   are in the image; both fetch the relay from its registry through the fence
5. the LlamaIndex snippet from the setup page, verbatim: ``pip install
   llama-index-tools-mcp``, ``BasicMCPClient(url).list_tools()``
6. the LangChain snippet, verbatim: ``pip install langchain fastmcp``,
   ``MCPAdapter(url).list_tools()`` -- ``langchain.mcp`` is beta and says its
   API may change, which is exactly why it is re-run daily

The result is a small JSON file the scoreboard shows, and a non-zero exit
that systemd records, so the day a path stops working is the day we know.

Everything talks to the lab, not production. The fence does not let the guest
reach production, and the canary's calls would be first-party traffic that
should not appear as adoption in any case.

    python -m failecho_sandbox canary          # run once, print the result
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import signal
import time

from . import Sandbox, SandboxError, available

LAB_URL = (os.environ.get("CANARY_LAB_URL") or "").rstrip("/")
# systemd joins several StateDirectory= paths with ":"; the first is ours
STATE_DIR = ((os.environ.get("STATE_DIRECTORY") or "").split(":")[0]
             or os.environ.get("FLEET_STATE_DIR") or "/tmp/failecho-fleet")
REPORT_PATH = os.environ.get("CANARY_REPORT_PATH") or os.path.join(STATE_DIR, "canary.json")

RUN_SCRIPT = """import urllib.request
urllib.request.urlopen("https://httpbingo.org/status/200", timeout=20).read()
print("called")
"""

# A stdio MCP client in forty lines: start the relay, initialize, list tools.
# Newline-delimited JSON-RPC, which is what the stdio transport is.
HANDSHAKE = r'''
import json, os, subprocess, sys, time, select
env = dict(os.environ, FAILECHO_URL=os.environ["LAB"] + "/mcp")
cmd = json.loads(os.environ.get("RELAY_CMD") or '["failecho-mcp"]')
p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, env=env, text=True, bufsize=1)
def send(o):
    p.stdin.write(json.dumps(o) + "\n"); p.stdin.flush()
def recv(deadline):
    while time.time() < deadline:
        r, _, _ = select.select([p.stdout], [], [], 0.5)
        if r:
            line = p.stdout.readline()
            if line.strip():
                return json.loads(line)
    raise SystemExit("relay: no answer in time; stderr: " + p.stderr.read()[-800:])
send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18",
      "capabilities": {}, "clientInfo": {"name": "failecho-canary", "version": "0"}}})
init = recv(time.time() + 150)   # npx and uvx download the relay first
send({"jsonrpc": "2.0", "method": "notifications/initialized"})
send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
tools = recv(time.time() + 60)
names = sorted(t["name"] for t in tools["result"]["tools"])
p.terminate()
print(json.dumps({"server": init["result"]["serverInfo"], "tools": names}))
'''

EXPECTED_TOOLS = ["check_tool_failure", "report_recovery_outcome", "report_tool_failure", "report_tool_success"]

# The two framework snippets, as the setup page prints them, wrapped only
# in what it takes to run them and print the tool names.
LLAMAINDEX_SNIPPET = """import asyncio, json, os
from llama_index.tools.mcp import BasicMCPClient

async def main():
    client = BasicMCPClient(os.environ["LAB"] + "/mcp")
    result = await client.list_tools()          # a ListToolsResult; the tools are result.tools
    print(json.dumps(sorted(t.name for t in result.tools)))
asyncio.run(main())
"""
LANGCHAIN_SNIPPET = """import asyncio, json, os
from langchain.mcp import MCPAdapter

async def main():
    async with MCPAdapter(os.environ["LAB"] + "/mcp") as adapter:
        tools = await adapter.list_tools()
    print(json.dumps(sorted(getattr(t, "name", None) or t["name"] for t in tools)))
asyncio.run(main())
"""


def _framework_step(vm: Sandbox, steps: list, env: dict, label: str, venv: str, packages: list[str],
                    snippet: str, filename: str) -> None:
    """One framework path: its own venv (they do not share dependency sets),
    the page's pip line, the page's snippet, four tool names back."""
    r = vm.run(["python3", "-m", "venv", venv], timeout=60)
    if not r.ok:
        steps.append({"name": f"{label}: venv", "ok": False, "seconds": r.get("seconds"), "detail": r.stderr[-300:]})
        return
    r = vm.run([f"{venv}/bin/pip", "install", "--quiet", "--no-cache-dir", *packages], timeout=420)
    steps.append({"name": f"pip install {' '.join(packages)}", "ok": r.ok, "seconds": r.get("seconds"),
                  "detail": "" if r.ok else (r.stderr.strip().splitlines() or [""])[-1][:300]})
    if not r.ok:
        return
    r = vm.run([f"{venv}/bin/python", filename], files={filename: snippet}, timeout=120, env=env)
    try:
        got = json.loads(r.stdout.strip().splitlines()[-1]) if r.ok and r.stdout.strip() else None
    except ValueError:
        got = None
    steps.append({"name": f"{label} snippet: list_tools -> 4 tools", "ok": got == EXPECTED_TOOLS,
                  "seconds": r.get("seconds"),
                  "detail": ", ".join(got) if got else (r.stderr.strip().splitlines() or [""])[-1][:300]})


def run_canary(vm: Sandbox, lab_url: str) -> list[dict]:
    steps: list[dict] = []
    env = {"FAILECHO_ENDPOINT": lab_url, "FAILECHO_REPORTER_ID": "install-canary", "LAB": lab_url}

    def step(name: str, r, ok: bool, detail: str = "") -> None:
        steps.append({"name": name, "ok": bool(ok), "seconds": r.get("seconds"),
                      "detail": (detail or (r.stderr.strip().splitlines() or [""])[-1])[:300]})

    r = vm.run(["python3", "-m", "venv", "/work/venv"], timeout=60)
    step("python -m venv", r, r.ok)
    if not r.ok:
        return steps
    pip, py = "/work/venv/bin/pip", "/work/venv/bin/python"

    r = vm.run([pip, "install", "--quiet", "failecho-autoreport"], timeout=180)
    step("pip install failecho-autoreport", r, r.ok)
    if r.ok:
        r = vm.run([py, "-c", "import failecho_autoreport as a; print(a.__version__)"], timeout=30)
        step("import failecho_autoreport", r, r.ok, r.stdout.strip())
        wrapper_version = r.stdout.strip() if r.ok else None
    else:
        wrapper_version = None

    r = vm.run([py, "-m", "failecho_autoreport", "check", "api.github.com", "GET /repos", "not_found", "404"],
               timeout=60, env=env)
    step("failecho_autoreport check (lab)", r, r.ok and "known:" in r.stdout,
         (r.stdout.strip().splitlines()[1:2] or [""])[0].strip())

    r = vm.run([py, "-m", "failecho_autoreport", "run", "hello.py"], files={"hello.py": RUN_SCRIPT}, timeout=90, env=env)
    reported = [l for l in r.stderr.splitlines() if l.startswith("[failecho] reported")]
    step("failecho_autoreport run (one call observed)", r,
         r.ok and "called" in r.stdout and bool(reported) and "reported 1 call" in reported[0],
         reported[0] if reported else "")

    r = vm.run([pip, "install", "--quiet", "failecho-mcp"], timeout=300)
    step("pip install failecho-mcp", r, r.ok)
    if r.ok:
        r = vm.run([py, "-c", "import failecho_mcp as m; print(m.__version__)"], timeout=30)
        step("import failecho_mcp", r, r.ok, r.stdout.strip())
        r = vm.run([py, "handshake.py"], files={"handshake.py": HANDSHAKE}, timeout=150,
                   env={**env, "PATH": "/work/venv/bin:/usr/local/bin:/usr/bin:/bin"})
        try:
            got = json.loads(r.stdout.strip().splitlines()[-1]) if r.ok else {}
        except ValueError:
            got = {}
        step("failecho-mcp stdio handshake: 4 tools", r, r.ok and got.get("tools") == EXPECTED_TOOLS,
             f"{got.get('server', {}).get('name', '?')} -> {', '.join(got.get('tools') or [])}" if got else "")
    for label, cmd, timeout in (("npx -y failecho-mcp", ["npx", "-y", "failecho-mcp"], 200),
                                ("uvx failecho-mcp", ["uvx", "failecho-mcp"], 280)):
        r = vm.run([py, "handshake.py"], files={"handshake.py": HANDSHAKE}, timeout=timeout,
                   env={**env, "RELAY_CMD": json.dumps(cmd), "NODE_USE_ENV_PROXY": "1"})
        try:
            got = json.loads(r.stdout.strip().splitlines()[-1]) if r.ok else {}
        except ValueError:
            got = {}
        step(f"{label} stdio handshake: 4 tools", r, r.ok and got.get("tools") == EXPECTED_TOOLS,
             f"{got.get('server', {}).get('name', '?')} -> {', '.join(got.get('tools') or [])}" if got else "")

    _framework_step(vm, steps, env, "LlamaIndex", "/work/venv-li", ["llama-index-tools-mcp"],
                    LLAMAINDEX_SNIPPET, "li.py")
    _framework_step(vm, steps, env, "LangChain", "/work/venv-lc", ["langchain", "fastmcp"],
                    LANGCHAIN_SNIPPET, "lc.py")
    steps.append({"name": "wrapper_version", "ok": True, "seconds": 0, "detail": wrapper_version or "?"})
    return steps


def main() -> int:
    # a stop mid-run (systemd restarting a unit we depend on) must still close
    # the VM: SIGTERM becomes SystemExit so context managers unwind
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    if not LAB_URL:
        print("canary: CANARY_LAB_URL not set; refusing to guess an endpoint")
        return 2
    started = time.monotonic()
    report = {"at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "lab": LAB_URL, "steps": [], "ok": False}
    why = available()
    if why:
        report["error"] = f"sandbox unavailable: {why}"
    else:
        try:
            # two framework installs need more scratch than a builder run
            with Sandbox(scratch_mib=1536) as vm:
                report["boot_seconds"] = vm.boot_seconds
                report["steps"] = run_canary(vm, LAB_URL)
        except SandboxError as e:
            report["error"] = str(e)
    checks = [s for s in report["steps"] if s["name"] != "wrapper_version"]
    report["ok"] = bool(checks) and all(s["ok"] for s in checks) and "error" not in report
    report["seconds"] = round(time.monotonic() - started, 1)
    os.makedirs(os.path.dirname(REPORT_PATH) or ".", exist_ok=True)
    tmp = REPORT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report, fh)
    os.replace(tmp, REPORT_PATH)
    for s in report["steps"]:
        print(f"  {'PASS' if s['ok'] else 'FAIL'}  {s['name']}  {s['detail']}")
    print(f"canary: {'all paths work' if report['ok'] else 'A PATH IS BROKEN'} ({report['seconds']}s)"
          + (f" -- {report['error']}" if report.get("error") else ""))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
