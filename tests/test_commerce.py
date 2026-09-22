"""The commerce taxonomy, tested against a shop that fails on purpose.

`docs/commerce-failures.md` makes three claims worth checking before any
browser adapter is built: the failures fingerprint stably, the structure hash
notices a checkout changing, and the split between the merchant's failures and
a person's is enforced rather than described. No browser is involved -- the
uncertain part is the naming, not the clicking.
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

ROOT = Path(__file__).resolve().parents[1]


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

    def get(path):
        try:
            with urllib.request.urlopen(base + path, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def control(**changes):
        req = urllib.request.Request(base + "/state", data=json.dumps(changes).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)

    yield get, control
    proc.terminate()
    proc.wait(timeout=10)


# -- the structure hash, which is the point of the whole scheme ----------------


def test_the_structure_hash_notices_a_checkout_changing(shop):
    get, control = shop
    _, before = get("/checkout")
    first = fc.structure_hash(before)

    _, again = get("/checkout")
    assert fc.structure_hash(again) == first, "the same checkout must hash the same"

    control(build="v2")            # a deploy adds a coupon field
    _, after = get("/checkout")
    assert fc.structure_hash(after) != first, "a changed checkout must hash differently"


def test_the_structure_hash_ignores_prices_and_text():
    a = '<form><input name="email"><input name="card"><button id="pay-now">Pay 19.99</button></form>'
    b = '<form><input name="email"><input name="card"><button id="pay-now">Pay 24.50 today</button></form>'
    assert fc.structure_hash(a) == fc.structure_hash(b)


# -- the failures a shopping agent meets ---------------------------------------


def test_each_failure_gets_its_class(shop):
    get, control = shop
    status, html = get("/cart")
    assert fc.classify_page(status, html) == "consent_wall"

    control(stock=0)
    status, html = get("/product/1")
    assert fc.classify_page(status, html) == "out_of_stock"

    control(build="v3")            # the deploy that breaks the pay step
    status, html = get("/pay")
    assert status == 504 and fc.classify_page(status, html) == "server_error"

    control(challenge_after=1)
    get("/")
    status, html = get("/cart")
    assert fc.classify_page(status, html) == "bot_challenge"


def test_the_same_failure_fingerprints_the_same_twice(shop):
    get, control = shop
    control(build="v3")
    reports = []
    for _ in range(2):
        status, html = get("/pay")
        _, form = get("/checkout")
        reports.append(fc.report_for("shop.example", "checkout.payment",
                                     fc.classify_page(status, html), form, status))
    assert reports[0] == reports[1], "two identical failures must produce identical reports"


# -- the two rules the document insists on -------------------------------------


def test_an_account_side_failure_never_leaves_the_machine():
    for error_type in fc.ACCOUNT_SIDE:
        assert fc.shareable(error_type) is False
        assert fc.report_for("shop.example", "checkout.payment", error_type, "<form></form>") is None
        assert fc.advice_for(error_type) is None


def test_a_bot_challenge_is_never_answered_with_evasion():
    assert fc.advice_for("bot_challenge") in fc.CHALLENGE_RECOVERIES
    assert fc.advice_for("bot_challenge") == "use_site_api"


def test_a_report_carries_nothing_from_the_page(shop):
    get, control = shop
    control(build="v3")
    status, html = get("/pay")
    _, form = get("/checkout")
    report = fc.report_for("shop.example", "checkout.payment", fc.classify_page(status, html), form, status)
    flat = json.dumps(report)
    for forbidden in ("email", "address", "card", "coupon", "pay-now", "Gateway", "19.99", "/pay"):
        assert forbidden not in flat, f"{forbidden} leaked into the report: {flat}"
    assert set(report) <= {"service", "operation", "error_type", "version", "outcome", "error_code"}


def test_an_operation_outside_the_vocabulary_is_refused():
    with pytest.raises(ValueError):
        fc.report_for("shop.example", "checkout.pay_now_button", "server_error", "<form></form>")
