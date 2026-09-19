"""FailEcho in front of another MCP server: advice inside the failing tool's error.

A model given FailEcho's own tools has to think of asking, and while it is
handling a failure it mostly does not (OpenCode, 0.1 calls a run). This puts
the answer where the model is already looking. It sits between an MCP client
and a stdio MCP server the client would otherwise start itself:

    failecho-mcp proxy -- npx -y @modelcontextprotocol/server-github

and passes every message through untouched, in both directions, byte for
byte, with one exception: the response to a ``tools/call`` that failed. That
one gets a single line of the network's advice appended -- a text item on a
result flagged ``isError``, a sentence on a JSON-RPC error's message:

    FailEcho: try backoff, worked 128/251 (confidence 0.61).

Every tool call's outcome is also reported, as its shape only: the server's
own name (``serverInfo.name``), the tool name, an error class, a code, the
latency. Never arguments, never results, never the error text -- that is read
here, locally, to pick the error class, and dropped.

What it must never do, each with a test:

* change a message it is not annotating (resources, prompts, notifications,
  sampling and every other request pass as the same bytes);
* hold up the stream: advice is fetched on its own thread, at most
  ``ADVICE_TIMEOUT_SECONDS``, and only the failed response waits for it;
* fail because FailEcho does: unreachable, the response goes out unannotated;
* outlive or mask the server: its exit code is the proxy's exit code.

Environment:

    FAILECHO_DISABLED=1   a plain pipe: no reports, no advice
    FAILECHO_ADVISE=0     report, but do not annotate errors
    FAILECHO_SERVICE      the service name, if the server reports none
    FAILECHO_ENDPOINT     default https://failecho.com
    FAILECHO_REPORTER_ID  stable id, so this machine counts as one reporter
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from typing import IO

from failecho_autoreport import FailEcho

#: The longest a failed response is held for advice. The autoreport budget.
ADVICE_TIMEOUT_SECONDS = 3.0


class _ToolError(Exception):
    """Carries a failed call's text to the classifier and no further."""


def _error_text(message: dict) -> str | None:
    """The failure text of a tools/call response, or None if it succeeded."""
    if "error" in message:
        err = message.get("error") or {}
        return f"{err.get('code', '')} {err.get('message', '')}".strip() or "error"
    result = message.get("result")
    if isinstance(result, dict) and result.get("isError"):
        parts = [c.get("text", "") for c in result.get("content") or []
                 if isinstance(c, dict) and c.get("type") == "text"]
        return " ".join(p for p in parts if p)[:2000] or "error"
    return None


def annotate(message: dict, line: str) -> dict:
    """The same response with the advice line added. Never removes anything."""
    out = dict(message)
    if "error" in out and isinstance(out["error"], dict):
        err = dict(out["error"])
        err["message"] = f"{err.get('message', '')}\n{line}".lstrip("\n")
        out["error"] = err
    elif isinstance(out.get("result"), dict):
        result = dict(out["result"])
        result["content"] = list(result.get("content") or []) + [{"type": "text", "text": line}]
        out["result"] = result
    return out


