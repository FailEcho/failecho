"""The builder personas: agents that write and run code, the way people use them.

The rest of the fleet looks things up. That is a fraction of what agents do
all day; most of it is "build this small thing", which means writing code,
installing packages, reading a doc page, running the code, reading the
traceback, fixing it and running it again. The failures in that loop are of
two kinds, and telling them apart is the point of these two personas:

* **local** -- the agent's own bug. A traceback in code it just wrote, a
  package that does not exist because it guessed the name, a dependency
  conflict it created. Counted here; never reported, because nobody else will
  ever hit that exact failure and the network would be noise.
* **shared** -- the world's fault. PyPI timing out mid-install, the GitHub
  API answering 403 at the shared rate limit, a doc site returning 503.
  Reported, because the next agent will hit exactly that.

The ratio between the two, on realistic tasks, is what the scoreboard's
``build`` cohort shows. It is the one number that says how much of an
agent's pain a shared network could ever address.

Where the code runs: never on this host. ``run_python`` and
``resolve_python_deps`` execute inside a throwaway microVM
(``failecho_sandbox``) with a read-only root, no secrets, and a single fenced
path to an allowlist of hosts. The model's code inside the VM runs under
``failecho_autoreport run``, so its own HTTP calls are observed there and
reported to the lab under this persona's reporter id -- from inside the
sandbox, through the fence, like any other agent.

Only ``fetch_doc`` runs on the host, as a plain read of an allowlisted doc
page, and it goes through the same ask-then-recover discipline as the other
personas' tools, so the ask/blind twins still differ in exactly one thing.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

from failecho_autoreport import classify

from failecho_sandbox import Sandbox, SandboxError, available

#: The pages a builder may read from the host. The same list the sandbox
#: proxy enforces for the guest, minus the lab.
DOC_HOSTS = ("docs.python.org", "peps.python.org", "packaging.python.org",
             "developer.mozilla.org", "pypi.org", "registry.npmjs.org", "api.github.com")

#: Realistic small jobs. Each one is a thing somebody has asked an agent to
#: do this week. Half touch shared services in ways that can fail for shared
#: reasons; two are purely local so the ratio has a floor.
BUILDER_TASKS = [
    "Write a script that fetches the latest 5 releases of astral-sh/uv from the GitHub API "
    "(https://api.github.com/repos/astral-sh/uv/releases) and prints tag and published date, one per line. Run it.",
    "Build a tiny CLI, pkginfo.py, that takes a PyPI package name and prints its latest version, license and "
    "how many releases it has, using https://pypi.org/pypi/<name>/json. Run it for requests and httpx.",
    "Check whether these requirements can be installed together: requests==2.31.0, httpx, urllib3<2. "
    "Use resolve_python_deps and explain the result in one sentence.",
    "Write a link counter: fetch https://peps.python.org/pep-0008/ and report how many href links it has and how "
    "many point at docs.python.org. Use only the standard library. Run it.",
    "Write json2csv.py that converts a JSON array of objects to CSV on stdout. Test it on the 'slides' array "
    "from https://httpbingo.org/json. Run it.",
    "Write a retry helper with exponential backoff (0.5s, 1s, 2s) and test it against "
    "https://httpbingo.org/status/200,200,503 ten times. Print how many calls needed a retry. Run it.",
    "Compare the latest numpy version on PyPI with the latest release tag of numpy/numpy on GitHub and say "
    "whether they match. Run it.",
    "Print a markdown table of stars for pallets/flask, psf/requests, encode/httpx, fastapi/fastapi and "
    "astral-sh/uv from the GitHub API, most stars first. Run it.",
    "Write a rate-limit-aware client: call https://httpbingo.org/status/200,429 twenty times; on 429 sleep 1s and "
    "retry once. Print successes, retries and total seconds. Run it.",
    "Write slugify(text) that lowercases, replaces runs of non-alphanumerics with one hyphen and trims hyphens. "
    "Add five unittest cases and run them.",
    "Install the package 'rich' and use it to print a table of the first ten entries of sys.builtin_module_names "
    "with their length. Run it.",
    "Fetch https://registry.npmjs.org/express and print the latest version and how many dependencies that "
    "version declares. Run it.",
    # harder: pagination, dates, headers, real parsing, a multi-step job. Added
    # 2026-09-17 after 30 of the first 41 runs came out clean.
    "Fetch the 3 most recent closed pull requests of astral-sh/uv (GitHub API, state=closed, per_page=3) and "
    "print title, merged date as ISO-8601 UTC, and how many days each stayed open. Handle a missing merged_at. Run it.",
    "Page through https://api.github.com/repos/pallets/flask/releases with per_page=5, following the Link header, "
    "until you have 12 releases; print each tag and the days since the previous release. Run it.",
    "Download https://peps.python.org/pep-0020/ and extract the 19 aphorisms of the Zen of Python with the standard "
    "library only (html.parser). Print them numbered. Run it.",
    "Build a small ETL: fetch https://httpbingo.org/json, flatten slideshow.slides into rows (title, type, item), "
    "write out.csv, then POST the CSV text to https://httpbingo.org/post and print the length of the echoed 'data'. Run it.",
    "Write a cache-aware GitHub client: GET https://api.github.com/repos/psf/requests, keep the ETag, GET it again "
    "with If-None-Match, and print both status codes and X-RateLimit-Remaining after each. Run it.",
    "Parse https://peps.python.org/peps.rss/ with xml.etree and print the 5 most recent PEP titles with publication "
    "dates converted to UTC ISO-8601. Run it.",
    "Verify the latest requests wheel: read https://pypi.org/pypi/requests/json, pick the newest bdist_wheel, download "
    "it from files.pythonhosted.org, SHA-256 it, and compare with the digest PyPI reports. Print match or mismatch. Run it.",
    "Write a polite crates.io client: fetch https://crates.io/api/v1/crates/<name> for serde, tokio, reqwest, clap and "
    "anyhow at no more than one request per second, retrying once on 429 using Retry-After; print name, max_version "
    "and total seconds. Run it.",
]

BUILDER_SCHEMAS = [
    {"type": "function", "function": {
        "name": "run_python",
        "description": "Run Python 3.12 code in a sandbox with network access to pypi.org, api.github.com, "
                       "registry.npmjs.org, crates.io, httpbingo.org and the Python docs. requests and httpx are installed. "
                       "Optionally pip-install packages first. Returns exit code, stdout and stderr.",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "description": "the complete program"},
            "filename": {"type": "string", "description": "optional, default task.py"},
            "requirements": {"type": "array", "items": {"type": "string"},
                             "description": "pip requirements to install before running, e.g. [\"rich\"]"}},
            "required": ["code"]}}},
    {"type": "function", "function": {
        "name": "resolve_python_deps",
        "description": "Resolve a set of pip requirements against PyPI (uv pip compile) without installing. "
                       "Returns the pinned set, or the conflict.",
        "parameters": {"type": "object", "properties": {
            "requirements": {"type": "array", "items": {"type": "string"}}}, "required": ["requirements"]}}},
    {"type": "function", "function": {
        "name": "fetch_doc",
        "description": "Fetch a documentation or registry page as text (first 6000 characters). Allowed hosts: "
                       + ", ".join(DOC_HOSTS) + ".",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
]

SYSTEM_PROMPT = (
    "You are a developer agent. Build what the user asks, in Python 3.12, using the tools. Write the whole "
    "program in one run_python call, read the result, and if it fails fix the code and run again -- at most "
    "four runs. Read a doc page with fetch_doc only if you are unsure of an API. When it works, or when you have "
    "tried four times, stop and report in under 80 words what happened, including any error you could not fix."
)

#: Signals in a traceback or a pip/uv log that the failure was the world's,
#: not the code's. First match wins; anything else that exits non-zero is
#: local. Conservative on purpose: a local bug misfiled as shared is noise
#: in the network, a shared failure misfiled as local is only a lower ratio.
SHARED_SIGNALS = (
    # same order as the wrapper's ERROR_CLASSES, so a 403 that says "rate limit"
    # lands on rate_limit here exactly as it does when the wrapper sees it live
    ("timeout", re.compile(r"timed? ?out|ReadTimeout|ConnectTimeout|TimeoutError|deadline", re.I)),
    ("rate_limit", re.compile(r"HTTP Error 429|\b429\b|rate.?limit|Too Many Requests", re.I)),
    ("auth_error", re.compile(r"HTTP Error 40[13]\b|\b403 Forbidden\b", re.I)),
    ("connection_error", re.compile(r"ConnectionError|ConnectionReset|RemoteDisconnected|Connection refused|"
                                    r"Failed to establish a new connection|ProxyError|"
                                    r"error sending request|Network is unreachable", re.I)),
    ("server_error", re.compile(r"HTTP Error 5\d\d|status(?: code)? 5\d\d|\b50[234]\b|Bad Gateway|Service Unavailable", re.I)),
    # pip's generic wrapper line, after the specific signals so a status in
    # the same message wins
    ("connection_error", re.compile(r"Could not fetch URL", re.I)),
)
#: pip and uv name the index in their errors; the traceback of a script does
#: not name the host, and we do not go looking in the model's code for it.
INDEX_HOSTS = re.compile(r"(files\.pythonhosted\.org|pypi\.org)")
#: The in-guest wrapper's summary line, e.g. "reported 12 calls (3 failures)".
_SUMMARY_FAILURES = re.compile(r"reported \d+ calls? \((\d+) failures?\)")
_SUMMARY_CALLS = re.compile(r"reported (\d+) calls?")


#: The fence refusing a host is the sandbox's doing, not the world's. A
#: builder that reached for github.com when only api.github.com was allowed
#: put a github.com "auth_error/443" into the lab on night one; the host is
#: allowed now, and any future refusal is filed as local.
_FENCE = re.compile(r"Tunnel connection failed: 403|403 Forbidden.*172\.16\.0\.1|Network is unreachable", re.I)


def classify_output(stderr: str, stdout: str = "") -> tuple[str, str | None]:
    """('shared', error_type) or ('local', None) for a failed run's output."""
    text = (stderr or "")[-8000:] + "\n" + (stdout or "")[-2000:]
    if _FENCE.search(text):
        return "local", None
    for et, pattern in SHARED_SIGNALS:
        if pattern.search(text):
            return "shared", et
    return "local", None


