"""Automatic failure reporting for agents that are not Claude Code.

The Claude Code plugin reports every MCP tool call because a hook fires after
it, with no model in the loop. Everywhere else -- LangChain, LlamaIndex, a
cron job, a local 7B driving a scraper -- reporting depends on the model
choosing to call a tool, and a small model reliably will not. That is the gap
this closes: wrap the call site once and the decision disappears.

    from failecho_autoreport import FailEcho

    fe = FailEcho()

    @fe.watch(service="api.github.com", operation="create_issue")
    def create_issue(...):
        ...

Or, for a list of framework tool objects:

    tools = fe.wrap(tools, service="github-mcp")

What it sends is a failure's *shape*: service, operation, a short error class,
an HTTP-ish code when there is one, and how long the call took. Never
arguments, never return values, never the prompt, never a credential. The
error text itself is off unless FAILECHO_SEND_ERRORS=1.

Three properties this must have, because it runs inside somebody else's
program:

* **It never raises.** Every network path is wrapped; a reporting bug must not
  become the host application's exception.
* **It never blocks.** Reports go on a bounded queue drained by a daemon
  thread. A slow or dead FailEcho costs the caller nothing at all, not even
  the two seconds the Claude Code hook is willing to spend.
* **It never changes behaviour.** The wrapper re-raises exactly what it
  caught, and returns exactly what the callable returned.

Environment:

    FAILECHO_ENDPOINT        default https://failecho.com
    FAILECHO_DISABLED=1      do nothing at all
    FAILECHO_REPORTER_ID     stable id, so this process counts as one reporter
    FAILECHO_SEND_ERRORS=1   also send the error text (normalized server-side)
    FAILECHO_REPORT_SUCCESS=0  do not report successful calls
    FAILECHO_OPERATOR_TOKEN  FailEcho's own agents only. Marks reports as
                             first-party so they are never counted as adoption.
"""

from __future__ import annotations

import atexit
import functools
import inspect
import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.request
import uuid

__all__ = ["FailEcho", "classify"]


class _NoFingerprint(Exception):
    """An outcome arrived with no failure to attach it to."""

__version__ = "0.1.1"

DEFAULT_ENDPOINT = "https://failecho.com"

#: Reports are dropped rather than queued past this. An agent in a retry storm
#: should not grow a backlog it will never send; the observations that matter
#: are the first few, and the network's whole point is that it is optional.
MAX_QUEUE = 256

#: Not the caller's timeout -- the worker's. The caller never waits at all.
TIMEOUT_SECONDS = 5.0

#: First match wins, and the order matters: "503 ... invalid upstream" is a
#: server error rather than a validation one. Kept identical to the Claude Code
#: hook's table on purpose -- two agents reporting the same failure through
#: different paths have to produce the same error_type or the evidence does not
#: join up.
ERROR_CLASSES = (
    ("timeout", re.compile(r"time[d ]?\s?out|deadline exceeded|ETIMEDOUT", re.I)),
    ("rate_limit", re.compile(r"\b429\b|rate.?limit|too many requests|quota", re.I)),
    ("auth_error", re.compile(
        r"\b40[13]\b|unauthori[sz]ed|forbidden|permission denied|authenticat|invalid (api )?key",
        re.I)),
    ("not_found", re.compile(r"\b404\b|not found|no such", re.I)),
    ("validation_error", re.compile(
        r"\b4(00|22)\b|invalid|validation|required|must be|schema", re.I)),
    ("connection_error", re.compile(
        r"ECONN(REFUSED|RESET)|ENOTFOUND|EPIPE|connection (refused|reset|closed|error)"
        r"|network|socket", re.I)),
    ("server_error", re.compile(
        r"\b5\d\d\b|internal (server )?error|service unavailable|bad gateway|upstream", re.I)),
)
HTTP_CLASSES = {"rate_limit", "auth_error", "not_found", "validation_error", "server_error"}

_STATUS = re.compile(r"\b([45]\d\d)\b")


def classify(exc: BaseException) -> tuple[str, str | None]:
    """A failure's class and code, from its text alone.

    Returns something like ("rate_limit", "429"). The text is read and thrown
    away; only the class and the code leave this function, which is what makes
    it safe to run over exceptions whose messages may contain anything.
    """
    text = f"{type(exc).__name__}: {exc}"
    for name, pattern in ERROR_CLASSES:
        if pattern.search(text):
            code = None
            if name in HTTP_CLASSES:
                match = _STATUS.search(text)
                if match:
                    code = match.group(1)
            return name, code
    return "error", None


