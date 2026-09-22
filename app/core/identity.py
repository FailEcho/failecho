"""Verifying a signed reporter.

``X-Reporter-ID`` is self-chosen and unverified, which is correct for an
anonymous network and useless as proof: any caller can claim any id. A
reporter who wants its evidence to be attributable can instead make its id an
Ed25519 public key and sign each request with the private one.

    X-Reporter-ID         ed25519:<base64url public key>
    X-Reporter-Timestamp  <unix seconds>
    X-Reporter-Signature  <base64url signature>

signed over ``failecho-sig-v1 \\n timestamp \\n METHOD \\n path \\n sha256(body)``.

What this buys, exactly: a report attributed to a key was made by the holder
of that key, and a reporter's history cannot be taken over by someone who
guesses its id. What it does not buy, and the front page must never imply
otherwise: **it is not Sybil resistance.** A thousand keys cost nothing. What
makes a reporter count towards adoption is the threshold in
:mod:`app.core.adoption`, not the signature.

A bad signature is refused with 400 rather than silently downgraded to
unsigned: a reporter that believes it is signing and is not would never find
out, and would be counted as one of the anonymous crowd it was trying to
leave.

The verification itself is pure Python (``app/core/ed25519.py``, ~4 ms), and
uses ``cryptography`` when that happens to be installed. Nothing here is on
the read path an agent waits on mid-failure unless that agent chose to sign.
"""

from __future__ import annotations

import base64
import hashlib
import time

from app.core import ed25519

SCHEME = "failecho-sig-v1"
PREFIX = "ed25519:"

#: Clock skew a signed request may carry. Wide enough for an unsynchronised
#: container, narrow enough that a captured header is stale before it is
#: worth replaying.
MAX_SKEW_SECONDS = 300

SIGNATURE_HEADER = "X-Reporter-Signature"
TIMESTAMP_HEADER = "X-Reporter-Timestamp"


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def is_key_id(reporter_id: str | None) -> bool:
    return bool(reporter_id) and reporter_id.startswith(PREFIX)


def signed_material(timestamp: int, method: str, path: str, body: bytes) -> bytes:
    """What the signature covers.

    The body's digest, never the body: a signature must not become a second
    copy of anything the network was careful not to store.
    """
    digest = hashlib.sha256(body or b"").hexdigest()
    return "\n".join((SCHEME, str(int(timestamp)), method.upper(), path, digest)).encode()


def verify(reporter_id: str, timestamp: str | int | None, signature: str | None,
           method: str, path: str, body: bytes, now: float | None = None) -> bool:
    """Whether this request really came from the key it names."""
    if not is_key_id(reporter_id) or not signature or timestamp is None:
        return False
    try:
        stamp = int(timestamp)
        public = _unb64(reporter_id[len(PREFIX):])
        raw = _unb64(signature)
    except (ValueError, TypeError):
        return False
    if abs((now if now is not None else time.time()) - stamp) > MAX_SKEW_SECONDS:
        return False
    return ed25519.verify(public, signed_material(stamp, method, path, body), raw)