#: What classify() needs to see to land on the same error_type the wrapper
#: would have produced for the live exception. The log text itself stays here.
_CANON = {"server_error": "503 service unavailable", "rate_limit": "429 rate limit",
          "auth_error": "403 forbidden", "timeout": "timed out", "connection_error": "connection error"}


class _Shared(Exception):
    """A shared failure seen in a subprocess's output, shaped for classify()."""

    def __init__(self, error_type: str, text: str):
        super().__init__(_CANON.get(error_type, error_type))
        self.error_type = error_type


def fetch_doc(url: str) -> dict:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host not in DOC_HOSTS:
        raise ValueError(f"host not allowed: {host}")
    req = urllib.request.Request(url, headers={"User-Agent": "failecho-fleet/0.1 (+https://failecho.com; lab)",
                                               "Accept": "text/html, application/json, text/plain"})
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read(200_000).decode("utf-8", "replace")
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return {"url": url, "text": text[:6000]}


PROXY_STATS = os.environ.get("FAILECHO_SANDBOX_PROXY_STATS") or "/run/failecho-sandbox/proxy-stats.json"
LAB_HOSTS = ("lab.failecho.com",)


def _proxy_connections() -> int | None:
    """Connections the fence has opened for the guest so far, excluding the
    ones to the lab (that is the wrapper reporting, not the task's traffic)."""
    try:
        with open(PROXY_STATS, encoding="utf-8") as fh:
            d = json.load(fh)
        return sum(n for host, n in d.get("by_host", {}).items() if host not in LAB_HOSTS)
    except (OSError, ValueError):
        return None


