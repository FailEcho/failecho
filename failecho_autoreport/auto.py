"""Zero-code reporting: observe every outbound HTTP call this process makes.

    python -m failecho_autoreport run my_agent.py        # no code changes
    # or, from inside the program, one line before anything else:
    import failecho_autoreport.auto; failecho_autoreport.auto.enable()

Importing this module does nothing. Patching happens only when enable() is
called, or through the `run` command, which calls it. The first version
patched on import, and importing it in a test process turned Starlette's
TestClient -- an httpx.Client -- into a reporter: 91 rows of test traffic
reached the production network as independent adoption inside two minutes.
A library that phones home because it was imported is a library that will
phone home from somewhere it should not. Never again by default.

The decorator in the parent module asks you to name a service and an
operation per call site. That is the accurate way, and for a script you did
not write it is also the way that never happens. This module instead patches
the HTTP clients a Python program is likely to use -- urllib, requests, httpx
-- and reports every outbound call by host and route shape. The Claude Code
hook does the same for MCP tools: after the call, without the model or the
author deciding anything.

What is reported per call: the host, the method with the first path segment
(``POST /repos``), whether it failed, the HTTP status when there was one, and
the duration. Nothing else. The path beyond its first segment is dropped
before anything is recorded -- that is where the identifiers live -- and the
query string, headers and body are never read at all.

What is not touched: calls to the FailEcho endpoint itself (the wrapper
already refuses those), and anything through a client library that is not
installed. Patching is idempotent; importing this twice patches once.

Recovery outcomes, inferred. The decorator cannot know what a program did
between a failure and the next call, so there `recovered()` is explicit.
This module sees every call in order, and one pattern is unambiguous: the
same URL fails with a transient error -- 429, 5xx, a timeout, a connection
error -- and then succeeds, inside two minutes. That is a retry, and if the
program waited a second or more first, a backoff. Both are reported as
outcomes on the failure they followed, which is the half of the network
another agent can act on and the half zero-code reporting used to leave
empty. A second failure of the same URL is reported as the retry not
working. Nothing is inferred for 404s, auth or validation errors: a success
after those is a different request, not a fix. The URL is the key only in
this process's memory; what leaves is still host and route.
FAILECHO_INFER_RECOVERY=0 turns this off.

Everything here fails open. If a patch cannot be applied, the client keeps
working unobserved; if reporting raises, the caller never sees it.
"""

from __future__ import annotations

import time
import urllib.parse
import urllib.request

import os

from . import FailEcho, classify, default

_PATCHED: set[str] = set()
_fe: FailEcho | None = None

#: A failure followed by a success on the same URL inside this window is a
#: recovery; a wait of at least BACKOFF_GAP before the success makes it a
#: backoff rather than a bare retry.
INFER_WINDOW = 120.0
BACKOFF_GAP = 1.0
TRANSIENT = frozenset({"rate_limit", "server_error", "timeout", "connection_error"})
MAX_PENDING = 256
#: URL -> (when it failed, error_type, error_code), for failures a later
#: call might turn out to have recovered from. Never leaves the process.
_pending: dict[str, tuple[float, str, str | None]] = {}
_infer = True


def client() -> FailEcho:
    global _fe
    if _fe is None:
        _fe = default()
    return _fe


def route(method: str, url: str) -> tuple[str, str]:
    """(service, operation) for one request: host, and method + first segment.

    ``GET https://api.github.com/repos/foo/bar/issues?x=1`` becomes
    (``api.github.com``, ``GET /repos``). The owner, the repository, the query
    string -- gone before this function returns. Two agents hitting the same
    route on the same host must land on the same operation, and neither the
    identifiers nor the arguments may leave the process.
    """
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    segment = next((p for p in parts.path.split("/") if p), "")
    # a segment that looks like an identifier is not a route
    if segment and (segment.isdigit() or len(segment) > 40):
        segment = "_"
    return host, f"{method.upper()} /{segment}" if segment else method.upper()


def _mutates(method: str) -> bool:
    return method.upper() not in ("GET", "HEAD", "OPTIONS")


def _report(method: str, url: str, started: float, status: int | None, exc: BaseException | None) -> None:
    try:
        service, operation = route(method, url)
        if not service:
            return
        # The wrapper reports over urllib, and urllib is patched. Observing our
        # own report would report the report, forever. The parent module skips
        # failecho.com; a self-hosted or local endpoint needs this check too.
        if _netloc(url) == _netloc(client().endpoint):
            return
        elapsed = int((time.monotonic() - started) * 1000)
        failure = exc if exc is not None else (_StatusError(status) if status is not None and status >= 400 else None)
        if failure is not None:
            _infer_outcome(method, url, service, operation, failure)
            client().record_failure(service, operation, failure, elapsed, _mutates(method))
        else:
            _infer_outcome(method, url, service, operation, None)
            client().record_success(service, operation, elapsed, _mutates(method))
    except Exception:  # noqa: BLE001 - reporting must never surface
        pass


