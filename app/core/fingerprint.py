"""Deterministic failure fingerprinting.

A fingerprint is the join key of the whole network: it is what lets Agent B
recognise that it is hitting the exact failure Agent A reported 40 seconds ago.

    fingerprint = sha256(
        canonical_service | operation | version | schema_hash |
        error_type | error_code | normalized_error
    )

Properties we care about:

* **Deterministic** -- same logical failure, same fingerprint, on any node.
* **Cheap** -- one hash, no I/O, no model call.
* **Opaque** -- the digest leaks nothing on its own.
"""

from __future__ import annotations

import hashlib

from app.core.aliases import canonical_service

#: Truncated SHA-256. 128 bits of digest is far more than enough at this scale
#: and keeps URLs, logs and JSON payloads readable.
FINGERPRINT_LENGTH = 32

#: Byte that cannot appear in the input fields, so field boundaries are
#: unambiguous ("ab"+"c" can never collide with "a"+"bc").
_SEPARATOR = "\x1f"


def _canon(value: str | None) -> str:
    """Canonicalize one field: ``None`` -> "", trimmed, case-folded.

    Case folding means ``VALIDATION_ERROR`` and ``validation_error`` are the
    same failure class, which is what agents actually mean.
    """
    if value is None:
        return ""
    return str(value).strip().casefold()


def compute_fingerprint(
    *,
    service: str,
    operation: str,
    version: str | None = None,
    schema_hash: str | None = None,
    error_type: str | None = None,
    error_code: str | None = None,
    normalized_error: str | None = None,
) -> str:
    """Return the fingerprint for a normalized failure signature.

    ``normalized_error`` must already have been through
    :func:`app.core.normalize.normalize_error`.
    """
    payload = _SEPARATOR.join(
        _canon(part)
        for part in (
            canonical_service(service),
            operation,
            version,
            schema_hash,
            error_type,
            error_code,
            normalized_error,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]