class Builder:
    """The VM side of a builder run: one sandbox for the whole run, a venv
    made on first use, and the local/shared ledger."""

    def __init__(self, reporter: str, lab_public_url: str | None, fe):
        self.reporter = reporter
        self.fe = fe
        self.vm: Sandbox | None = None
        self.venv_ready = False
        self.vm_runs = 0
        self.local_failures = 0
        self.shared_failures: list[dict] = []
        self.last_ok = False
        self.boot_error: str | None = None
        #: Coverage of the zero-code wrapper on code nobody wrote for it: per
        #: run_python, how many connections the fence saw the task open and
        #: how many calls the wrapper said it observed. A run with traffic
        #: and nothing observed is a client the wrapper does not patch.
        self.coverage: list[dict] = []
        # What the guest is told, and all it is told. No endpoint means the
        # in-guest wrapper stays off (FAILECHO_DISABLED), never a default.
        if lab_public_url:
            self.guest_env = {"FAILECHO_ENDPOINT": lab_public_url, "FAILECHO_REPORTER_ID": reporter,
                              "FAILECHO_REPORT_SUCCESS": "1"}
        else:
            self.guest_env = {"FAILECHO_DISABLED": "1"}

    def __enter__(self) -> "Builder":
        why = available()
        if why:
            self.boot_error = why
            return self
        try:
            self.vm = Sandbox()
            self.vm.boot()
        except SandboxError as e:
            self.boot_error = str(e)
            self.vm = None
        return self

    def __exit__(self, *exc) -> None:
        if self.vm is not None:
            self.vm.close()

    # -- the ledger ------------------------------------------------------

    def _account(self, r, service_hint: str | None) -> dict:
        """Turn a task result into the tool's answer, and file the failure."""
        out = {"exit": r.exit, "stdout": r.stdout[-1500:], "stderr": r.stderr[-1500:],
               "seconds": r.get("seconds"), "timed_out": bool(r.get("timed_out"))}
        if r.ok:
            self.last_ok = True
            return out
        self.last_ok = False
        kind, et = classify_output(r.stderr, r.stdout)
        if r.get("timed_out"):
            kind, et = "local", None     # the code hung; the sandbox cut it
        if kind == "shared":
            m = INDEX_HOSTS.search(r.stderr or "")
            service = m.group(1) if m else service_hint
            rec = {"service": service or "unknown", "error_type": et}
            self.shared_failures.append(rec)
            out["failure"] = "shared"
            # pip and uv talk to the index themselves; the in-guest wrapper
            # only sees the script's own calls, so the host files this one.
            if service and service_hint in ("pip", "uv"):
                exc = _Shared(et, r.stderr)
                self.fe.record_failure(service, service_hint + " install" if service_hint == "pip" else "uv pip compile",
                                       exc, mutates=False)
        else:
            self.local_failures += 1
            out["failure"] = "local"
        return out

    # -- the tools ---------------------------------------------------------

    def _ensure_venv(self) -> dict | None:
        if self.venv_ready:
            return None
        r = self.vm.run(["python3", "-m", "venv", "--system-site-packages", "/work/venv"], timeout=60)
        if not r.ok:
            return {"error": "venv failed", "stderr": r.stderr[-500:]}
        self.venv_ready = True
        return None

    def run_python(self, code: str, filename: str = "task.py", requirements: list | None = None) -> dict:
        if self.vm is None:
            return {"error": f"sandbox unavailable: {self.boot_error}"}
        filename = re.sub(r"[^A-Za-z0-9_.-]", "_", filename or "task.py")[:40] or "task.py"
        if not filename.endswith(".py"):
            filename += ".py"
        problem = self._ensure_venv()
        if problem:
            return problem
        if requirements:
            reqs = [str(x)[:80] for x in requirements][:10]
            self.vm_runs += 1
            r = self.vm.run(["/work/venv/bin/python", "-m", "pip", "install", "--quiet", *reqs], timeout=120,
                            env=self.guest_env)
            if not r.ok:
                return {"step": "pip install", **self._account(r, "pip")}
        self.vm_runs += 1
        before = _proxy_connections()
        r = self.vm.run(["/work/venv/bin/python", "-m", "failecho_autoreport", "run", filename],
                        files={filename: code}, timeout=90, env=self.guest_env)
        after = _proxy_connections()
        # The wrapper's own lines are not the program's output -- but its
        # summary says how many of the program's calls failed, and a failure
        # the program's own retry absorbed is a shared failure the run's exit
        # code will never show. Those are filed here, from the summary.
        summary = next((l for l in r.stderr.splitlines() if l.startswith("[failecho] reported")), "")
        m = _SUMMARY_FAILURES.search(summary)
        absorbed = int(m.group(1)) if m else 0
        mc = _SUMMARY_CALLS.search(summary)
        observed = int(mc.group(1)) if mc else 0
        if before is not None and after is not None:
            connections = max(after - before, 0)
            self.coverage.append({"connections": connections, "observed": observed,
                                  "missed": bool(connections > 0 and observed == 0)})
        r["stderr"] = "\n".join(l for l in r.stderr.splitlines() if not l.startswith("[failecho]"))
        out = {"step": "run", **self._account(r, None)}
        if absorbed and r.ok:
            # reported to the lab from inside the VM already; counted here only
            self.shared_failures.extend({"service": "(inside the run)", "error_type": "reported"} for _ in range(absorbed))
            out["shared_failures_absorbed"] = absorbed
        return out

    def resolve_python_deps(self, requirements: list) -> dict:
        if self.vm is None:
            return {"error": f"sandbox unavailable: {self.boot_error}"}
        reqs = "\n".join(str(x)[:80] for x in (requirements or [])[:20]) + "\n"
        self.vm_runs += 1
        r = self.vm.run(["uv", "pip", "compile", "--no-header", "--quiet", "--python", "/usr/bin/python3",
                         "requirements.in"], files={"requirements.in": reqs}, timeout=90)
        out = self._account(r, "uv")
        if r.ok:
            out["resolved"] = r.stdout.strip().splitlines()[:40]
        return out

    def summary(self) -> dict:
        return {"vm_runs": self.vm_runs, "local_failures": self.local_failures,
                "shared_failures": self.shared_failures, "last_ok": self.last_ok,
                "coverage": self.coverage,
                "sandbox": "ok" if self.vm is not None else f"unavailable: {self.boot_error}"}
