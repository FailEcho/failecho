"""Normalization: the same logical error must always reduce to the same text."""

from __future__ import annotations

import pytest

from app.core.normalize import normalize_error


def test_large_numeric_ids_collapse():
    assert normalize_error("Repository 918272 was not found") == (
        "Repository <N> was not found"
    )
    assert normalize_error("Repository 555812 was not found") == (
        "Repository <N> was not found"
    )


def test_http_status_codes_survive():
    """422 and 500 must stay distinguishable -- they are the semantics."""
    assert normalize_error("HTTP 422 unprocessable") == "HTTP 422 unprocessable"
    assert normalize_error("HTTP 500 server error") == "HTTP 500 server error"
    assert normalize_error("HTTP 422 unprocessable") != normalize_error(
        "HTTP 500 server error"
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("user 3f2504e0-4f89-11d3-9a0c-0305e82c3301 gone", "user <UUID> gone"),
        ("mail to bob.smith+x@example.com failed", "mail to <EMAIL> failed"),
        ("GET https://api.example.com/v1/x?y=2 failed", "GET <URL> failed"),
        ("connection reset by 10.0.12.7", "connection reset by <IP>"),
        ("expired at 2026-09-10T08:59:12Z", "expired at <TIME>"),
        ("digest deadbeefcafe1234 mismatch", "digest <HEX> mismatch"),
        ("trace req_9f8e7d6c5b4a lost", "trace <ID> lost"),
        ("count 1,234,567 exceeded", "count <N> exceeded"),
    ],
)
def test_placeholders(raw, expected):
    assert normalize_error(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "token=sk_live_abcdefgh12345678 rejected",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sIgnAtUre",
        "api_key: AKIAIOSFODNN7EXAMPLE denied",
        "card 4111 1111 1111 1111 declined",
    ],
)
def test_secrets_are_redacted(raw):
    """Credential-shaped input is destroyed, not merely categorised."""
    normalized = normalize_error(raw)
    assert "<REDACTED>" in normalized
    for secret in ("sk_live_abcdefgh12345678", "AKIAIOSFODNN7EXAMPLE", "sIgnAtUre"):
        assert secret not in normalized


def test_semantics_are_preserved():
    """Words carry the meaning of an error and must never be touched."""
    assert normalize_error("Repository 1 was not found") == (
        "Repository 1 was not found"
    )
    assert normalize_error("permission denied for branch main") == (
        "permission denied for branch main"
    )


def test_idempotent():
    once = normalize_error("User 123456 hit https://x.io at 2026-09-10T08:59:12Z")
    assert normalize_error(once) == once


def test_empty_input():
    assert normalize_error(None) is None
    assert normalize_error("   ") is None


def test_long_messages_are_truncated():
    normalized = normalize_error("x" * 5000)
    assert len(normalized) <= 260


# ---------------------------------------------------------------------------
# credentials inside a connection string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message,secret",
    [
        ("redis://:sup3rs3cret@10.0.0.5:6379 refused", "sup3rs3cret"),
        ("postgres://admin:hunter2@localhost:5432/prod", "hunter2"),
        ("mysql://root:P@ssw0rd!@db01:3306/app", "ssw0rd"),
        ("amqp://guest:guestpass@rabbit:5672", "guestpass"),
        ("mongodb+srv://u:pw123@cluster0.abc.mongodb.net/x", "pw123"),
        ("https://user:secret@api.example.com/v1", "secret"),
        ("sftp://deploy:kEy123@files.internal/out", "kEy123"),
    ],
)
def test_a_password_in_a_connection_string_never_survives(message, secret):
    """The most ordinary way a password reaches an error message, and it was
    getting through. Only https was covered, by the URL rule; redis, postgres,
    mysql, amqp and sftp all passed the password along in clear text. The two
    shapes that beat a tighter first attempt are both here: an empty username
    (redis://:secret@host) and a password containing an @."""
    out = normalize_error(message)
    assert secret not in out, out
    assert "<REDACTED>" in out


@pytest.mark.parametrize(
    "message",
    [
        "connect to postgres://db.internal:5432 refused",
        "plain https://example.com/path?q=1 failed",
        "user alice@example.com not permitted",
        "image https://cdn.example.com/logo@2x.png missing",
        "See https://a.example.com and mail me@b.example.com",
    ],
)
def test_the_credential_rule_does_not_eat_ordinary_urls(message):
    """It must not run past the host: a URL with an @ later in the path, or a
    sentence with a URL and an address in it, are not credentials."""
    out = normalize_error(message)
    assert "<REDACTED>" not in out, out