def _url_key(method: str, url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return f"{method.upper()} {(p.hostname or '').lower()}{p.path}"


def _infer_outcome(method: str, url: str, service: str, operation: str, failure: BaseException | None) -> None:
    """File an outcome for an earlier transient failure of this exact URL,
    then remember this call if it is one a later call could recover from."""
    if not _infer:
        return
    now = time.monotonic()
    key = _url_key(method, url)
    previous = _pending.pop(key, None)
    if previous is not None:
        when, prev_type, prev_code = previous
        gap = now - when
        if gap <= INFER_WINDOW:
            action = "backoff" if gap >= BACKOFF_GAP else "retry"
            client().recovered(service, operation, action, failure is None,
                               error_type=prev_type, error_code=prev_code)
            client().inferred += 1
    if failure is not None:
        error_type, error_code = classify(failure)
        if error_type in TRANSIENT:
            if len(_pending) >= MAX_PENDING:
                oldest = min(_pending, key=lambda k: _pending[k][0])
                del _pending[oldest]
            _pending[key] = (now, error_type, error_code)


def _netloc(url: str) -> tuple[str, int | None]:
    """(host, port), so a self-hosted FailEcho on the same machine as the
    agent is told apart from the agent's other local targets. Hostname alone
    made every call to 127.0.0.1 look like a report of a report."""
    p = urllib.parse.urlsplit(url)
    try:
        port = p.port
    except ValueError:
        port = None
    return (p.hostname or "").lower(), port or {"https": 443, "http": 80}.get(p.scheme)


class _StatusError(Exception):
    """An HTTP status as an exception, so the shared classifier can read it."""

    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status


# -- urllib -----------------------------------------------------------------


def _patch_urllib() -> None:
    if "urllib" in _PATCHED:
        return
    import socket
    import urllib.error

    original = urllib.request.OpenerDirector.open

    def observed(self, fullurl, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        req = fullurl if isinstance(fullurl, urllib.request.Request) else None
        url = req.full_url if req else str(fullurl)
        method = req.get_method() if req else ("POST" if data is not None else "GET")
        started = time.monotonic()
        try:
            resp = original(self, fullurl, data, timeout)
        except urllib.error.HTTPError as e:
            _report(method, url, started, e.code, None)
            raise
        except Exception as e:  # noqa: BLE001
            _report(method, url, started, None, e)
            raise
        _report(method, url, started, getattr(resp, "status", None), None)
        return resp

    urllib.request.OpenerDirector.open = observed
    _PATCHED.add("urllib")


# -- requests ---------------------------------------------------------------


def _patch_requests() -> None:
    if "requests" in _PATCHED:
        return
    try:
        import requests
    except ImportError:
        return
    original = requests.Session.request

    def observed(self, method, url, *args, **kwargs):
        started = time.monotonic()
        try:
            resp = original(self, method, url, *args, **kwargs)
        except Exception as e:  # noqa: BLE001
            _report(method, url, started, None, e)
            raise
        _report(method, url, started, resp.status_code, None)
        return resp

    requests.Session.request = observed
    _PATCHED.add("requests")


# -- httpx ------------------------------------------------------------------


def _patch_httpx() -> None:
    if "httpx" in _PATCHED:
        return
    try:
        import httpx
    except ImportError:
        return

    original_send = httpx.Client.send

    def observed(self, request, *args, **kwargs):
        started = time.monotonic()
        try:
            resp = original_send(self, request, *args, **kwargs)
        except Exception as e:  # noqa: BLE001
            _report(request.method, str(request.url), started, None, e)
            raise
        _report(request.method, str(request.url), started, resp.status_code, None)
        return resp

    httpx.Client.send = observed

    original_asend = httpx.AsyncClient.send

    async def observed_async(self, request, *args, **kwargs):
        started = time.monotonic()
        try:
            resp = await original_asend(self, request, *args, **kwargs)
        except Exception as e:  # noqa: BLE001
            _report(request.method, str(request.url), started, None, e)
            raise
        _report(request.method, str(request.url), started, resp.status_code, None)
        return resp

    httpx.AsyncClient.send = observed_async
    _PATCHED.add("httpx")


def enable() -> list[str]:
    """Patch whatever is installed. Returns the names of the libraries patched."""
    global _infer
    _infer = os.environ.get("FAILECHO_INFER_RECOVERY", "1").strip().lower() not in ("0", "false", "no", "off")
    for patch in (_patch_urllib, _patch_requests, _patch_httpx):
        try:
            patch()
        except Exception:  # noqa: BLE001 - an unpatched client is still a working client
            pass
    return sorted(_PATCHED)