def _truthy(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


class FailEcho:
    """Reports the shape of failures, from inside somebody else's program."""

    def __init__(
        self,
        endpoint: str | None = None,
        reporter_id: str | None = None,
        *,
        enabled: bool | None = None,
        report_success: bool | None = None,
        send_errors: bool | None = None,
        timeout: float = TIMEOUT_SECONDS,
        operator_token: str | None = None,
    ) -> None:
        self.endpoint = (endpoint or os.environ.get("FAILECHO_ENDPOINT")
                         or DEFAULT_ENDPOINT).rstrip("/")
        self.reporter_id = (reporter_id or os.environ.get("FAILECHO_REPORTER_ID")
                            or f"anon-{uuid.uuid4().hex[:12]}")
        disabled = _truthy(os.environ.get("FAILECHO_DISABLED"), False)
        self.enabled = (not disabled) if enabled is None else enabled
        self.report_success = (
            _truthy(os.environ.get("FAILECHO_REPORT_SUCCESS"), True)
            if report_success is None else report_success
        )
        self.send_errors = (
            _truthy(os.environ.get("FAILECHO_SEND_ERRORS"), False)
            if send_errors is None else send_errors
        )
        self.timeout = timeout
        # Only FailEcho's own agents set this. With it, the server stores the
        # report as first_party and keeps it out of the adoption counters. An
        # agent we run that forgets it is counted as a stranger adopting us,
        # which is the one number on the front page that has to stay honest.
        self.operator_token = operator_token or os.environ.get("FAILECHO_OPERATOR_TOKEN") or None

        self.queued = 0
        self.dropped = 0
        self.sent = 0
        self.failed = 0
        self.unmatched = 0
        #: wrap() is best-effort by design, so it counts what it managed.
        #: wrapped == 0 after wrapping a tool list means nothing is reporting.
        self.wrapped = 0
        self.unwrapped = 0

        #: The fingerprint the server gave each (service, operation)'s last
        #: failure, so an outcome can be attached to it.
        self._fingerprints: dict[tuple[str, str], str] = {}

        self._queue: queue.Queue = queue.Queue(maxsize=MAX_QUEUE)
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()

    # -- the call sites ----------------------------------------------------

    def watch(self, service: str, operation: str | None = None,
              mutates: bool | None = None):
        """Decorate a callable so its failures and successes are reported.

        `operation` defaults to the function's own name. `mutates` declares
        whether the call changes state -- say True for anything that is not a
        read. One line here beats any heuristic the network could apply to the
        name, and for GraphQL it is the only way it can know. Works on both
        sync and async callables; the wrapper returns what the callable
        returned and re-raises what it raised, unchanged.
        """
        def decorate(func):
            name = operation or getattr(func, "__name__", "call")

            if inspect.iscoroutinefunction(func):
                @functools.wraps(func)
                async def async_wrapper(*args, **kwargs):
                    started = time.monotonic()
                    try:
                        result = await func(*args, **kwargs)
                    except BaseException as exc:
                        self.record_failure(service, name, exc, _elapsed(started), mutates)
                        raise
                    self.record_success(service, name, _elapsed(started), mutates)
                    return result
                return async_wrapper

            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                started = time.monotonic()
                try:
                    result = func(*args, **kwargs)
                except BaseException as exc:
                    self.record_failure(service, name, exc, _elapsed(started), mutates)
                    raise
                self.record_success(service, name, _elapsed(started), mutates)
                return result
            return wrapper
        return decorate

    def wrap(self, tools, service: str, mutates: bool | None = None):
        """Wrap a list of framework tool objects in place.

        `mutates` applies to every tool in the list; pass a dict keyed by tool
        name to declare them individually, e.g. {"create_issue": True,
        "get_issue": False}. Undeclared tools are left for the network's name
        heuristic, which is a guess.

        LangChain and LlamaIndex tools both expose a name and one or two
        invoke methods. Anything with a recognisable pair is wrapped; anything
        else is returned untouched rather than guessed at, because breaking a
        caller's tool to report on it would be a poor trade.
        """
        for tool in tools or []:
            # LangChain puts the name on the tool; LlamaIndex puts it on
            # tool.metadata. A tool whose name cannot be found is skipped, and
            # skipping used to be silent -- which is the worst outcome here,
            # because the caller believes reporting is on. The counters below
            # are how you check.
            name = (getattr(tool, "name", None)
                    or getattr(getattr(tool, "metadata", None), "name", None)
                    or getattr(tool, "__name__", None))
            if not name:
                self.unwrapped += 1
                continue
            # One layer per calling convention, never two. A LangChain
            # StructuredTool has both `_run` and `func`, and `_run` calls
            # `func` -- wrapping both reports the same failure twice, which is
            # worse than not reporting it, because the network cannot tell a
            # double count from two agents agreeing.
            # Ordered outermost-first per convention, and the first one that
            # actually takes an assignment wins. LlamaIndex exposes `fn` as a
            # read-only property over a settable `_fn`, so the public name is
            # tried and skipped rather than being absent.
            for candidates in (("_run", "func", "fn", "_fn"),
                               ("_arun", "coroutine", "async_fn", "_async_fn")):
                for attr in candidates:
                    target = getattr(tool, attr, None)
                    if target is None or not callable(target):
                        continue
                    declared = mutates.get(name) if isinstance(mutates, dict) else mutates
                    try:
                        setattr(tool, attr, self.watch(service, name, declared)(target))
                    except Exception:
                        # Frozen models and slotted classes land here. The
                        # tool keeps working; it just goes unreported.
                        continue
                    self.wrapped += 1
                    break  # this convention is covered; do not wrap deeper
        return tools

    # -- recording ---------------------------------------------------------

    def record_failure(self, service: str, operation: str,
                       exc: BaseException, latency_ms: int | None = None,
                       mutates: bool | None = None) -> None:
        error_type, error_code = classify(exc)
        body = {
            "service": service,
            "operation": operation,
            "outcome": "failure",
            "error_type": error_type,
        }
        if error_code:
            body["error_code"] = error_code
        if latency_ms is not None:
            body["latency_ms"] = latency_ms
        if mutates is not None:
            body["mutates"] = bool(mutates)
        if self.send_errors:
            body["error_message"] = f"{type(exc).__name__}: {exc}"[:2000]
        self._submit(body)

    def record_success(self, service: str, operation: str,
                       latency_ms: int | None = None,
                       mutates: bool | None = None) -> None:
        if not self.report_success:
            return
        body = {"service": service, "operation": operation, "outcome": "success"}
        if latency_ms is not None:
            body["latency_ms"] = latency_ms
        if mutates is not None:
            body["mutates"] = bool(mutates)
        self._submit(body)

    # -- the wire ----------------------------------------------------------

    def recovered(self, service: str, operation: str, action: str,
                  successful: bool = True) -> None:
        """Report what you tried after a failure, and whether it worked.

        This is the half of the network another agent can actually use: a
        failure rate says a call is broken, and only an outcome says what to
        do about it. The wrapper cannot infer the action -- it did not make
        the fix -- so this stays an explicit call.

        The fingerprint is resolved when the report is sent rather than now,
        because the failure it belongs to may still be on the queue. One
        worker draining in order is what makes that safe.
        """
        self._submit({"service": service, "operation": operation,
                      "action": action, "successful": bool(successful)},
                     kind="outcome")

    def _submit(self, body: dict, kind: str = "observe") -> None:
        if not self.enabled:
            return
        # Reporting FailEcho's own trouble to FailEcho is a loop, and it is
        # also not shared infrastructure anybody else is calling.
        if body.get("service", "").endswith("failecho.com"):
            return
        try:
            self._queue.put_nowait((kind, body))
            self.queued += 1
        except queue.Full:
            self.dropped += 1
            return
        self._ensure_worker()

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._drain, name="failecho-report", daemon=True)
            self._worker.start()

    def _drain(self) -> None:
        while True:
            try:
                kind, body = self._queue.get(timeout=5.0)
            except queue.Empty:
                return
            try:
                if kind == "outcome":
                    self._post_outcome(body)
                else:
                    self._post(body)
                self.sent += 1
            except _NoFingerprint:
                self.unmatched += 1
            except Exception:
                self.failed += 1
            finally:
                self._queue.task_done()

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "X-Reporter-ID": self.reporter_id,
            "User-Agent": f"failecho-autoreport/{__version__}",
        }
        if self.operator_token:
            # Bearer rather than X-FailEcho-Operator: the server accepts both,
            # and Bearer survives hosts that filter unknown header names.
            headers["Authorization"] = f"Bearer {self.operator_token}"
        return headers

    def _post(self, body: dict) -> None:
        request = urllib.request.Request(
            f"{self.endpoint}/v1/observe",
            data=json.dumps(body).encode(),
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = response.read()
        if body.get("outcome") != "failure":
            return
        try:
            fingerprint = json.loads(payload).get("fingerprint")
        except Exception:
            return
        if fingerprint:
            self._fingerprints[(body["service"], body["operation"])] = fingerprint

    def _post_outcome(self, body: dict) -> None:
        key = (body["service"], body["operation"])
        fingerprint = self._fingerprints.get(key)
        if not fingerprint:
            # Nothing to attach it to: the failure was dropped, disabled, or
            # never reported. Silently inventing a fingerprint would put the
            # outcome on the wrong failure, which is worse than losing it.
            raise _NoFingerprint(key)
        request = urllib.request.Request(
            f"{self.endpoint}/v1/outcome",
            data=json.dumps({"fingerprint": fingerprint,
                             "action": body["action"],
                             "successful": body["successful"]}).encode(),
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            response.read()

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait for queued reports to go out. Returns whether the queue drained.

        Only worth calling in a short-lived process -- a script or a job --
        where the interpreter would otherwise exit before the daemon thread
        has sent anything.
        """
        # Not `empty()`: the worker takes an item off the queue *before* it
        # sends it, so an empty queue can still have a request in flight, and
        # a script that exits on that answer loses its last report.
        # unfinished_tasks only drops once task_done() runs, after the post.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.02)
        return self._queue.unfinished_tasks == 0


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


_default: FailEcho | None = None


def default() -> FailEcho:
    """A process-wide client, for callers that do not want to hold one."""
    global _default
    if _default is None:
        _default = FailEcho()
        atexit.register(lambda: _default and _default.flush(timeout=2.0))
    return _default
