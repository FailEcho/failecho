"""A reporter identity you can prove: an Ed25519 key, kept on this machine.

`reporter_id` has always been a self-chosen string. That is enough to count
reporters and enough to tell your own evidence from somebody else's, and it
is deliberately anonymous. What it cannot do is prove anything: any caller
can send any id, so a reporter can be impersonated, and the network's one
number that has to stay honest -- independent reporters -- rests on nothing
but goodwill.

Signing fixes exactly that much. The reporter id becomes the public key, each
report is signed by the private key that never leaves this machine, and the
server can tell a report from this reporter from a report merely *claiming* to
be from it.

**What it does not do**, and the docs say so wherever this is offered:
generating a thousand keys is free. Signing proves continuity and rules out
impersonation. It does not prove personhood and it is not Sybil resistance;
the adoption threshold, not the key, is what makes a reporter count.

Off by default. Turning it on changes this installation's reporter id from its
installation id to the key id, which starts its history over -- so it is a
decision, not a default. Enable with ``FAILECHO_SIGN=1`` or
``FailEcho(sign=True)``, or run ``python -m failecho_autoreport identity``.
"""

from __future__ import annotations

import base64
import hashlib
import os
import time

from . import _ed25519

#: Version tag inside the signed material. A future scheme changes this, and
#: an old signature can never be replayed as a new one.
SCHEME = "failecho-sig-v1"

#: How far a signed request's clock may be from the server's. Wide enough for
#: an unsynchronised container, narrow enough that a captured header is stale
#: long before anyone can reuse it.
MAX_SKEW_SECONDS = 300


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def key_path() -> str:
    """Where the private key lives. Beside the installation id, not in the
    project: a key that ends up in a repository is a key that gets published."""
    override = os.environ.get("FAILECHO_IDENTITY_KEY")
    if override:
        return override
    base = (os.environ.get("XDG_STATE_HOME")
            or os.path.join(os.path.expanduser("~"), ".local", "state"))
    return os.path.join(base, "failecho", "identity.key")


def reporter_id_for(public: bytes) -> str:
    """The reporter id a key claims. The key *is* the identity."""
    return f"ed25519:{_b64(public)}"


def signed_material(timestamp: int, method: str, path: str, body: bytes) -> bytes:
    """What the signature covers: the scheme, the clock, the request line and
    a digest of the body. Not the body itself -- the signature must not become
    a second copy of anything we send."""
    digest = hashlib.sha256(body or b"").hexdigest()
    return "\n".join((SCHEME, str(int(timestamp)), method.upper(), path, digest)).encode()


class Identity:
    """One Ed25519 key, loaded from disk or created there."""

    def __init__(self, seed: bytes):
        self.seed = seed
        self.public = _ed25519.public_key(seed)
        self.reporter_id = reporter_id_for(self.public)

    # -- storage ----------------------------------------------------------

    @classmethod
    def load(cls, path: str | None = None) -> "Identity | None":
        """The existing key, or None. Never creates one."""
        try:
            with open(path or key_path(), encoding="utf-8") as fh:
                seed = _unb64(fh.read().strip())
        except (OSError, ValueError):
            return None
        return cls(seed) if len(seed) == 32 else None

    @classmethod
    def load_or_create(cls, path: str | None = None) -> "Identity | None":
        """The key for this installation, created on first use.

        None when nothing can be written: a read-only container, a locked-down
        home. An unsigned report is worth more than a crash, and a key held
        only in memory would be a new identity every restart -- which is the
        inflation this whole mechanism exists to prevent.
        """
        existing = cls.load(path)
        if existing is not None:
            return existing
        target = path or key_path()
        seed = _ed25519.generate_seed()
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            tmp = f"{target}.tmp"
            # 0600 before anything is written to it, not after
            handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                fh.write(_b64(seed))
            os.replace(tmp, target)
        except OSError:
            return None
        return cls(seed)

    # -- use --------------------------------------------------------------

    def headers(self, method: str, path: str, body: bytes) -> dict:
        """The three headers that prove this request came from this key."""
        timestamp = int(time.time())
        signature = _ed25519.sign(self.seed, signed_material(timestamp, method, path, body))
        return {
            "X-Reporter-ID": self.reporter_id,
            "X-Reporter-Timestamp": str(timestamp),
            "X-Reporter-Signature": _b64(signature),
        }


def verify_request(reporter_id: str, timestamp: str | int, signature: str,
                   method: str, path: str, body: bytes,
                   now: float | None = None) -> bool:
    """Whether these headers really were produced by that reporter id.

    Lives here so client and server agree by construction; the server has its
    own copy (`app/core/identity.py`) because neither package may depend on
    the other, and a test checks the two against each other.
    """
    if not reporter_id.startswith("ed25519:"):
        return False
    try:
        stamp = int(timestamp)
        public = _unb64(reporter_id.split(":", 1)[1])
        raw = _unb64(signature)
    except (ValueError, TypeError):
        return False
    if abs((now if now is not None else time.time()) - stamp) > MAX_SKEW_SECONDS:
        return False
    return _ed25519.verify(public, signed_material(stamp, method, path, body), raw)
