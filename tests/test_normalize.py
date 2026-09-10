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
