#!/usr/bin/env python3
"""FailEcho hook for Claude Code: report MCP tool failures automatically.

Claude Code runs this after every MCP tool call. No model decides anything,
it costs no tokens, and it uses only the standard library, so it installs as a
single copied file.

On a failure (``PostToolUseFailure``) it asks FailEcho what the network knows,
reports the failure, and -- when there is real evidence -- hands Claude a
short note before it retries. On a success (``PostToolUse``) it reports the
success, which is what makes failure rates mean anything, and if the same tool
had just failed it reports the second attempt as a recovery outcome: ``retry``
when the arguments were unchanged, ``adjust_arguments`` when they changed.

Privacy: it sends which service and tool failed, a coarse error class, and
the call's latency. Never tool arguments, tool results, prompts, file paths or
session ids, and the error text only with ``FAILECHO_HOOK_SEND_ERRORS=1``.
Servers it cannot name the way other users would -- private or local ones --
are skipped entirely; ``FAILECHO_HOOK_SERVICE_NAMES`` names them explicitly.

Install: ``/plugin marketplace add FailEcho/failecho`` then ``/plugin install
failecho@failecho``. By hand instead: copy this file to
``~/.claude/hooks/failecho_hook.py`` and add to ``~/.claude/settings.json``::

    {"hooks": {
      "PostToolUseFailure": [{"matcher": "mcp__.*", "hooks": [{"type": "command",
        "command": "python3 ~/.claude/hooks/failecho_hook.py", "timeout": 10}]}],
      "PostToolUse": [{"matcher": "mcp__.*", "hooks": [{"type": "command",
        "command": "python3 ~/.claude/hooks/failecho_hook.py", "timeout": 10}]}]
    }}

Environment:

    FAILECHO_ENDPOINT               default https://failecho.com
    FAILECHO_DISABLED=1             do nothing at all
    FAILECHO_REPORTER_ID            stable id; default: a random one kept locally
    FAILECHO_OPERATOR_TOKEN         FailEcho's own agents only
    FAILECHO_TEAM                   private mode: a secret your team shares (16+
                                    characters). Reports are stored for your team
                                    alone and your team's own evidence comes back
    FAILECHO_HOOK_SEND_ERRORS=1     also send the error text (normalized server-side)
    FAILECHO_HOOK_REPORT_SUCCESS=0  do not report successful calls
    FAILECHO_HOOK_SERVICE_NAMES     JSON map from a server alias to a public name,
                                    e.g. {"gh": "github-mcp-server"}
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

__version__ = "0.1.0"

DEFAULT_ENDPOINT = "https://failecho.com"
OPERATOR_HEADER = "X-FailEcho-Operator"

#: Per request. After the first network failure in a run the rest are skipped,
#: so an unreachable FailEcho costs Claude one timeout at most, never three.
TIMEOUT_SECONDS = 2.0

#: A second attempt at the same tool within this window counts as an attempt
#: to recover from the earlier failure.
RECOVERY_WINDOW_SECONDS = 30 * 60

#: Host suffixes that name private networks. A failure there is nobody else's
#: failure, and the host name itself may be sensitive.
PRIVATE_SUFFIXES = (
    ".local", ".localhost", ".internal", ".intranet", ".private", ".lan",
    ".home", ".corp", ".localdomain", ".test", ".invalid", ".example",
)

#: Package runners, and their flags that take a value.
NODE_RUNNERS = {"npx", "bunx", "pnpx"}
PYTHON_RUNNERS = {"uvx", "pipx"}
PACKAGE_FLAGS = {"npx": {"-p", "--package"}, "uvx": {"--from"}, "pipx": {"--spec"}}
VALUE_FLAGS = {
    "npx": {"--cache", "--prefix", "--registry", "--userconfig", "-c", "--call"},
    "uvx": {"--python", "-p", "--with", "--with-requirements", "--index",
            "--index-url", "--extra-index-url", "--default-index", "-w"},
    "pipx": {"--python", "--pip-args", "--index-url"},
}

NPM_NAME = re.compile(r"^(@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*$")
PYPI_NAME = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$")
VERSION_ONLY = re.compile(r"^v?\d+(\.\d+)*$")

#: First match wins; order matters ("503 ... upstream" is a server error, not
#: a validation one).
ERROR_CLASSES = (
    ("timeout", re.compile(r"time[d ]?\s?out|deadline exceeded|ETIMEDOUT", re.I)),
    ("rate_limit", re.compile(r"\b429\b|rate.?limit|too many requests|quota", re.I)),
    ("auth_error", re.compile(
        r"\b40[13]\b|unauthori[sz]ed|forbidden|permission denied|authenticat|invalid (api )?key",
        re.I)),
    ("not_found", re.compile(r"\b404\b|not found|no such", re.I)),
    # server_error before validation_error: "503 invalid upstream response"
    # matched "invalid" first and was filed as validation_error/503 (found in
    # review, 20 Sep). A 5xx is the service's, whatever words came with it.
    ("server_error", re.compile(
        r"\b5\d\d\b|internal (server )?error|service unavailable|bad gateway|upstream", re.I)),
    ("validation_error", re.compile(
        r"\b4(00|22)\b|invalid|validation|required|must be|schema", re.I)),
    ("connection_error", re.compile(
        r"ECONN(REFUSED|RESET)|ENOTFOUND|EPIPE|connection (refused|reset|closed|error)"
        r"|network|socket", re.I)),
)
#: Classes where a bare three-digit number in the message is an HTTP status.
HTTP_CLASSES = {"rate_limit", "auth_error", "not_found", "validation_error", "server_error"}


# ---------------------------------------------------------------------------
# naming: evidence is only shared when every user names a server the same way
# ---------------------------------------------------------------------------


def split_tool_name(tool_name: str) -> tuple[str, str] | None:
    """``mcp__<server>__<tool>`` -> (server alias, tool); None otherwise."""
    if not tool_name.startswith("mcp__"):
        return None
    parts = tool_name[len("mcp__"):].split("__", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def is_failecho(server: str) -> bool:
    """FailEcho never reports on itself."""
    return "failecho" in server.lower()


def public_host(url: str) -> str | None:
    """The host of a public URL; None for anything private, local or bare IP."""
    try:
        host = (urlparse(str(url)).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if not host or "." not in host:
        return None
    try:
        ipaddress.ip_address(host)
        return None  # an address says where, not what
    except ValueError:
        pass
    if host.endswith(PRIVATE_SUFFIXES):
        return None
    return host


def _strip_version(spec: str, python: bool) -> str:
    if python:
        return re.split(r"[=<>!~@\[;\s]", spec, maxsplit=1)[0]
    if spec.startswith("@"):
        scope_and_name = spec[1:].split("@", 1)[0]
        return "@" + scope_and_name
    return spec.split("@", 1)[0]


def package_name(command: str, args: list[Any]) -> str | None:
    """The public package a runner such as npx or uvx starts; None otherwise."""
    runner = Path(str(command)).name.lower()
    runner = re.sub(r"\.(cmd|exe|bat)$", "", runner)
    rest = [str(arg) for arg in args]
    if runner == "pnpm" and rest[:1] == ["dlx"]:
        runner, rest = "pnpx", rest[1:]
    if runner == "pipx" and rest[:1] == ["run"]:
        rest = rest[1:]
    if runner not in NODE_RUNNERS | PYTHON_RUNNERS:
        return None
    family = "npx" if runner in NODE_RUNNERS else runner
    python = runner in PYTHON_RUNNERS

    candidate = None
    skip_next = False
    for index, arg in enumerate(rest):
        if skip_next:
            skip_next = False
            continue
        if arg in PACKAGE_FLAGS.get(family, ()):
            candidate = rest[index + 1] if index + 1 < len(rest) else None
            break
        if arg in VALUE_FLAGS.get(family, ()):
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        candidate = arg
        break
    if not candidate:
        return None

    name = _strip_version(candidate.strip(), python).lower()
    if VERSION_ONLY.match(name):
        return None
    pattern = PYPI_NAME if python else NPM_NAME
    return name if pattern.match(name) else None


def _read_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def known_servers(project_dir: str | None) -> dict[str, dict]:
    """Every MCP server Claude Code has configured, by alias.

    Precedence follows Claude Code's own: user, then project (.mcp.json),
    then local (per-project entries in the user config) wins.
    """
    config_files = []
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        config_files.append(Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json")
    config_files.append(Path.home() / ".claude.json")
    configs = [_read_json(path) for path in config_files]

    servers: dict[str, dict] = {}
    for config in reversed(configs):
        servers.update(config.get("mcpServers") or {})
    if project_dir:
        servers.update(_read_json(Path(project_dir) / ".mcp.json").get("mcpServers") or {})
        for config in reversed(configs):
            local = (config.get("projects") or {}).get(project_dir) or {}
            servers.update(local.get("mcpServers") or {})
    return {name: cfg for name, cfg in servers.items() if isinstance(cfg, dict)}


def service_for(server: str, project_dir: str | None) -> str | None:
    """A name other users' agents would also use for this server, or None."""
    try:
        explicit = json.loads(os.environ.get("FAILECHO_HOOK_SERVICE_NAMES") or "{}")
    except ValueError:
        explicit = {}
    named = explicit.get(server) if isinstance(explicit, dict) else None
    if isinstance(named, str) and 0 < len(named.strip()) <= 128:
        return named.strip()

    config = known_servers(project_dir).get(server)
    if not config:
        return None
    if config.get("url"):
        return public_host(config["url"])
    if config.get("command"):
        return package_name(config["command"], config.get("args") or [])
    return None


