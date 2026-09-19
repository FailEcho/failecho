"""FailEcho in front of another MCP server: advice inside the failing tool's error.

A model given FailEcho's own tools has to think of asking, and while it is
handling a failure it mostly does not (OpenCode, 0.1 calls a run). This puts
the answer where the model is already looking. It sits between an MCP client
and a stdio MCP server the client would otherwise start itself:

    failecho-mcp proxy -- npx -y @modelcontextprotocol/server-github

or, for a remote server, speaks Streamable HTTP to it on the client's behalf
(the client still starts a local stdio process -- this one):

    failecho-mcp proxy --header "Authorization: Bearer $TOKEN" -- https://mcp.example.com/mcp

and passes every message through untouched, in both directions, byte for
byte, with one exception: the response to a ``tools/call`` that failed. That
one gets a single line of the network's advice appended -- a text item on a
result flagged ``isError``, a sentence on a JSON-RPC error's message:

    FailEcho: try backoff, worked 128/251 (confidence 0.61).

Every tool call's outcome is also reported, as its shape only: the server's
own name (``serverInfo.name``), the tool name, an error class, a code, the
latency. Never arguments, never results, never the error text -- that is read
here, locally, to pick the error class, and dropped.

Recovery is inferred the way ``failecho_autoreport run`` infers it: a call
that failed transiently (rate limit, server error, timeout, connection) and
is made again with the same arguments inside two minutes was a retry, and
whether the second call worked is reported as that retry's outcome. The
arguments are compared as a hash held in memory here; they never leave.
Without outcomes the network can say a tool is failing but never what
fixes it, and advice is exactly that second half.

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

import hashlib
import json
import os
import signal
import subprocess
import sys
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import IO

from failecho_autoreport import FailEcho

#: The longest a failed response is held for advice. The autoreport budget.
ADVICE_TIMEOUT_SECONDS = 3.0
#: A repeat of a transiently failed call inside this window is a retry.
INFER_WINDOW = 120.0
TRANSIENT = frozenset({"rate_limit", "server_error", "timeout", "connection_error"})


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


class HttpUpstream:
    """A remote Streamable HTTP MCP server, shaped like a subprocess.

    ``stdin.write`` takes the client's lines; each JSON-RPC message is POSTed
    on its own thread, so a slow call holds up nothing else. What comes back
    -- a JSON body, or an SSE stream of messages -- is queued, one message
    per line, for ``stdout.readline``. The session id the server issues on
    initialize is sent on every later request, and the session is deleted
    on close. An HTTP failure on a request becomes a JSON-RPC error on that
    request's id, so the client is never left waiting for an answer that
    will not come.

    Not handled: a server-initiated stream opened with GET (messages the
    server sends outside any request), and OAuth. A server that needs OAuth
    wants the client's own login flow; use a header token or connect to it
    directly.
    """

    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.headers = dict(headers or {})
        self.session_id: str | None = None
        self.protocol: str | None = None
        self._out: queue.Queue = queue.Queue()
        self._buf = b""
        self._inflight = 0
        self._cv = threading.Condition()
        self._closed = False
        self.stdin = self
        self.stdout = self
        self.returncode: int | None = None

    # -- the subprocess face --------------------------------------------------

    def write(self, data: bytes) -> int:
        self._buf += data
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            if line.strip():
                with self._cv:
                    self._inflight += 1
                threading.Thread(target=self._post, args=(line,), daemon=True).start()
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        """Client closed stdin: let the calls in flight finish, then end."""
        def finish():
            with self._cv:
                self._cv.wait_for(lambda: self._inflight == 0, timeout=600)
            self._delete_session()
            self._closed = True
            self._out.put(b"")
        threading.Thread(target=finish, daemon=True).start()

    def readline(self) -> bytes:
        return self._out.get()

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        return 0

    def send_signal(self, _sig) -> None:
        self.close()

    # -- HTTP -------------------------------------------------------------------

    def _request_headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self.headers}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.protocol:
            h["MCP-Protocol-Version"] = self.protocol
        return h

    def _emit(self, text: str) -> None:
        text = text.strip()
        if text:
            self._out.put(text.encode() + b"\n")

    def _post(self, line: bytes) -> None:
        try:
            try:
                msg = json.loads(line)
            except ValueError:
                msg = None
            rid = msg.get("id") if isinstance(msg, dict) and "method" in msg else None
            is_init = isinstance(msg, dict) and msg.get("method") == "initialize"
            req = urllib.request.Request(self.url, data=line, method="POST", headers=self._request_headers())
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    if is_init and r.headers.get("Mcp-Session-Id"):
                        self.session_id = r.headers["Mcp-Session-Id"]
                    ctype = (r.headers.get("Content-Type") or "").lower()
                    if "text/event-stream" in ctype:
                        data: list[str] = []
                        for raw in r:
                            s = raw.decode("utf-8", "replace").rstrip("\r\n")
                            if s.startswith("data:"):
                                data.append(s[5:].lstrip())
                            elif s == "" and data:
                                self._emit_message("\n".join(data), is_init)
                                data = []
                        if data:
                            self._emit_message("\n".join(data), is_init)
                    else:
                        body = r.read()
                        if body.strip():
                            self._emit_message(body.decode("utf-8", "replace"), is_init)
            except urllib.error.HTTPError as e:
                detail = e.read()[:200].decode("utf-8", "replace")
                if rid is not None:
                    self._emit(json.dumps({"jsonrpc": "2.0", "id": rid, "error": {
                        "code": -32603, "message": f"HTTP {e.code} from {self.url}: {detail}".strip()}}))
            except Exception as e:  # noqa: BLE001 - unreachable, reset, timed out
                if rid is not None:
                    self._emit(json.dumps({"jsonrpc": "2.0", "id": rid, "error": {
                        "code": -32603, "message": f"cannot reach {self.url}: {type(e).__name__}: {e}"}}))
        finally:
            with self._cv:
                self._inflight -= 1
                self._cv.notify_all()

    def _emit_message(self, text: str, is_init: bool) -> None:
        """One JSON body or SSE event, as one line; a batch as several."""
        try:
            msg = json.loads(text)
        except ValueError:
            return
        for m in msg if isinstance(msg, list) else [msg]:
            if is_init and isinstance(m, dict) and isinstance(m.get("result"), dict):
                self.protocol = m["result"].get("protocolVersion") or self.protocol
            self._emit(json.dumps(m, separators=(",", ":"), ensure_ascii=False))

    def _delete_session(self) -> None:
        if not self.session_id:
            return
        try:
            req = urllib.request.Request(self.url, method="DELETE", headers=self._request_headers())
            urllib.request.urlopen(req, timeout=5).close()
        except Exception:  # noqa: BLE001 - the server expires it anyway
            pass


class Proxy:
    """Two pipes and a ledger of the tools/call requests in flight."""

    def __init__(self, argv: list[str], fe: FailEcho | None = None, *,
                 advise: bool | None = None, service: str | None = None,
                 client_in: IO[bytes] | None = None, client_out: IO[bytes] | None = None,
                 headers: dict[str, str] | None = None) -> None:
        self.argv = argv
        self.headers = headers or {}
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
        #: request id (as JSON) -> (tool name, started, arguments hash);
        #: initialize ids -> None
        self._pending: dict[str, tuple[str, float, str] | None] = {}
        #: (tool, arguments hash) -> (failed at, error_type, error_code) for
        #: transient failures a repeat of the same call may recover from
        self._retryable: dict[tuple[str, str], tuple[float, str, str | None]] = {}
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
                params = m.get("params") or {}
                name = params.get("name")
                if isinstance(name, str):
                    args = json.dumps(params.get("arguments") or {}, sort_keys=True, default=str)
                    digest = hashlib.sha256(args.encode()).hexdigest()
                    with self._lock:
                        self._pending[key] = (name, time.monotonic(), digest)
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
        tool, started, digest = entry
        service = self.service or _fallback_name(self.argv)
        latency = int((time.monotonic() - started) * 1000)
        text = _error_text(msg)
        if text is None:
            self.fe.record_success(service, tool, latency)
            self._infer(service, tool, digest, None)
            return None
        exc = _ToolError(text)
        self.fe.record_failure(service, tool, exc, latency)
        self._infer(service, tool, digest, exc)
        if not self.advise:
            return None
        t = threading.Thread(target=self._advise_and_write, args=(raw, msg, service, tool, exc), daemon=True)
        self._advising.append(t)
        t.start()
        return True

    def _infer(self, service: str, tool: str, digest: str, failure: Exception | None) -> None:
        """File the outcome of a repeated call, then remember this one if a
        later repeat could recover from it."""
        from failecho_autoreport import classify
        now = time.monotonic()
        key = (tool, digest)
        with self._lock:
            previous = self._retryable.pop(key, None)
        if previous is not None and now - previous[0] <= INFER_WINDOW:
            self.fe.recovered(service, tool, "retry", failure is None,
                              error_type=previous[1], error_code=previous[2])
        if failure is not None:
            et, code = classify(failure)
            if et in TRANSIENT:
                with self._lock:
                    if len(self._retryable) >= 256:
                        self._retryable.pop(min(self._retryable, key=lambda k: self._retryable[k][0]))
                    self._retryable[key] = (now, et, code)

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
        if _is_url(self.argv[0]):
            self.proc = HttpUpstream(self.argv[0], self.headers)   # type: ignore[assignment]
        else:
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


def _is_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def _fallback_name(argv: list[str]) -> str:
    """The service name when the server gives none: the URL's host, or the
    command's name."""
    if not argv:
        return "mcp"
    if _is_url(argv[0]):
        from urllib.parse import urlsplit
        return (urlsplit(argv[0]).hostname or "mcp").lower()
    return os.path.basename(argv[0])


USAGE = ("usage: failecho-mcp proxy [--header 'Name: value' ...] -- <server command> [args...]\n"
         "       failecho-mcp proxy [--header 'Name: value' ...] -- https://host/mcp")


def main(argv: list[str]) -> int:
    """``failecho-mcp proxy [--header 'Name: value'] -- <command...> | <url>``"""
    headers: dict[str, str] = {}
    while argv and argv[0] != "--":
        if argv[0] == "--header" and len(argv) > 1 and ":" in argv[1]:
            name, value = argv[1].split(":", 1)
            headers[name.strip()] = value.strip()
            argv = argv[2:]
        else:
            break
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2
    code = Proxy(argv, headers=headers).run()
    # The client-side reader is still blocked on stdin when the server has
    # gone; a normal interpreter exit then aborts on that stream's lock
    # (SIGABRT instead of the server's exit code). Flush and leave directly.
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(code)


if __name__ == "__main__":   # run as a file, without the MCP SDK installed
    main(sys.argv[1:])
