"""Deterministic error-message normalization.

Goal
----
Turn a concrete error message into a *class* of error message, so that two
agents hitting the same underlying problem produce the same fingerprint:

    "Repository 918272 was not found"  ->  "Repository <N> was not found"
    "Repository 555812 was not found"  ->  "Repository <N> was not found"

Design rules
------------
1. **No LLM.** Pure regex, pure Python, fully deterministic and auditable.
2. **Conservative.** We only replace tokens that are obviously identifiers
   (UUIDs, emails, URLs, IPs, timestamps, long numbers, hex blobs, tokens).
   Words carry the semantics of an error and are never touched.
3. **Small numbers survive.** HTTP-status-like numbers (<= 3 digits) are kept
   verbatim so ``HTTP 422`` stays distinguishable from ``HTTP 500``.
4. **Privacy first.** A redaction pass runs *before* the placeholder pass so
   anything that smells like a secret (bearer tokens, API keys, JWTs, card
   numbers) is destroyed rather than merely categorised. The raw message is
   never persisted -- see :mod:`app.core.privacy`.

Order matters: broader patterns (URL) run before narrower ones (IP, number)
so a URL is replaced as a whole instead of being shredded piece by piece.
"""

from __future__ import annotations

import re

#: Normalized messages are truncated to bound storage on a small VPS.
MAX_NORMALIZED_LENGTH = 256

#: Numbers with at least this many digits are treated as identifiers.
#: 4 keeps 3-digit HTTP status codes (404, 422, 500) intact.
MIN_DIGITS_FOR_NUMERIC_ID = 4

#: Placeholders emitted by the normalizer. Handy for tests and docs.
PLACEHOLDERS = (
    "<REDACTED>",
    "<URL>",
    "<EMAIL>",
    "<UUID>",
    "<TIME>",
    "<IP>",
    "<ID>",
    "<HEX>",
    "<N>",
)

# ---------------------------------------------------------------------------
# Pass 1 -- redaction of probable secrets (privacy, not fingerprint quality)
# ---------------------------------------------------------------------------
_REDACTION_RULES: list[tuple[re.Pattern[str], str]] = [
    # key=value / key: value where the key looks like a credential
    (
        re.compile(
            r"\b(?:authorization|bearer|basic|api[_-]?key|apikey|access[_-]?token"
            r"|refresh[_-]?token|secret|client[_-]?secret|password|passwd|pwd|token)\b"
            r"\s*[:=]\s*[\"']?[A-Za-z0-9._\-/+=]{4,}[\"']?",
            re.IGNORECASE,
        ),
        "<REDACTED>",
    ),
    # "Bearer eyJhbGciOi..." / "Basic dXNlcjpwYXNz"
    (
        re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._\-/+=]{8,}", re.IGNORECASE),
        "<REDACTED>",
    ),
    # JSON Web Tokens
    (
        re.compile(r"\beyJ[A-Za-z0-9._\-]{10,}\.[A-Za-z0-9._\-]{4,}\.[A-Za-z0-9._\-]{4,}"),
        "<REDACTED>",
    ),
    # Vendor-prefixed secrets: sk_live_..., ghp_..., xoxb-..., AKIA...
    (
        re.compile(
            r"\b(?:sk|pk|rk|whsec|ghp|gho|ghs|ghu|ghr|xox[abprs])[-_][A-Za-z0-9_\-]{8,}\b"
        ),
        "<REDACTED>",
    ),
    (re.compile(r"\bAKIA[0-9A-Z]{12,}\b"), "<REDACTED>"),
    # Card-shaped digit groups: 4111 1111 1111 1111 / 4111-1111-1111-1111
    (re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{2,4}\b"), "<REDACTED>"),
    # Credentials in a URI: scheme://user:password@host
    #
    # Any scheme, not just http. A connection string is the most common way a
    # password ends up inside an error message -- "connection to
    # postgres://admin:hunter2@db:5432 refused" -- and only https was covered,
    # by the URL rule in pass 2. Everything else came through intact:
    # redis, postgres, mysql, amqp, mongodb. Tested, not assumed.
    #
    # The userinfo match is greedy to the last @ before a slash or a space,
    # which is what makes it hold for the two shapes a tighter pattern missed:
    # an empty username (redis://:secret@host) and a password containing an @
    # (mysql://root:P@ssw0rd@host). It cannot run past the host, because the
    # character class excludes both whitespace and /.
    #
    # The host is deliberately kept: it is the useful part of the failure, and
    # pass 2 turns it into <URL> or <IP> anyway where it can.
    (
        re.compile(r"\b([a-z][a-z0-9+.\-]{1,15})://[^\s/]*@", re.IGNORECASE),
        r"\1://<REDACTED>@",
    ),
]

