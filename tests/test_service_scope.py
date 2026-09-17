"""The naming-split repair: one failure, several operation names, evidence
that joins anyway -- for the failure classes that belong to the service.

The fleet's first finding: `GET /repos` from the zero-code wrapper and
`github_repo` from a decorator are the same GitHub call, and a rate limit on
one is a rate limit on the other. The evidence used to sit on two
fingerprints and help nobody.
"""

from __future__ import annotations

from tests.conftest import insert_recovery, observe, query

GH = {"service": "api.github.com", "version": None, "schema_hash": None, "error_message": None}


def failure(client, operation, error_type="rate_limit", code="403"):
    return observe(client, operation=operation, error_type=error_type, error_code=code, **GH)["fingerprint"]


def ask(client, operation, error_type="rate_limit", code="403"):
    return query(client, operation=operation, error_type=error_type, error_code=code, **GH)


def test_a_rate_limit_fix_learned_under_one_name_is_offered_under_another(client):
    fp_decorator = failure(client, "github_repo")
    for i in range(6):
        insert_recovery(fingerprint=fp_decorator, action="wait_until_reset", successful=True, minutes_ago=30 + i,
                        reporter_hash=f"r{i % 3}")
    failure(client, "GET /repos")            # the zero-code wrapper's name, no outcomes yet
    d = ask(client, "GET /repos")
    assert d["recovery_actions"] == [], "this operation has no evidence of its own"
    rec = d["recommendation"]
    assert rec is not None and rec["action"] == "wait_until_reset" and rec["scope"] == "service"
    assert rec["based_on_attempts"] == 6 and rec["unique_reporters"] == 3
    ev = d["service_evidence"]
    assert ev["operations"] == ["github_repo"] and ev["fingerprints"] == 1
    assert ev["recovery_actions"][0]["action"] == "wait_until_reset" and ev["recovery_actions"][0]["successes"] == 6


def test_the_operations_own_evidence_wins_when_it_has_any(client):
    fp_a = failure(client, "github_repo")
    fp_b = failure(client, "GET /repos")
    for i in range(6):
        insert_recovery(fingerprint=fp_a, action="wait_until_reset", successful=True, minutes_ago=30 + i, reporter_hash=f"a{i % 3}")
    for i in range(6):
        insert_recovery(fingerprint=fp_b, action="backoff", successful=True, minutes_ago=30 + i, reporter_hash=f"b{i % 3}")
    rec = ask(client, "GET /repos")["recommendation"]
    assert rec["action"] == "backoff" and rec["scope"] == "operation"
    # and the service view is still shown alongside, for the reader
    assert "github_repo" in ask(client, "GET /repos")["service_evidence"]["operations"]


def test_a_skip_at_operation_level_is_not_overridden_by_the_service(client):
    fp_a = failure(client, "github_repo")
    fp_b = failure(client, "GET /repos")
    for i in range(6):
        insert_recovery(fingerprint=fp_a, action="backoff", successful=True, minutes_ago=30 + i, reporter_hash=f"a{i % 3}")
    for i in range(8):
        insert_recovery(fingerprint=fp_b, action="backoff", successful=False, minutes_ago=10 + i, reporter_hash=f"b{i % 3}")
    rec = ask(client, "GET /repos")["recommendation"]
    assert rec["action"] == "skip" and rec["scope"] == "operation", "this operation's own recent record is the verdict"


def test_operation_specific_classes_are_never_pooled(client):
    fp = failure(client, "github_repo", "not_found", "404")
    for i in range(6):
        insert_recovery(fingerprint=fp, action="refresh_schema", successful=True, minutes_ago=30 + i, reporter_hash=f"r{i % 3}")
    failure(client, "GET /repos", "not_found", "404")
    d = ask(client, "GET /repos", "not_found", "404")
    assert d["service_evidence"] is None and d["recommendation"] is None


def test_pooling_stays_on_one_service_and_one_code(client):
    fp = failure(client, "github_repo")
    for i in range(6):
        insert_recovery(fingerprint=fp, action="backoff", successful=True, minutes_ago=30 + i, reporter_hash=f"r{i % 3}")
    # a 429 on the same service is a different code; a 403 on another host is another service
    observe(client, operation="GET /repos", error_type="rate_limit", error_code="429", **GH)
    assert ask(client, "GET /repos", "rate_limit", "429")["service_evidence"] is None
    observe(client, operation="GET /repos", error_type="rate_limit", error_code="403",
            **dict(GH, service="api.example.com"))
    assert query(client, operation="GET /repos", error_type="rate_limit", error_code="403",
                 **dict(GH, service="api.example.com"))["service_evidence"] is None


def test_the_default_scope_is_operation(client):
    fp = failure(client, "github_repo")
    for i in range(6):
        insert_recovery(fingerprint=fp, action="backoff", successful=True, minutes_ago=30 + i, reporter_hash=f"r{i % 3}")
    rec = ask(client, "github_repo")["recommendation"]
    assert rec["scope"] == "operation"
