"""The network saying "nothing works": recommendation.action == "skip".

The lab's first day ended in a tie between agents that asked and agents
that retried blind, because when every retry on record had failed the
network still had no recommendation, and the asker retried anyway. Saying
what worked is half the job; saying when nothing does is the other half.
"""

from __future__ import annotations

from tests.conftest import insert_recovery, observe, query

HOUR = 60


def seed(client) -> str:
    return observe(client)["fingerprint"]


def test_twenty_failed_retries_from_two_reporters_is_a_skip(client):
    fp = seed(client)
    for i in range(20):
        insert_recovery(fingerprint=fp, action="retry", successful=False, minutes_ago=5 + i % 3 * 60,
                        reporter_hash=f"r{i % 2}")
    d = query(client)
    rec = d["recommendation"]
    assert rec is not None and rec["action"] == "skip"
    assert rec["based_on_attempts"] == 20 and rec["based_on_successes"] == 0
    assert rec["unique_reporters"] == 2 and 0 < rec["confidence"] < 1
    assert "retry 0/20" in rec["warning"] and "Do not spend another attempt" in rec["warning"]
    # the raw evidence is still listed, unchanged
    assert [a["action"] for a in d["recovery_actions"]] == ["retry"]


def test_several_failed_actions_add_up(client):
    fp = seed(client)
    for i in range(3):
        insert_recovery(fingerprint=fp, action="retry", successful=False, minutes_ago=10 + i, reporter_hash=f"a{i}")
    for i in range(3):
        insert_recovery(fingerprint=fp, action="backoff", successful=False, minutes_ago=20 + i, reporter_hash=f"b{i}")
    rec = query(client)["recommendation"]
    assert rec["action"] == "skip" and rec["based_on_attempts"] == 6
    assert "retry 0/3" in rec["warning"] and "backoff 0/3" in rec["warning"]


def test_one_recent_success_means_no_skip(client):
    fp = seed(client)
    for i in range(8):
        insert_recovery(fingerprint=fp, action="retry", successful=False, minutes_ago=10 + i, reporter_hash=f"r{i % 3}")
    insert_recovery(fingerprint=fp, action="retry", successful=True, minutes_ago=5, reporter_hash="r9")
    rec = query(client)["recommendation"]
    assert rec is None, "one thing worked recently; the network has no verdict either way yet"


def test_old_failures_do_not_skip_today(client):
    """A day of failures last week says nothing about now; the window is the
    same one decay uses."""
    fp = seed(client)
    for i in range(20):
        insert_recovery(fingerprint=fp, action="retry", successful=False, minutes_ago=3 * 24 * HOUR + i,
                        reporter_hash=f"r{i % 2}")
    assert query(client)["recommendation"] is None


def test_too_few_attempts_is_not_a_verdict(client):
    fp = seed(client)
    for i in range(4):
        insert_recovery(fingerprint=fp, action="retry", successful=False, minutes_ago=10 + i, reporter_hash=f"r{i}")
    assert query(client)["recommendation"] is None


def test_one_reporters_retry_storm_is_capped_and_discounted(client):
    """Twenty failures in one hour from one reporter count as five (the
    per-reporter hourly cap): exactly the floor, with the low-diversity
    discount, the same way a positive recommendation from one reporter is."""
    fp = seed(client)
    for i in range(20):
        insert_recovery(fingerprint=fp, action="retry", successful=False, minutes_ago=10, reporter_hash="storm")
    rec = query(client)["recommendation"]
    assert rec is not None and rec["action"] == "skip"
    assert rec["effective_attempts"] == 5 and rec["unique_reporters"] == 1
    diverse = None
    # the same volume from four reporters is worth more
    from tests.conftest import _truncate  # noqa: PLC0415
    _truncate()
    fp = seed(client)
    for i in range(20):
        insert_recovery(fingerprint=fp, action="retry", successful=False, minutes_ago=10, reporter_hash=f"r{i % 4}")
    diverse = query(client)["recommendation"]
    assert diverse["confidence"] > rec["confidence"]


def test_a_working_fix_outranks_futility(client):
    fp = seed(client)
    for i in range(10):
        insert_recovery(fingerprint=fp, action="retry", successful=False, minutes_ago=10 + i, reporter_hash=f"r{i % 3}")
    for i in range(6):
        insert_recovery(fingerprint=fp, action="refresh_schema", successful=True, minutes_ago=15 + i, reporter_hash=f"s{i % 3}")
    rec = query(client)["recommendation"]
    assert rec["action"] == "refresh_schema"


def test_skip_is_the_answer_over_mcp_too(client):
    fp = seed(client)
    for i in range(10):
        insert_recovery(fingerprint=fp, action="retry", successful=False, minutes_ago=10 + i, reporter_hash=f"r{i % 2}")
    import json

    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "check_tool_failure", "arguments": {
                "service": "github-mcp", "operation": "create_issue", "version": "2.8.1", "schema_hash": "a817ce",
                "error_type": "validation_error", "error_code": "422",
                "error_message": "Repository 555812 was not found"}}}
    r = client.post("/mcp", json=body, headers={"accept": "application/json, text/event-stream"})
    text = r.text
    payload = json.loads(text.split("data: ", 1)[1].split("\n", 1)[0]) if text.startswith("event:") else r.json()
    answer = json.loads(payload["result"]["content"][0]["text"])
    assert answer["recommendation"]["action"] == "skip"


def test_the_homepage_fixes_panel_does_not_list_skip(client):
    fp = seed(client)
    for i in range(10):
        insert_recovery(fingerprint=fp, action="retry", successful=False, minutes_ago=10 + i, reporter_hash=f"r{i % 2}")
    from app.core.cache import dashboard_cache

    dashboard_cache.clear()
    assert client.get("/v1/recovery-intelligence").json() == []
