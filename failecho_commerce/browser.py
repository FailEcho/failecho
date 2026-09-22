"""Watch a browser page, the way the wrapper watches a function.

    from failecho_commerce.browser import watch

    page = watch(page, site="example.com")
    page.click("#pay", op="checkout.payment")

A failed step is classified, reported as its shape only, and the network's
advice is attached to the exception the agent already handles -- the same
contract as ``failecho_autoreport``: never raises, never blocks, never
changes what the call returned or raised.

Nothing here imports Playwright. It wraps whatever page object it is given
and recognises failures by what they say, so it works with Playwright, with
Puppeteer through a binding, and with a stub in a test. That also means no
browser is needed to develop or test it, which matters: a browser is 400-700
MB and this project's host does not have it to spare.

What is sent, and nothing else (see docs/commerce-failures.md):

    service   the registrable domain              example.com
    operation a step from the vocabulary          checkout.payment
    version   a hash of the page's shape          3f0a91c2...
    error_type / error_code / latency

Never the URL beyond the domain, the page text, the selector, the form
values, the prices or the order. Account-side failures -- a declined card, a
rejected address -- are not reported at all: they are about one person, they
do not generalise, and the network has nothing to say about them.
"""

from __future__ import annotations

import time

from . import ACCOUNT_SIDE, OPERATIONS, advice_for, classify_page, report_for

#: Methods worth watching: the ones that fail when a site changes.
WATCHED = ("click", "fill", "goto", "press", "check", "select_option", "type",
           "wait_for_selector", "wait_for_url", "hover", "tap")

#: What a driver says when it cannot find or reach something, mapped to the
#: taxonomy. Matched against the exception text, lowercased.
_SIGNS = (
    ("element_missing", ("no node found", "waiting for selector", "not visible", "strict mode violation",
                         "element is not attached", "locator resolved to 0", "no element matching")),
    ("navigation_timeout", ("timeout", "navigation failed", "exceeded while waiting", "net::err_timed_out")),
    ("server_error", ("net::err_connection", "net::err_empty_response", "econnreset")),
)


#: Steps where a page can say "no" with a 200 and a friendly sentence. Only
#: these read the page on the way out: in a real browser `content()` costs
#: milliseconds, and paying it on every click would be a tax on the whole run.
SENSITIVE_STEPS = ("checkout.payment", "checkout.confirm", "auth.login", "auth.2fa")


#: Text that means the failure belongs to this person's account, not to the
#: merchant. Recognised so it can be dropped, not so it can be reported.
_ACCOUNT_SIGNS = (
    ("payment_declined", ("card was declined", "payment declined", "do not honor", "card declined")),
    ("insufficient_funds", ("insufficient funds", "not enough funds")),
    ("address_rejected", ("address could not be verified", "invalid address", "we do not ship")),
    ("session_expired", ("session has expired", "please sign in again", "session timed out")),
)


def classify_exception(exc: BaseException, html: str = "", status: int | None = None) -> str:
    """The failure class for a step that raised.

    The page is consulted first when we have it: a consent overlay or a
    challenge page makes every selector disappear, and reporting that as
    `element_missing` would teach the network the wrong thing.
    """
    page_text = (html or "").lower()
    for name, needles in _ACCOUNT_SIGNS:
        if any(n in page_text for n in needles):
            return name          # dropped upstream; never shared
    if html or status:
        page_class = classify_page(status or 200, html)
        if page_class in ("bot_challenge", "consent_wall", "auth_wall", "out_of_stock", "rate_limit"):
            return page_class
    # The message first, the exception's class name only if the message said
    # nothing: every Playwright error is a `TimeoutError` of some kind, and
    # letting the class name vote turns a refused connection into a timeout.
    for text in (str(exc).lower(), type(exc).__name__.lower()):
        for name, needles in _SIGNS:
            if any(n in text for n in needles):
                return name
    return "element_missing"


class _Silent(Exception):
    """A step that returned; there is no driver error to classify."""


