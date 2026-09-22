"""Ed25519, in pure Python, because this package has no dependencies.

`failecho-autoreport` installs into somebody else's agent. It has never
needed a dependency and is not about to grow one for an optional feature, so
signing is implemented here from RFC 8032's reference algorithm. It is slow
by cryptographic standards -- a few milliseconds per signature -- and that is
irrelevant at one signature per report, on a code path that already waits on
a socket.

Correctness is checked against RFC 8032 section 7.1's test vectors and, when
`cryptography` happens to be installed, against that library's implementation
(`tests/test_identity.py`).

This is a signing primitive, not a secret store. The private key it is given
was generated and kept by the caller; nothing here writes it anywhere.
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


def generate_seed() -> bytes:
    """A new 32-byte private seed, from the OS."""
    return os.urandom(32)


def public_key(seed: bytes) -> bytes:
    if len(seed) != 32:
        raise ValueError("an Ed25519 seed is 32 bytes")
    digest = _sha512(seed)
    return _encode_point(_scalarmult(B, _clamp(digest)))


def sign(seed: bytes, message: bytes) -> bytes:
    if len(seed) != 32:
        raise ValueError("an Ed25519 seed is 32 bytes")
    digest = _sha512(seed)
    a = _clamp(digest)
    prefix = digest[32:]
    pub = _encode_point(_scalarmult(B, a))
    r = int.from_bytes(_sha512(prefix + message), "little") % L
    big_r = _encode_point(_scalarmult(B, r))
    k = int.from_bytes(_sha512(big_r + pub + message), "little") % L
    s = (r + k * a) % L
    return big_r + s.to_bytes(32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """True only for a signature this key really made. Never raises: every
    malformed input is simply not a valid signature."""
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
