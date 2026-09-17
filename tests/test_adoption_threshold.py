"""One scanner must not be able to write the adoption number.

On 2026-09-16 a fuzzer sent eleven junk rows to /v1/observe under eight
fresh reporter ids, and the front page said "11 independent observations".
Every `agent` row is still stored and still evidence; adoption is counted
only from reporters that look like agents: enough observations, across more
than one service, over more than a moment.
"""

from __future__ import annotations

import pytest

from app.core.config import SOURCE_AGENT, SOURCE_AGENT_SPARSE, settings
from tests.conftest import insert_observation, run_prune


def stats(client) -> dict:
    from app.core.cache import dashboard_cache

    dashboard_cache.clear()
    return client.get("/v1/stats").json()


def established_agent(reporter: str, *, start_minutes_ago: int = 60) -> None:
    """Five observations, two services, spread over twenty minutes."""
    for i, service in enumerate(("api.github.com", "api.github.com", "pypi.org", "pypi.org", "api.github.com")):
        insert_observation(service=service, operation="op", reporter_hash=reporter,
                           minutes_ago=start_minutes_ago - i * 5, source=SOURCE_AGENT)


def test_the_fuzzer_pattern_counts_as_nothing(client):
    """Eleven rows, eight reporter ids, one or two rows each, all inside a
    minute: exactly what happened. Stored, and adoption stays at zero."""
    for i in range(11):
        insert_observation(service="tmp/pp-fuzz", operation="/tmp/pp-fuzz", outcome="success",
                           reporter_hash=f"fuzz-{i % 8}", minutes_ago=1, source=SOURCE_AGENT)
    s = stats(client)
    assert s["real_observations_total"] == 0 and s["real_observations_24h"] == 0
    assert s["real_reporters_24h"] == 0 and s["real_active_failures"] == 0
    assert s["sparse_observations"] == 11
    assert s["observations_total"] == 11, "the rows are kept; only the label the front page counts changes"


def test_an_agent_that_looks_like_one_counts(client):
    established_agent("agent-A")
    s = stats(client)
    assert s["real_observations_total"] == 5 and s["real_observations_24h"] == 5
    assert s["real_reporters_24h"] == 1 and s["sparse_observations"] == 0


def test_each_leg_of_the_threshold_is_required(client):
    # enough rows, all within a minute: a burst, not an agent
    for i in range(6):
        insert_observation(service=("api.github.com", "pypi.org")[i % 2], reporter_hash="burst", minutes_ago=1)
    # spread out, four rows: not yet
    for i in range(4):
        insert_observation(service=("api.github.com", "pypi.org")[i % 2], reporter_hash="thin", minutes_ago=60 - i * 10)
    s = stats(client)
    assert s["real_observations_total"] == 0 and s["sparse_observations"] == 10
    # one service is enough: an agent whose whole job is GitHub is an agent
    for i in range(6):
        insert_observation(service="api.github.com", reporter_hash="one-service", minutes_ago=60 - i * 5)
    s = stats(client)
    assert s["real_observations_total"] == 6 and s["sparse_observations"] == 10


def test_anonymous_rows_never_establish_adoption(client):
    for i in range(8):
        insert_observation(service=("api.github.com", "pypi.org")[i % 2], reporter_hash=None, minutes_ago=60 - i * 5)
    s = stats(client)
    assert s["real_observations_total"] == 0 and s["sparse_observations"] == 8


def test_the_threshold_is_stated_in_the_response(client):
    s = stats(client)
    assert s["adoption_threshold"] == {"min_observations": settings.adoption_min_observations,
                                       "min_services": settings.adoption_min_services,
                                       "min_span_seconds": settings.adoption_min_span_seconds}
    assert s["adoption_threshold"]["min_observations"] >= 5 and s["adoption_threshold"]["min_span_seconds"] >= 600


def test_first_party_and_demo_are_untouched_by_the_threshold(client):
    insert_observation(reporter_hash="ours", source="first_party")
    insert_observation(reporter_hash="demo", source="demo_agent")
    s = stats(client)
    assert s["first_party_observations"] == 1 and s["demo_agent_observations"] == 1
    assert s["sparse_observations"] == 0


def test_retention_archives_thin_reporters_apart_from_established_ones(client, rows):
    """Archives lose reporter identity, so the judgement is made when the raw
    rows are folded: established reporters archive as `agent`, the rest as
    `agent_sparse`, and the counters still add up after the prune."""
    hours = settings.retention_hours
    established_agent("agent-A", start_minutes_ago=hours * 60 + 120)
    for i in range(3):
        insert_observation(service="tmp/pp-fuzz", reporter_hash=f"fuzz-{i}", minutes_ago=hours * 60 + 60)
    before = stats(client)
    assert before["real_observations_total"] == 5 and before["sparse_observations"] == 3
    run_prune()
    archived = {r["source"]: r for r in rows("hourly_stats")}
    assert set(archived) == {SOURCE_AGENT, SOURCE_AGENT_SPARSE}
    assert sum(r["success_count"] + r["failure_count"] for r in rows("hourly_stats") if r["source"] == SOURCE_AGENT_SPARSE) == 3
    after = stats(client)
    assert after["real_observations_total"] == 5 and after["sparse_observations"] == 3
    assert after["observations_total"] == 8


def test_query_evidence_still_sees_thin_reporters(client):
    """The threshold is about the front page, not the intelligence: a failure
    reported by a brand-new reporter is still a failure another agent can be
    told about."""
    from tests.conftest import observe

    observe(client, service="api.github.com", operation="create_issue", headers={"X-Reporter-ID": "brand-new"})
    assert stats(client)["sparse_observations"] == 1
    answer = client.post("/v1/query", json={"service": "api.github.com", "operation": "create_issue",
                                            "version": "2.8.1", "schema_hash": "a817ce",
                                            "error_type": "validation_error", "error_code": "422",
                                            "error_message": "Repository 918272 was not found"}).json()
    assert answer["observations"]["total"] == 1
