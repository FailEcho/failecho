"""Privacy primitives and FailEcho's data-collection contract.

WHAT THIS NETWORK STORES
------------------------
Only structured failure *metadata*:

* service / operation / version / schema_hash  (which tool, which call shape)
* outcome (``success`` | ``failure``)
* error_type / error_code                      (short classifiers)
* normalized_error                             (identifiers stripped, see
                                                :mod:`app.core.normalize`)
* latency_ms
* fingerprint                                  (a SHA-256 digest)
* reporter_hash                                (salted hash, optional)
* created_at, source

WHAT THIS NETWORK NEVER STORES
------------------------------
prompts, model messages, tool arguments, tool results, request bodies,
response bodies, API keys, authorization headers, cookies, customer names,
customer emails, credit-card data, or any other secret.

How that is enforced, concretely:

1. The request schemas do not *have* fields for any of it. Unknown JSON keys
   are dropped by Pydantic before they reach the database layer, so an agent
   that accidentally sends ``{"prompt": ...}`` cannot persist it here.
2. The raw ``error_message`` is normalized in the request handler and the raw
   string is discarded -- it is never written to a column and never logged.
   Only ``normalized_error`` survives.
3. ``normalize_error`` runs a redaction pass first, so credential-shaped
   substrings become ``<REDACTED>`` rather than being categorised and kept.
4. ``X-Reporter-ID`` is salted and hashed on arrival. The raw value is never
   stored. Rotating ``FIN_REPORTER_SALT`` makes old hashes unlinkable.
5. No authentication means there is no account, no email and no billing
   identity to leak in the first place.
"""

from __future__ import annotations

import hashlib

from app.core.config import settings

#: Length of a stored reporter hash. Short enough to be cheap, long enough that
#: collisions do not distort unique-reporter counts at MVP scale.
REPORTER_HASH_LENGTH = 32


def hash_reporter_id(reporter_id: str | None) -> str | None:
    """Salt-and-hash an optional reporter identifier.

    Returns ``None`` when the caller is anonymous, which is the default and a
    fully supported mode. The raw identifier is never returned or stored.
    """
    if reporter_id is None:
        return None
    value = reporter_id.strip()
    if not value:
        return None
    digest = hashlib.sha256(f"{settings.reporter_salt}:{value}".encode("utf-8"))
    return digest.hexdigest()[:REPORTER_HASH_LENGTH]
