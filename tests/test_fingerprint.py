"""Fingerprinting: the join key of the network."""

from __future__ import annotations

from app.core.fingerprint import FINGERPRINT_LENGTH, compute_fingerprint
from app.core.normalize import normalize_error

BASE = dict(
    service="github-mcp",
    operation="create_issue",
    version="2.8.1",
    schema_hash="a817ce",
    error_type="validation_error",
    error_code="422",
)


def fp(message: str, **overrides) -> str:
    fields = {**BASE, **overrides}
    return compute_fingerprint(normalized_error=normalize_error(message), **fields)


def test_same_logical_error_same_fingerprint():
    assert fp("Repository 123456 not found") == fp("Repository 987654 not found")


def test_different_error_different_fingerprint():
    assert fp("Repository 123456 not found") != fp("Permission denied")


def test_error_code_matters():
    assert fp("boom", error_code="422") != fp("boom", error_code="500")


def test_scope_fields_matter():
    message = "Repository 123456 not found"
    assert fp(message) != fp(message, version="2.9.0")
    assert fp(message) != fp(message, schema_hash="ffffff")
    assert fp(message) != fp(message, service="gitlab-mcp")
    assert fp(message) != fp(message, operation="delete_issue")


def test_case_insensitive_on_identity_fields():
    assert fp("boom", error_type="VALIDATION_ERROR") == fp(
        "boom", error_type="validation_error"
    )


def test_shape():
    value = fp("Repository 123456 not found")
    assert len(value) == FINGERPRINT_LENGTH
    assert all(c in "0123456789abcdef" for c in value)


def test_field_boundaries_cannot_collide():
    """"ab"+"c" must not hash like "a"+"bc"."""
    a = compute_fingerprint(service="ab", operation="c")
    b = compute_fingerprint(service="a", operation="bc")
    assert a != b
