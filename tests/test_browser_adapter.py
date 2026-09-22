"""The browser adapter, against the shop that fails on purpose.

No browser is installed here and none is needed: the adapter never imports
Playwright, it wraps whatever page object it is handed. So the page is a stub
that behaves like a driver -- it fetches over HTTP, it raises the errors
Playwright raises, and it refuses to click a button that is not on the page.
What is being tested is the part that would be wrong in production: the class
each failure gets, what leaves the machine, and that a driver's error still
reaches the caller unchanged.

A real browser only adds pixels to this. When one is available (the user's own
machine, not this 500 MB host) the same test should run against Playwright by
swapping FakePage out.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import failecho_commerce as fc
from failecho_commerce.browser import classify_exception, watch

ROOT = Path(__file__).resolve().parents[1]


# -- a driver, without a browser ----------------------------------------------


class DriverTimeout(Exception):
    """What Playwright raises when a selector never appears."""


class FakePage:
    """The small part of a page object the adapter touches."""

    def __init__(self, base: str):
        self.base = base
        self.html = ""
        self.last_status: int | None = None
        self.clicks: list[str] = []

    def content(self) -> str:
        return self.html

    def goto(self, path: str) -> int:
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as r:
                self.last_status, self.html = r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            self.last_status, self.html = e.code, e.read().decode()
        if self.last_status >= 500:
            raise DriverTimeout(f"net::ERR_EMPTY_RESPONSE at {path}")
        return self.last_status

    def click(self, selector: str):
        if selector.lstrip("#") not in self.html:
            raise DriverTimeout(
                f'Timeout 30000ms exceeded while waiting for selector "{selector}"')
        self.clicks.append(selector)
        return True

    def fill(self, selector: str, value: str):
        return self.click(selector)

    def title(self) -> str:                      # not watched: passed through
        return "shop"


class Recorder:
    """Stands in for a FailEcho reporter: keeps the bodies, sends nothing."""

    def __init__(self, answer=None):
        self.bodies: list[dict] = []
        self.answer = answer
        self.checks: list[tuple] = []

    def _submit(self, body, kind="observe"):
        self.bodies.append(body)

    def check(self, service, operation, error_type=None, error_code=None, timeout=None):
        self.checks.append((service, operation, error_type, error_code))
        return self.answer


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def shop():
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "failecho_commerce.shop", str(port)],
                            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.terminate()
        pytest.fail("the shop never came up")
    base = f"http://127.0.0.1:{port}"

    def control(**changes):
        req = urllib.request.Request(base + "/state", data=json.dumps(changes).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)

    yield base, control
    proc.terminate()
    proc.wait(timeout=10)


# -- the failure a shopping agent actually meets -------------------------------


def test_a_renamed_pay_button_is_reported_as_the_shape_that_changed(shop):
    base, control = shop
    fe = Recorder()
    page = watch(FakePage(base), site="shop.example", fe=fe)

    page.goto("/checkout", op="checkout.start")
    page.click("#pay-now", op="checkout.payment")          # v1: works
    assert page.successes == 2

    control(build="v3")                                    # the deploy
    page.goto("/checkout", op="checkout.start")
    with pytest.raises(DriverTimeout):
        page.click("#pay-now", op="checkout.payment")

    report = page.reports[-1]
    assert report["error_type"] == "element_missing"
    assert report["operation"] == "checkout.payment"
    assert report["service"] == "shop.example"
    assert fe.bodies[-1] == report, "the reporter got exactly what we recorded"


def test_successes_are_reported_too_so_the_rate_has_a_denominator(shop):
    base, _ = shop
    fe = Recorder()
    page = watch(FakePage(base), site="shop.example", fe=fe)
    page.goto("/checkout", op="checkout.start")
    page.click("#pay-now", op="checkout.payment")
    outcomes = [b["outcome"] for b in fe.bodies]
    assert outcomes == ["success", "success"]
    assert all("latency_ms" in b for b in fe.bodies)


def test_a_challenge_page_is_caught_where_it_happens_not_one_click_later(shop):
    """A driver does not raise on a 403: it navigates to the challenge and
    returns. The agent only finds out at the next click, which looks like a
    missing button and would teach the network a lie."""
    base, control = shop
    fe = Recorder()
    page = watch(FakePage(base), site="shop.example", fe=fe)
    control(challenge_after=1)
    page.goto("/", op="product.open")
    page.goto("/checkout", op="checkout.start")            # 403, raises nothing

    assert page.reports[-1]["error_type"] == "bot_challenge"
    assert page.reports[-1]["error_code"] == "403"
    assert fe.bodies[-1]["outcome"] == "failure"
    assert page.successes == 1, "the challenge page is not a success"


def test_a_challenge_is_never_answered_with_a_way_around_it(shop):
    base, control = shop
    fe = Recorder()
    page = watch(FakePage(base), site="shop.example", fe=fe)
    control(challenge_after=1)
    page.goto("/", op="product.open")
    page.goto("/checkout", op="checkout.start")
    line = page.last_advice
    assert "use_site_api" in line
    assert not any(word in line.lower() for word in ("captcha", "bypass", "evade", "proxy", "user-agent"))


def test_a_declined_card_never_leaves_the_machine(shop):
    base, control = shop
    fe = Recorder()
    page = watch(FakePage(base), site="shop.example", fe=fe)
    control(card="declined")
    page.goto("/pay", op="checkout.payment")               # 200, declined text
    with pytest.raises(DriverTimeout):
        page.click("#order", op="checkout.confirm")

    assert page.reports == []
    assert fe.bodies == [], "a decline is neither the merchant's failure nor a success"


def test_a_second_failure_after_a_redeploy_asks_for_a_human(shop):
    base, control = shop
    page = watch(FakePage(base), site="shop.example", fe=Recorder())

    page.goto("/checkout", op="checkout.payment")
    with pytest.raises(DriverTimeout) as first:
        page.click("#express-pay", op="checkout.payment")   # never on this site
    assert "reload" in first.value.failecho["advice"]       # shape unseen before

    control(build="v2")                                     # a deploy adds a field
    page.goto("/checkout", op="checkout.payment")
    with pytest.raises(DriverTimeout) as second:
        page.click("#express-pay", op="checkout.payment")
    assert "human_handoff" in second.value.failecho["advice"]


# -- the contract: never raises, never changes what happened -------------------


def test_the_driver_error_reaches_the_caller_unchanged(shop):
    base, control = shop
    control(build="v3")
    page = watch(FakePage(base), site="shop.example", fe=Recorder())
    page.goto("/checkout", op="checkout.start")
    with pytest.raises(DriverTimeout) as caught:
        page.click("#pay-now", op="checkout.payment")
    assert 'waiting for selector "#pay-now"' in str(caught.value)


def test_a_page_that_cannot_be_read_costs_a_class_not_an_exception(shop):
    base, control = shop
    control(build="v3")
    inner = FakePage(base)
    inner.goto("/checkout")
    inner.content = lambda: (_ for _ in ()).throw(RuntimeError("target closed"))
    page = watch(inner, site="shop.example", fe=Recorder())
    with pytest.raises(DriverTimeout):
        page.click("#pay-now", op="checkout.payment")
    assert page.reports[-1]["error_type"] == "element_missing"


def test_a_step_outside_the_vocabulary_is_watched_by_nobody(shop):
    base, control = shop
    control(build="v3")
    fe = Recorder()
    page = watch(FakePage(base), site="shop.example", fe=fe)
    page.goto("/checkout", op="checkout.start")
    with pytest.raises(DriverTimeout):
        page.click("#pay-now", op="checkout.pay_button")    # not a step name
    assert page.reports == [] and len(fe.bodies) == 1


def test_everything_else_passes_through(shop):
    base, _ = shop
    page = watch(FakePage(base), site="shop.example")
    assert page.title() == "shop"                           # not watched
    assert page.goto("/", op="product.open") == 200          # return value kept
    assert page.click("a", op="product.open") is True
    assert page._page.clicks == ["a"]


def test_a_report_carries_nothing_from_the_page(shop):
    base, control = shop
    control(build="v3")
    page = watch(FakePage(base), site="shop.example", fe=Recorder())
    page.goto("/checkout", op="checkout.start")
    with pytest.raises(DriverTimeout):
        page.click("#pay-now", op="checkout.payment")
    flat = json.dumps(page.reports[-1])
    for forbidden in ("pay-now", "email", "card", "coupon", "checkout\">", "127.0.0.1", "selector"):
        assert forbidden not in flat, f"{forbidden} leaked: {flat}"
    assert set(page.reports[-1]) <= {"service", "operation", "error_type", "version",
                                     "outcome", "error_code", "latency_ms"}


# -- classification on its own --------------------------------------------------


@pytest.mark.parametrize("message,expected", [
    ('Timeout 30000ms exceeded while waiting for selector "#pay"', "element_missing"),
    ("strict mode violation: locator resolved to 0 elements", "element_missing"),
    ("net::ERR_CONNECTION_REFUSED at https://shop.example/checkout", "server_error"),
    ("page.goto: Navigation failed because page crashed", "navigation_timeout"),
])
def test_a_drivers_own_words_map_to_the_taxonomy(message, expected):
    assert classify_exception(DriverTimeout(message)) == expected


def test_the_page_beats_the_drivers_words():
    challenge = "<html><body>Are you a robot? Complete the challenge.</body></html>"
    exc = DriverTimeout('waiting for selector "#pay"')
    assert classify_exception(exc) == "element_missing"
    assert classify_exception(exc, challenge, 403) == "bot_challenge"


def test_account_side_text_is_recognised_so_it_can_be_dropped():
    assert classify_exception(DriverTimeout("x"), "<p>Your card was declined.</p>", 200) in fc.ACCOUNT_SIDE
