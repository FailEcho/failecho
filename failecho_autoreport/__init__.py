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
    FAILECHO_INFER_RECOVERY=0  `run` mode only: do not infer retry/backoff
                             outcomes from a failure followed by a success.
    FAILECHO_ADVISE=1        on a failure, also ask the network what worked
                             (see "Advice" below). Off by default.

Advice. Reporting alone helps the next agent; it does nothing for this one.
With advise on, a failure in a watched call is followed by one read of the
network (`/v1/query`, which stores nothing) before the exception goes on up.
The answer is attached, never acted on:

    try:
        create_issue(...)
    except Exception as exc:
        exc.failecho            # the network's answer, a dict, or None
        fe.advice_text(exc)     # one line for a log or a model's tool error

On Python 3.11+ the line is also added as an exception note, so it shows in
tracebacks. The exception itself, its type and message, are unchanged. The
read costs up to ADVICE_TIMEOUT_SECONDS, on failures only; that is the one
place this module waits, which is why it is opt-in.
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

__version__ = "0.1.5"

DEFAULT_ENDPOINT = "https://failecho.com"

#: Reports are dropped rather than queued past this. An agent in a retry storm
#: should not grow a backlog it will never send; the observations that matter
#: are the first few, and the network's whole point is that it is optional.
MAX_QUEUE = 256

#: Not the caller's timeout -- the worker's. The caller never waits at all.
TIMEOUT_SECONDS = 5.0
#: How long a failing call may wait for advice. Short: a slow network answer
#: must cost less than the retry it is meant to inform. 3 s, not 1.5: in a
#: clean-install test (19 Sep) the first failure of a fresh process lost its
#: advice at 1.5 s, while later reads took about 0.2 s -- a cold first
#: connection, most likely. FAILECHO_ADVICE_TIMEOUT overrides.
try:
    ADVICE_TIMEOUT_SECONDS = float(os.environ.get("FAILECHO_ADVICE_TIMEOUT") or 3.0)