def classify(error: str) -> tuple[str, str | None]:
    """A coarse error class and code, read locally from the error text."""
    error_type = next(
        (name for name, pattern in ERROR_CLASSES if pattern.search(error)), "tool_error"
    )
    rpc = re.search(r"(-32\d{3})\b", error)
    if rpc:
        return error_type, rpc.group(1)
    if error_type in HTTP_CLASSES:
        status = re.search(r"\b([1-5]\d\d)\b", error)
        if status:
            return error_type, status.group(1)
    return error_type, None


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------


class Client:
    """Fail-soft JSON POSTs. The first network failure disables the rest."""

    def __init__(self, endpoint: str, headers: dict[str, str]) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.headers = headers
        self.down = False

    def post(self, path: str, body: dict[str, Any]) -> dict | None:
        if self.down:
            return None
        payload = json.dumps({k: v for k, v in body.items() if v is not None}).encode()
        request = urllib.request.Request(
            self.endpoint + path,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": f"failecho-claude-code-hook/{__version__}",
                **self.headers,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return json.loads(response.read() or b"{}")
        except (urllib.error.URLError, OSError, ValueError):
            self.down = True
            return None


# ---------------------------------------------------------------------------
# local state: only enough to recognise a second attempt
# ---------------------------------------------------------------------------


def state_dir() -> Path:
    base = os.environ.get("CLAUDE_PLUGIN_DATA") or os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    path = root / "failecho" if not os.environ.get("CLAUDE_PLUGIN_DATA") else root
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def reporter_id() -> str:
    """A random id kept on this machine, so its reports count as one reporter.

    Random on purpose: nothing about the user or the machine goes into it.
    """
    if os.environ.get("FAILECHO_REPORTER_ID"):
        return os.environ["FAILECHO_REPORTER_ID"]
    path = state_dir() / "reporter_id"
    try:
        existing = path.read_text().strip()
        if existing:
            return existing
    except OSError:
        pass
    new = f"claude-code-{uuid.uuid4().hex}"
    try:
        path.write_text(new)
    except OSError:
        pass
    return new


def _digest(value: Any) -> str:
    """A local fingerprint of the tool's arguments. Compared here, never sent."""
    raw = json.dumps(value, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _pending_path(session_id: str, tool_name: str) -> Path:
    key = hashlib.sha256(f"{session_id}\x1f{tool_name}".encode()).hexdigest()[:24]
    return state_dir() / f"pending-{key}.json"


def _load_pending(path: Path) -> dict | None:
    data = _read_json(path)
    if not data or time.time() - float(data.get("at", 0)) > RECOVERY_WINDOW_SECONDS:
        return None
    return data


def _save_pending(path: Path, data: dict) -> None:
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
    except OSError:
        pass


def _clear_pending(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _recovery_action(previous: dict, input_digest: str) -> str:
    return "retry" if previous.get("input") == input_digest else "adjust_arguments"


# ---------------------------------------------------------------------------
# what Claude sees
# ---------------------------------------------------------------------------


def _team_note(answer: dict) -> str | None:
    """The team's own evidence, in private mode. Always said to be the team's."""
    team = answer.get("team_evidence")
    if not isinstance(team, dict):
        return None
    actions = {a.get("action"): a for a in team.get("recovery_actions") or [] if isinstance(a, dict)}
    rec = team.get("recommendation") or {}
    if rec.get("action"):
        a = actions.get(rec["action"]) or {}
        counts = (f" ({a.get('successes')}/{a.get('attempts')} attempts)"
                  if "successes" in a and "attempts" in a else "")
        return f"Your team's own history: {rec['action']} worked{counts}."
    tried = [f"{a['action']} {a.get('successes', 0)}/{a['attempts']}" for a in actions.values() if a.get("attempts")]
    if tried:
        return "Your team's own history, no clear fix yet: " + ", ".join(tried[:3]) + "."
    if team.get("failures"):
        return (f"Your team has hit this {team['failures']} time(s) in the last "
                f"{team.get('window_days', 30)} days; no recovery recorded yet.")
    return None


def context_note(service: str, operation: str, answer: dict | None) -> str | None:
    """A short, factual note for Claude -- only when there is evidence."""
    if not answer:
        return None
    team = _team_note(answer)
    if not answer.get("known"):
        # Nobody else has seen it; the team may have. Private evidence is
        # never passed off as the network's.
        return f"FailEcho: {team} Counts are observed outcomes; no model produced them." if team else None
    observed = answer.get("observations") or {}
    sources = ", ".join(answer.get("evidence_sources") or []) or "unknown"
    # Status needs ten observations an hour; below that it says nothing, and
    # printed next to a well-evidenced recovery it reads as a contradiction.
    status = answer.get("status")
    status_text = (
        f" Current status: {status}." if status and status != "INSUFFICIENT_DATA" else ""
    )
    lines = [
        f"FailEcho: this failure ({service} / {operation}) has been reported "
        f"{observed.get('total', 0)} times, {observed.get('last_1h', 0)} in the last "
        f"hour, by {observed.get('unique_reporters', 0)} reporters "
        f"(evidence from: {sources}).{status_text}"
    ]
    recommendation = answer.get("recommendation")
    if recommendation and recommendation.get("action") == "skip":
        # the network's verdict that nothing works: said as an instruction,
        # because "recovery that worked: skip (0/56)" reads as nonsense
        tried = ", ".join(
            f"{a['action']} 0/{a.get('recent_attempts') or a['attempts']}"
            for a in (answer.get("recovery_actions") or [])[:3]
        )
        lines.append(
            f"Nothing tried in the last 24h has worked ({tried}). Do not retry this call; "
            "fail fast, or try something different and it will be recorded."
        )
    elif recommendation:
        pooled = " (evidence pooled from other operation names on this service)" \
            if recommendation.get("scope") == "service" else ""
        lines.append(
            f"Recovery that worked for others: {recommendation['action']} "
            f"({recommendation.get('based_on_successes')}/"
            f"{recommendation.get('based_on_attempts')} attempts, "
            f"confidence {recommendation.get('confidence')}){pooled}."
        )
    else:
        tried = [
            f"{a['action']} {a['successes']}/{a['attempts']}"
            for a in (answer.get("recovery_actions") or [])[:3]
        ]
        if tried:
            lines.append("Recoveries tried so far: " + ", ".join(tried) + ".")
    if team:
        lines.append(team)
    lines.append("Counts are observed outcomes; no model produced them.")
    return " ".join(lines)


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------


def _client() -> Client:
    headers = {"X-Reporter-ID": reporter_id()}
    if os.environ.get("FAILECHO_OPERATOR_TOKEN"):
        headers[OPERATOR_HEADER] = os.environ["FAILECHO_OPERATOR_TOKEN"]
    if os.environ.get("FAILECHO_TEAM"):
        # Private mode. Without this line the documented variable did nothing
        # here and a team's reports went to the public network (23 Sep).
        headers["X-FailEcho-Team"] = os.environ["FAILECHO_TEAM"]
    return Client(os.environ.get("FAILECHO_ENDPOINT") or DEFAULT_ENDPOINT, headers)


def _identity(event: dict) -> tuple[str, str] | None:
    split = split_tool_name(str(event.get("tool_name") or ""))
    if not split or is_failecho(split[0]):
        return None
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd")
    service = service_for(split[0], project_dir)
    return (service, split[1]) if service else None


def _latency(event: dict) -> int | None:
    value = event.get("duration_ms")
    return int(value) if isinstance(value, (int, float)) and value >= 0 else None


def on_failure(event: dict, client: Client) -> dict | None:
    if event.get("is_interrupt"):
        return None  # the user stopped it; nothing failed
    identity = _identity(event)
    if identity is None:
        return None
    service, operation = identity
    error = str(event.get("error") or "")
    error_type, error_code = classify(error)
    call = {
        "service": service,
        "operation": operation,
        "error_type": error_type,
        "error_code": error_code,
    }
    if os.environ.get("FAILECHO_HOOK_SEND_ERRORS") == "1" and error:
        call["error_message"] = error[:2000]

    pending = _pending_path(str(event.get("session_id") or ""), str(event["tool_name"]))
    previous = _load_pending(pending)
    input_digest = _digest(event.get("tool_input"))

    # Ask before reporting, so the answer is what everyone else saw.
    answer = client.post("/v1/query", call)
    report = client.post(
        "/v1/observe", {**call, "outcome": "failure", "latency_ms": _latency(event)}
    )
    if previous and previous.get("fingerprint"):
        # The agent tried again and it failed again.
        client.post("/v1/outcome", {
            "fingerprint": previous["fingerprint"],
            "action": _recovery_action(previous, input_digest),
            "successful": False,
        })
    fingerprint = (report or {}).get("fingerprint") or (answer or {}).get("fingerprint")
    if fingerprint:
        _save_pending(pending, {"fingerprint": fingerprint, "input": input_digest,
                                "at": time.time()})

    note = context_note(service, operation, answer)
    if not note:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PostToolUseFailure",
                                   "additionalContext": note}}


def on_success(event: dict, client: Client) -> None:
    identity = _identity(event)
    if identity is None:
        return
    service, operation = identity
    if os.environ.get("FAILECHO_HOOK_REPORT_SUCCESS") != "0":
        client.post("/v1/observe", {"service": service, "operation": operation,
                                    "outcome": "success", "latency_ms": _latency(event)})
    pending = _pending_path(str(event.get("session_id") or ""), str(event["tool_name"]))
    previous = _load_pending(pending)
    if previous and previous.get("fingerprint"):
        client.post("/v1/outcome", {
            "fingerprint": previous["fingerprint"],
            "action": _recovery_action(previous, _digest(event.get("tool_input"))),
            "successful": True,
        })
        _clear_pending(pending)


def handle(event: dict, client: Client | None = None) -> dict | None:
    """Dispatch one hook event. Returns the JSON to print, if any."""
    client = client or _client()
    name = event.get("hook_event_name")
    if name == "PostToolUseFailure":
        return on_failure(event, client)
    if name == "PostToolUse":
        on_success(event, client)
    return None


def main() -> int:
    if os.environ.get("FAILECHO_DISABLED", "").lower() in ("1", "true", "yes"):
        return 0
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            return 0
        output = handle(event)
        if output:
            print(json.dumps(output))
    except Exception:  # noqa: BLE001 - a reporting hook must never break a session
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