class Proxy:
    """Two pipes and a ledger of the tools/call requests in flight."""

    def __init__(self, argv: list[str], fe: FailEcho | None = None, *,
                 advise: bool | None = None, service: str | None = None,
                 client_in: IO[bytes] | None = None, client_out: IO[bytes] | None = None) -> None:
        self.argv = argv
        disabled = os.environ.get("FAILECHO_DISABLED", "").strip().lower() in ("1", "true", "yes", "on")
        self.fe = None if disabled else (fe or FailEcho())
        if advise is None:
            advise = os.environ.get("FAILECHO_ADVISE", "1").strip().lower() not in ("0", "false", "no", "off")
        self.advise = bool(advise) and self.fe is not None
        self.service = service or os.environ.get("FAILECHO_SERVICE") or None
        self.client_in = client_in or sys.stdin.buffer
        self.client_out = client_out or sys.stdout.buffer
        self._out_lock = threading.Lock()
        self._lock = threading.Lock()
        #: request id (as JSON) -> (tool name, started); initialize ids -> None
        self._pending: dict[str, tuple[str, float] | None] = {}
        self._advising: list[threading.Thread] = []
        self.proc: subprocess.Popen | None = None
        self.annotated = 0

    # -- the two directions ----------------------------------------------------

    def _write_client(self, data: bytes) -> None:
        with self._out_lock:
            self.client_out.write(data)
            self.client_out.flush()

    def _from_client(self) -> None:
        """Client to server: forwarded as read, noting tools/call and initialize."""
        assert self.proc and self.proc.stdin
        try:
            for raw in iter(self.client_in.readline, b""):
                if self.fe is not None:
                    self._note_request(raw)
                self.proc.stdin.write(raw)
                self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                self.proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass

    def _note_request(self, raw: bytes) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        for m in msg if isinstance(msg, list) else [msg]:
            if not isinstance(m, dict) or "id" not in m or "method" not in m:
                continue
            key = json.dumps(m["id"])
            if m["method"] == "tools/call":
                name = (m.get("params") or {}).get("name")
                if isinstance(name, str):
                    with self._lock:
                        self._pending[key] = (name, time.monotonic())
            elif m["method"] == "initialize":
                with self._lock:
                    self._pending[key] = None

    def _from_server(self) -> None:
        """Server to client: forwarded as read, unless it answers a failed call."""
        assert self.proc and self.proc.stdout
        for raw in iter(self.proc.stdout.readline, b""):
            held = self._inspect(raw) if self.fe is not None else None
            if held is None:
                try:
                    self._write_client(raw)
                except (BrokenPipeError, OSError, ValueError):
                    break

    def _inspect(self, raw: bytes) -> bool | None:
        """Report a tools/call outcome. True when the line was handed to an
        advice thread, which writes it; None to forward it now."""
        with self._lock:
            if not self._pending:
                return None
        try:
            msg = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(msg, dict) or "method" in msg or "id" not in msg:
            return None   # a batch, a notification or a server request: untouched
        key = json.dumps(msg["id"])
        with self._lock:
            if key not in self._pending:
                return None
            entry = self._pending.pop(key)
        if entry is None:   # the initialize response: learn the server's name
            info = (msg.get("result") or {}).get("serverInfo") or {}
            if not self.service and isinstance(info.get("name"), str):
                self.service = info["name"]
            return None
        tool, started = entry
        service = self.service or (os.path.basename(self.argv[0]) if self.argv else "mcp")
        latency = int((time.monotonic() - started) * 1000)
        text = _error_text(msg)
        if text is None:
            self.fe.record_success(service, tool, latency)
            return None
        exc = _ToolError(text)
        self.fe.record_failure(service, tool, exc, latency)
        if not self.advise:
            return None
        t = threading.Thread(target=self._advise_and_write, args=(raw, msg, service, tool, exc), daemon=True)
        self._advising.append(t)
        t.start()
        return True

    def _advise_and_write(self, raw: bytes, msg: dict, service: str, tool: str, exc: Exception) -> None:
        out = raw
        try:
            from failecho_autoreport import classify
            et, code = classify(exc)
            answer = self.fe.check(service, tool, et, code, timeout=ADVICE_TIMEOUT_SECONDS)
            line = FailEcho.advice_text(answer) if answer else None
            if line:
                out = (json.dumps(annotate(msg, line), separators=(",", ":")) + "\n").encode()
                self.annotated += 1
        except Exception:  # noqa: BLE001 - advice is never worth a lost response
            out = raw
        try:
            self._write_client(out)
        except (BrokenPipeError, OSError, ValueError):
            pass

    # -- lifetime ----------------------------------------------------------------

    def run(self) -> int:
        try:
            self.proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        except OSError as exc:
            print(f"failecho-mcp proxy: cannot start {self.argv[0]!r}: {exc}", file=sys.stderr)
            return 127
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, lambda s, _f: self.proc and self.proc.send_signal(s))
            except ValueError:
                pass   # not the main thread (tests)
        reader = threading.Thread(target=self._from_client, daemon=True)
        reader.start()
        self._from_server()          # returns when the server closes stdout
        code = self.proc.wait()
        for t in list(self._advising):
            t.join(ADVICE_TIMEOUT_SECONDS + 1)
        if self.fe is not None:
            self.fe.flush(timeout=2.0)
        return code


def main(argv: list[str]) -> int:
    """``failecho-mcp proxy -- <server command...>``"""
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        print("usage: failecho-mcp proxy -- <mcp server command> [args...]", file=sys.stderr)
        return 2
    code = Proxy(argv).run()
    # The client-side reader is still blocked on stdin when the server has
    # gone; a normal interpreter exit then aborts on that stream's lock
    # (SIGABRT instead of the server's exit code). Flush and leave directly.
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(code)