# ---------------------------------------------------------------------------
# Pass 2 -- identifier placeholders
# ---------------------------------------------------------------------------
_NORMALIZATION_RULES: list[tuple[re.Pattern[str], str]] = [
    # URLs (incl. ws://) -- run first, they can contain IPs, ports and numbers.
    (re.compile(r"\b(?:https?|wss?|ftp)://[^\s\"'<>)\]}]+", re.IGNORECASE), "<URL>"),
    # Email addresses
    (
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        "<EMAIL>",
    ),
    # UUIDs (any version, with or without dashes handled by the hex rule below)
    (
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "<UUID>",
    ),
    # ISO-8601 timestamps: 2026-09-10T08:59:12.123Z / 2026-09-10 08:59:12+02:00
    (
        re.compile(
            r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?"
            r"(?:Z|[+-]\d{2}:?\d{2})?)?\b"
        ),
        "<TIME>",
    ),
    # Bare clock times: 08:59:12
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b"), "<TIME>"),
    # IPv4, optionally with a port
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b"), "<IP>"),
    # Prefixed request/trace/correlation identifiers: req_a1b2c3, trace-9f8e7d
    (
        re.compile(
            r"\b(?:req|request|trace|span|corr|correlation|session|txn|tx|job|run|evt|event)"
            r"[-_][A-Za-z0-9]{6,}\b",
            re.IGNORECASE,
        ),
        "<ID>",
    ),
    # Hexadecimal blobs: 0xdeadbeef, md5/sha digests. Requires >= 1 hex letter so
    # that a plain decimal number falls through to the <N> rule instead.
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<HEX>"),
    (
        re.compile(r"\b(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{8,}\b"),
        "<HEX>",
    ),
    # Long opaque tokens: >= 20 chars mixing letters and digits.
    (
        re.compile(r"\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{20,}\b"),
        "<ID>",
    ),
    # Thousands-separated numbers: 1,234,567
    (re.compile(r"\b\d{1,3}(?:,\d{3})+\b"), "<N>"),
    # Large bare integers (identifiers), optionally signed decimals.
    (re.compile(rf"\b\d{{{MIN_DIGITS_FOR_NUMERIC_ID},}}(?:\.\d+)?\b"), "<N>"),
]

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")


def redact_secrets(message: str) -> str:
    """Destroy anything that looks like a credential. Runs before placeholders."""
    for pattern, replacement in _REDACTION_RULES:
        message = pattern.sub(replacement, message)
    return message


def normalize_error(message: str | None) -> str | None:
    """Normalize a raw error message into a stable, privacy-safe form.

    Returns ``None`` for empty input. The output is deterministic: the same
    input always produces the same output, on any machine, forever.
    """
    if message is None:
        return None

    text = _CONTROL_CHARS.sub(" ", str(message))
    text = _WHITESPACE.sub(" ", text).strip()
    if not text:
        return None

    text = redact_secrets(text)
    for pattern, replacement in _NORMALIZATION_RULES:
        text = pattern.sub(replacement, text)

    text = _WHITESPACE.sub(" ", text).strip()
    if len(text) > MAX_NORMALIZED_LENGTH:
        text = text[:MAX_NORMALIZED_LENGTH].rstrip() + "..."
    return text or None