class WatchedPage:
    """A page object with the same methods, watched where it matters."""

    def __init__(self, page, site: str, fe=None, advise: bool = True, default_op: str | None = None):
        self._page = page
        self._site = site.lower()
        self._fe = fe
        self._advise = advise
        self._default_op = default_op
        #: what was reported, for tests and for a caller that wants to see it
        self.reports: list[dict] = []
        self.successes = 0
        #: the last shape seen per step, so a redeploy is visible locally
        self._shapes: dict[str, str] = {}
        #: the last advice produced, for a failure that raised nothing
        self.last_advice: str | None = None

    # -- pass everything else through, untouched --------------------------

    def __getattr__(self, name):
        attr = getattr(self._page, name)
        if name not in WATCHED or not callable(attr):
            return attr

        def watched(*args, op: str | None = None, **kwargs):
            operation = op or self._default_op
            started = time.monotonic()
            try:
                result = attr(*args, **kwargs)
            except BaseException as exc:   # noqa: BLE001 - re-raised below
                if operation in OPERATIONS:
                    self._handle(exc, operation, int((time.monotonic() - started) * 1000))
                raise
            if operation in OPERATIONS:
                self._succeeded(operation, int((time.monotonic() - started) * 1000))
            return result

        return watched

    # -- the failure path --------------------------------------------------

    def _page_state(self) -> tuple[str, int | None]:
        """The page's shape and status, best effort. A driver that cannot
        answer costs us a poorer classification, never an exception."""
        html = ""
        try:
            content = getattr(self._page, "content", None)
            if callable(content):
                html = content() or ""
        except Exception:  # noqa: BLE001
            html = ""
        status = getattr(self._page, "last_status", None)
        return html, status if isinstance(status, int) else None

    def _send(self, body: dict) -> None:
        """Hand a finished body to the reporter's queue.

        Not `record_failure`: that classifies the exception itself, and the
        class here comes from the page, which knows more -- a challenge page
        looks exactly like a missing element to a driver. The body is already
        shape-only, so nothing is re-derived from the exception's text.
        """
        if self._fe is None:
            return
        submit = getattr(self._fe, "_submit", None)
        if callable(submit):
            submit(dict(body))

    def _succeeded(self, operation: str, latency_ms: int) -> None:
        """A step that returned. That is not the same as a step that worked:
        a driver navigates to a challenge page or a login wall without raising
        anything, and the agent only discovers it one click later, where it
        looks like a missing button. The status is already known, so the cheap
        check is worth it; the page is only read when the status is bad."""
        try:
            status = getattr(self._page, "last_status", None)
            if isinstance(status, int) and status >= 400:
                self._silent_failure(operation, status, latency_ms)
                return
            if operation in SENSITIVE_STEPS:
                html, _ = self._page_state()
                if classify_exception(_Silent(), html, status) in ACCOUNT_SIDE:
                    # the card was declined on a 200 page: this is not the
                    # merchant failing and it is not a success either, so the
                    # honest thing is to count nothing
                    return
            self.successes += 1
            self._send({"service": self._site, "operation": operation,
                        "outcome": "success", "latency_ms": latency_ms})
        except Exception:  # noqa: BLE001 - never reaches the caller
            pass

    def _silent_failure(self, operation: str, status: int, latency_ms: int) -> None:
        """A step that returned a page it should not have."""
        html, _ = self._page_state()
        error_type = classify_page(status, html)
        if error_type in ACCOUNT_SIDE:
            return
        report = report_for(self._site, operation, error_type, html, status)
        if report is None:
            return
        report["latency_ms"] = latency_ms
        known = self._shapes.get(operation)
        self._shapes[operation] = report["version"]
        self.reports.append(report)
        self._send(report)
        if self._advise:
            self.last_advice = self._advice_line(
                report, error_type, known is not None and known != report["version"])

    def _handle(self, exc: BaseException, operation: str, latency_ms: int) -> None:
        try:
            html, status = self._page_state()
            error_type = classify_exception(exc, html, status)
            if error_type in ACCOUNT_SIDE:
                return                      # one person's card: nothing to share
            report = report_for(self._site, operation, error_type, html, status)
            if report is None:
                return
            report["latency_ms"] = latency_ms
            # Did this step's page change shape since we last saw it fail or
            # work? That is the difference between "reload" and "the site
            # was redeployed, a human has to look".
            known = self._shapes.get(operation)
            changed = known is not None and known != report["version"]
            self._shapes[operation] = report["version"]
            self.reports.append(report)
            self._send(report)
            if self._advise:
                self._attach(exc, report, error_type, changed)
        except Exception:  # noqa: BLE001 - reporting never reaches the caller
            pass

    def _attach(self, exc: BaseException, report: dict, error_type: str, changed: bool) -> None:
        line = self._advice_line(report, error_type, changed)
        self.last_advice = line
        if not line:
            return
        try:
            exc.failecho = {"report": report, "advice": line}
        except Exception:  # noqa: BLE001 - some exceptions take no attributes
            pass
        if hasattr(exc, "add_note"):
            exc.add_note(line)

    def _advice_line(self, report: dict, error_type: str, changed: bool) -> str | None:
        answer = None
        if self._fe is not None:
            answer = self._fe.check(report["service"], report["operation"], error_type, report.get("error_code"))
        line = None
        if answer is not None:
            from failecho_autoreport import FailEcho
            line = FailEcho.advice_text(answer)
        if line is None:
            # nothing from the network: say what this class means locally,
            # which for a challenge is never a way around it
            suggestion = advice_for(error_type, {"structure_changed": changed})
            line = f"FailEcho: no evidence yet; for {error_type} the usual answer is {suggestion}." if suggestion else None
        return line


def watch(page, site: str, fe=None, advise: bool = True, default_op: str | None = None) -> WatchedPage:
    return WatchedPage(page, site, fe=fe, advise=advise, default_op=default_op)
