"""One class for one failure.

`error_type` is part of the fingerprint and it is free text, so the same 404
arrived as `not_found` from the hook and `http_error` from anyone reading a
status off a response. Found the hard way: three queries against production
returned `known: false` for a failure that was sitting in the database.
"""

from __future__ import annotations

import pytest

from app.core.error_types import canonical_error_type as canon
from app.core.fingerprint import compute_fingerprint


@pytest.mark.parametrize(
    "written,code,expected",
    [
        ("not_found", "404", "not_found"),
        ("NotFound", None, "not_found"),
        ("not found", None, "not_found"),
        ("404", None, "not_found"),
        ("too_many_requests", "429", "rate_limit"),
        ("throttled", None, "rate_limit"),
        ("unauthorized", "401", "auth_error"),
        ("bad_request", "400", "validation_error"),
        ("deadline_exceeded", None, "timeout"),
        ("ECONNRESET", None, "connection_error"),
    ],
)
def test_synonyms_land_on_one_class(written, code, expected):
    assert canon(written, code) == expected


@pytest.mark.parametrize(
    "written,code,expected",
    [
        ("http_error", "404", "not_found"),
        ("HTTP Error", "500", "server_error"),
        ("error", "429", "rate_limit"),
        ("unknown", "403", "auth_error"),
        (None, "429", "rate_limit"),
        ("", "429", "rate_limit"),
    ],
)
def test_a_generic_class_defers_to_the_status_code(written, code, expected):
    """`http_error` says nothing on its own; the code beside it does."""
    assert canon(written, code) == expected


def test_a_specific_class_is_never_overridden_by_the_code():
    """If a caller says auth_error with a 500 they disagree with the status,
    and that disagreement is theirs to keep. Silently rewriting it would file
    their evidence under a class they never used."""
    assert canon("auth_error", "500") == "auth_error"
    assert canon("not_found", "429") == "not_found"


def test_unknown_classes_are_left_alone():
    """Never invent a class. An unrecognised value comes back tidied, not
    replaced -- somebody's internal taxonomy is not ours to reinterpret."""
    assert canon("something_bespoke", "404") == "something_bespoke"
    assert canon("Widget Exploded", None) == "widget_exploded"


def test_401_and_403_share_a_class_and_keep_their_codes():
    """The hook and the wrapper have always filed both under auth_error; a
    server table that said `forbidden` for 403 put a wrapper's report and an
    OTLP span of the same failure on two fingerprints. One class now; the
    status code stays in the fingerprint, so the two failures stay distinct
    where it counts."""
    assert canon("unauthorized", "401") == canon("forbidden", "403") == "auth_error"
    from app.core.fingerprint import compute_fingerprint

    assert compute_fingerprint(service="s", operation="o", error_type="auth_error", error_code="401") != \
        compute_fingerprint(service="s", operation="o", error_type="auth_error", error_code="403")


def test_it_is_idempotent():
    for value, code in [("http_error", "404"), ("429", None), ("NotFound", None)]:
        once = canon(value, code)
        assert canon(once, code) == once


def test_two_agents_describing_one_404_get_one_fingerprint():
    """The failure this was written for: the hook writes not_found, a caller
    reading the status writes http_error, and they never matched."""
    def fp(error_type):
        return compute_fingerprint(
            service="fetch", operation="fetch",
            error_type=error_type, error_code="404",
        )

    assert fp("http_error") == fp("not_found") == fp("NotFound") == fp("404")
    assert fp("auth_error") != fp("not_found"), "a real disagreement survives"


def test_the_vocabulary_matches_what_the_hook_emits():
    """The hook is the largest single reporter. If the server canonicalises to
    words the hook never uses, every hook report lands somewhere else."""
    from pathlib import Path

    from app.core.error_types import CANONICAL

    hook = (Path(__file__).resolve().parents[1]
            / "plugin" / "hooks" / "failecho_hook.py").read_text()
    for emitted in ("rate_limit", "auth_error", "not_found",
                    "validation_error", "server_error", "timeout",
                    "connection_error"):
        assert f'"{emitted}"' in hook, f"hook no longer emits {emitted}"
        assert emitted in CANONICAL, f"{emitted} missing from the vocabulary"
