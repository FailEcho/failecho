"""Ed25519 verification, with a pure-Python fallback.

The server verifies signed reports (see :mod:`app.core.identity`). It prefers
``cryptography`` when it is installed -- faster and constant-time -- and falls
back to the reference algorithm from RFC 8032 so that a self-hosted FailEcho
needs no extra dependency to accept a signed reporter.

This is a copy of the client's implementation in
``failecho_autoreport/_ed25519.py``, minus signing. Neither package may depend
on the other -- one is published to PyPI and installs into somebody else's
agent, the other is this service -- so the arithmetic is duplicated the way
the error-classification table already is, and ``tests/test_identity.py``
checks the two against each other and against the RFC's test vectors.
"""


from __future__ import annotations

import hashlib
import os

# -- the curve, as RFC 8032 defines it ---------------------------------------

P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
I = pow(2, (P - 1) // 4, P)  # noqa: E741 - the RFC calls it I


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _x_recover(y: int) -> int:
    xx = (y * y - 1) * pow(D * y * y + 1, P - 2, P)
    x = pow(xx, (P + 3) // 8, P)
    if (x * x - xx) % P != 0:
        x = (x * I) % P
    if x % 2 != 0:
        x = P - x
    return x


BY = 4 * pow(5, P - 2, P) % P
BX = _x_recover(BY)
#: The base point, in extended coordinates (X, Y, Z, T).
B = (BX % P, BY % P, 1, BX * BY % P)
IDENTITY = (0, 1, 1, 0)


def _add(a: tuple, b: tuple) -> tuple:
    """Twisted Edwards addition, extended coordinates: no branches, no
    inversions, and no special case for doubling."""
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    aa = (y1 - x1) * (y2 - x2) % P
    bb = (y1 + x1) * (y2 + x2) % P
    cc = 2 * t1 * t2 * D % P
    dd = 2 * z1 * z2 % P
    e = bb - aa
    f = dd - cc
    g = dd + cc
    h = bb + aa
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _scalarmult(point: tuple, scalar: int) -> tuple:
    result = IDENTITY
    addend = point
    while scalar > 0:
        if scalar & 1:
            result = _add(result, addend)
        addend = _add(addend, addend)
        scalar >>= 1
    return result


def _encode_point(point: tuple) -> bytes:
    x, y, z, _ = point
    zi = pow(z, P - 2, P)
    x = x * zi % P
    y = y * zi % P
    return ((y & ~(1 << 255)) | ((x & 1) << 255)).to_bytes(32, "little")


def _decode_point(data: bytes) -> tuple | None:
    """None rather than an exception: a malformed key is a request to reject,
    not an error in our own code."""
    if len(data) != 32:
        return None
    value = int.from_bytes(data, "little")
    y = value & ~(1 << 255)
    sign = value >> 255
    x = _x_recover(y)
    if x & 1 != sign:
        x = P - x
    point = (x, y, 1, x * y % P)
    if not _on_curve(point):
        return None
    return point


def _on_curve(point: tuple) -> bool:
    x, y, z, t = point
    zi = pow(z, P - 2, P)
    x = x * zi % P
    y = y * zi % P
    return (-x * x + y * y - 1 - D * x * x * y * y) % P == 0


def _clamp(digest: bytes) -> int:
    scalar = bytearray(digest[:32])
    scalar[0] &= 248
    scalar[31] &= 127
    scalar[31] |= 64
    return int.from_bytes(scalar, "little")


# -- the three operations anyone here needs ----------------------------------


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """True only for a signature that key really made. Never raises: a
    malformed key or signature is simply not a valid one."""
    if _fast_verify is not None:
        return _fast_verify(public, message, signature)
    return _slow_verify(public, message, signature)


def _slow_verify(public: bytes, message: bytes, signature: bytes) -> bool:
    try:
        if len(signature) != 64 or len(public) != 32:
            return False
        big_r = _decode_point(signature[:32])
        point_a = _decode_point(public)
        if big_r is None or point_a is None:
            return False
        s = int.from_bytes(signature[32:], "little")
        if s >= L:
            return False          # non-canonical S: malleable, so refused
        k = int.from_bytes(_sha512(signature[:32] + public + message), "little") % L
        left = _scalarmult(B, s)
        right = _add(big_r, _scalarmult(point_a, k))
        return _encode_point(left) == _encode_point(right)
    except Exception:  # noqa: BLE001 - a bad key is a "no", not a crash
        return False


def _load_fast():
    """`cryptography` if it is here, otherwise nothing. Not a requirement:
    a self-hosted instance must work with the standard library alone."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except Exception:  # noqa: BLE001
        return None

    def fast(public: bytes, message: bytes, signature: bytes) -> bool:
        try:
            Ed25519PublicKey.from_public_bytes(public).verify(signature, message)
            return True
        except (InvalidSignature, ValueError, TypeError):
            return False

    return fast


_fast_verify = _load_fast()