except ValueError:
    ADVICE_TIMEOUT_SECONDS = 3.0

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
        advise: bool | None = None,
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
        self.advise = _truthy(os.environ.get("FAILECHO_ADVISE"), False) if advise is None else advise
        #: The last answer per (service, operation), for callers that catch
        #: the exception somewhere the attribute is out of reach.
        self.last_advice: dict[tuple[str, str], dict] = {}
        self.advised = 0
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
        #: Outcomes the auto module inferred from the sequence of calls
        #: (a transient failure, then the same URL succeeding), as opposed
        #: to ones the program reported through recovered().
        self.inferred = 0
        self.sent_outcomes = 0
        #: Failure observations queued, so a summary can say how many of the
        #: calls it reported were failures without re-reading the queue.
        self.queued_failures = 0
        #: wrap() is best-effort by design, so it counts what it managed.
        #: wrapped == 0 after wrapping a tool list means nothing is reporting.
        self.wrapped = 0
        self.unwrapped = 0

        #: The fingerprint the server gave the last failure of each
        #: (service, operation), and of each exact shape (with error_type and
        #: code), so an outcome can be attached to the failure it belongs to.
        self._fingerprints: dict[tuple, str] = {}

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
                        if self.advise and isinstance(exc, Exception):
                            import asyncio
                            await asyncio.get_running_loop().run_in_executor(
                                None, self._attach_advice, service, name, exc)
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
                    if self.advise and isinstance(exc, Exception):
                        self._attach_advice(service, name, exc)
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

    # -- advice ------------------------------------------------------------

    def check(self, service: str, operation: str, error_type: str | None = None,
              error_code: str | None = None, timeout: float | None = None) -> dict | None:
        """What the network knows about this failure: the `/v1/query`
        answer, or None if it could not be had. A read; stores nothing.
        Never raises."""
        if not self.enabled:
            return None
        body = {"service": service, "operation": operation}
        if error_type:
            body["error_type"] = error_type
        if error_code:
            body["error_code"] = str(error_code)
        request = urllib.request.Request(
            f"{self.endpoint}/v1/query", data=json.dumps(body).encode(),
            headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout or ADVICE_TIMEOUT_SECONDS) as response:
                answer = json.loads(response.read())
        except Exception:
            return None
        return answer if isinstance(answer, dict) else None

    def _attach_advice(self, service: str, operation: str, exc: BaseException) -> None:
        try:
            error_type, error_code = classify(exc)
            answer = self.check(service, operation, error_type, error_code)
            if answer is None:
                return
            self.advised += 1
            self.last_advice[(service, operation)] = answer
            try:
                exc.failecho = answer
            except Exception:
                pass
            line = self.advice_text(answer)
            if line and hasattr(exc, "add_note"):
                exc.add_note(line)
        except Exception:
            pass

    @staticmethod
    def advice_text(answer) -> str | None:
        """One line from an answer (or an exception carrying one), for a log
        or a model's tool error. None when the network has nothing to say."""
        if isinstance(answer, BaseException):
            answer = getattr(answer, "failecho", None)
        if not isinstance(answer, dict):
            return None
        rec = answer.get("recommendation") or {}
        action = rec.get("action")
        tried = {a.get("action"): a for a in answer.get("recovery_actions") or [] if isinstance(a, dict)}
        # The same error class on this service's other operations: the
        # server pools it when the operation itself has nothing (19 Sep: a
        # tool name the network had never seen, under api.github.com, with
        # 2330 pooled backoff attempts, got no line at all).
        pooled = {a.get("action"): a for a in (answer.get("service_evidence") or {}).get("recovery_actions") or []
                  if isinstance(a, dict)}
        if action == "skip":
            return "FailEcho: skip -- nothing other agents tried recently has fixed this failure."
        if action:
            a = tried.get(action) or pooled.get(action) or {}
            evidence = (f", worked {a['successes']}/{a['attempts']}"
                        if "successes" in a and "attempts" in a else "")
            conf = rec.get("confidence")
            conf = f" (confidence {conf:.2f})" if isinstance(conf, (int, float)) else ""
            return f"FailEcho: try {action}{evidence}{conf}."
        # No recommendation, but evidence: say what others tried and how it
        # went, and that it is not a recommendation. Withholding it left a
        # model with nothing where the network knew backoff worked 128/251.
        for lead, source in (("other agents tried", tried), ("on this service's other operations, agents tried", pooled)):
            seen = [a for a in source.values() if "successes" in a and "attempts" in a and a["attempts"]]
            if seen:
                seen.sort(key=lambda a: -a["successes"] / a["attempts"])
                parts = ", ".join(f"{a['action']} worked {a['successes']}/{a['attempts']}" for a in seen[:3])
                return f"FailEcho: no clear fix yet; {lead} {parts}."
        return None
        rec = answer.get("recommendation") or {}
        action = rec.get("action")
        tried = {a.get("action"): a for a in answer.get("recovery_actions") or [] if isinstance(a, dict)}
        if action == "skip":
            return "FailEcho: skip -- nothing other agents tried recently has fixed this failure."
        if action:
            a = tried.get(action) or {}
            evidence = (f", worked {a['successes']}/{a['attempts']}"
                        if "successes" in a and "attempts" in a else "")
            conf = rec.get("confidence")
            conf = f" (confidence {conf:.2f})" if isinstance(conf, (int, float)) else ""
            return f"FailEcho: try {action}{evidence}{conf}."
        # No recommendation, but evidence: say what others tried and how it
        # went, and that it is not a recommendation. Withholding it left a
        # model with nothing where the network knew backoff worked 128/251.
        seen = [a for a in tried.values() if "successes" in a and "attempts" in a and a["attempts"]]
        if seen:
            seen.sort(key=lambda a: -a["successes"] / a["attempts"])
            parts = ", ".join(f"{a['action']} worked {a['successes']}/{a['attempts']}" for a in seen[:3])
            return f"FailEcho: no clear fix yet; other agents tried {parts}."
        return None

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
                  successful: bool = True, *, error_type: str | None = None,
                  error_code: str | None = None) -> None:
        """Report what you tried after a failure, and whether it worked.

        This is the half of the network another agent can actually use: a
        failure rate says a call is broken, and only an outcome says what to
        do about it. The wrapper cannot know what a program did between two
        calls -- it did not make the fix -- so from the decorator this stays
        an explicit call. `run` mode is the exception: it sees the sequence,
        and a transient failure followed by the same URL succeeding is a
        retry (or a backoff, if the program waited), which it reports as
        such. See auto.py.

        error_type and error_code name the failure this outcome belongs to
        when several shapes of the same operation are in flight; without
        them it attaches to the operation's most recent failure.

        The fingerprint is resolved when the report is sent rather than now,
        because the failure it belongs to may still be on the queue. One
        worker draining in order is what makes that safe.
        """
        body = {"service": service, "operation": operation,
                "action": action, "successful": bool(successful)}
        if error_type:
            body["error_type"] = error_type
            if error_code:
                body["error_code"] = error_code
        self._submit(body, kind="outcome")

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
            if kind == "observe" and body.get("outcome") == "failure":
                self.queued_failures += 1
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
                    self.sent_outcomes += 1
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
            self._fingerprints[(body["service"], body["operation"],
                                body["error_type"], body.get("error_code"))] = fingerprint

    def _fingerprint_for(self, body: dict) -> str | None:
        """The failure an outcome belongs to: the exact shape when the outcome
        names one and that shape has been reported, else the operation's most
        recent failure."""
        key = (body["service"], body["operation"])
        if body.get("error_type"):
            exact = self._fingerprints.get(key + (body["error_type"], body.get("error_code")))
            if exact:
                return exact
        return self._fingerprints.get(key)

    def _post_outcome(self, body: dict) -> None:
        key = (body["service"], body["operation"])
        fingerprint = self._fingerprint_for(body)
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
