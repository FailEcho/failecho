"""A shop that fails on purpose, so the commerce taxonomy can be tested.

`docs/commerce-failures.md` claims a naming scheme and a set of failure
classes for agents that shop. Nothing verifies it. Pointing agents at real
merchants to find out is not an option: it is their infrastructure, their
terms, and their bot protection.

So this is a storefront of our own, with the failures written down rather
than discovered: an element that moves when the "site build" changes, a
consent overlay, an item that goes out of stock, a payment step that times
out, a challenge page. Standard library only, no browser needed -- the flows
are plain HTTP, which is enough to test the part that is actually uncertain:
do these failures fingerprint stably, and does the structure hash notice when
the checkout changes?

    python -m failecho_commerce.shop 8099 --build v1

The build id decides the shape of the checkout form (and so the structure
hash) and which step is broken. Nothing here talks to FailEcho.
"""

from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: Each build changes the checkout form's fields, the way a real site does
#: between deploys, and breaks one step. The names are what an adapter would
#: hash into `version`.
BUILDS = {
    "v1": {"fields": ["email", "address", "card"], "broken": None},
    "v2": {"fields": ["email", "address", "card", "coupon"], "broken": None},
    # the deploy that breaks agents: the pay button is renamed
    "v3": {"fields": ["email", "address", "card", "coupon"], "broken": "checkout.payment"},
}

STATE = {"build": "v1", "stock": 3, "challenge_after": 0, "requests": 0}


def _form(build: str) -> str:
    fields = "".join(f'<input name="{f}">' for f in BUILDS[build]["fields"])
    button = "pay-now" if BUILDS[build]["broken"] != "checkout.payment" else "complete-order"
    return f'<form id="checkout">{fields}<button id="{button}">Pay</button></form>'


class Shop(BaseHTTPRequestHandler):
    """Six steps, each able to fail in one named way."""

    def _send(self, code: int, body: str, ctype: str = "text/html") -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Shop-Build", STATE["build"])
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        STATE["requests"] += 1
        path = self.path.split("?")[0]
        build = BUILDS[STATE["build"]]

        # a challenge page after N requests, the way anti-bot protection
        # appears partway through a session
        if STATE["challenge_after"] and STATE["requests"] > STATE["challenge_after"]:
            return self._send(403, "<html><body>Are you a robot? Complete the challenge.</body></html>")

        if path == "/":
            return self._send(200, "<html><body><a href='/product/1'>Widget</a></body></html>")
        if path.startswith("/product/"):
            if STATE["stock"] <= 0:
                return self._send(200, "<html><body><span id='stock'>Out of stock</span></body></html>")
            return self._send(200, "<html><body><button id='add-to-cart'>Add</button></body></html>")
        if path == "/cart":
            return self._send(200, "<html><body><div id='cart'>1 item</div>"
                                   "<div id='consent'>Accept cookies</div></body></html>")
        if path == "/checkout":
            return self._send(200, f"<html><body>{_form(STATE['build'])}</body></html>")
        if path == "/pay":
            if build["broken"] == "checkout.payment":
                time.sleep(0.2)
                return self._send(504, "<html><body>Gateway timeout</body></html>")
            return self._send(200, '<html><body><div id="order">confirmed</div></body></html>')
        if path == "/state":
            return self._send(200, json.dumps(STATE), "application/json")
        return self._send(404, "<html><body>No such page</body></html>")

    def do_POST(self):  # noqa: N802
        """Control endpoints, so a test can deploy a new build or empty the
        shelf without restarting the server."""
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or "{}")
        STATE.update({k: v for k, v in body.items() if k in STATE})
        return self._send(200, json.dumps(STATE), "application/json")

    def log_message(self, *args):
        pass


def serve(port: int = 8099, build: str = "v1") -> None:
    STATE["build"] = build
    ThreadingHTTPServer(("127.0.0.1", port), Shop).serve_forever()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    build = sys.argv[sys.argv.index("--build") + 1] if "--build" in sys.argv else "v1"
    serve(port, build)
