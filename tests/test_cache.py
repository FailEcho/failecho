"""The dashboard read cache.

Three endpoints return network-wide numbers identical for every caller, and a
launch-day crowd asks for them constantly. Memoising them for a few seconds is
the difference between absorbing a front-page spike and queueing on it.

The rest of the suite runs with the cache disabled (FIN_DASHBOARD_CACHE_SECONDS=0)
because those tests assert on writes they just made. This file is where the
cache itself is exercised.
"""

from __future__ import annotations

import time

import pytest

from app.core.cache import TTLCache, dashboard_cache
from tests.conftest import observe


def test_disabled_cache_never_serves_anything():
    """A zero TTL must behave as if the cache were not there at all."""
    cache = TTLCache(0)
    cache.set("k", "value")
    assert cache.get("k") is None
    assert cache.enabled is False


def test_hit_then_expiry():
    cache = TTLCache(0.15)
    cache.set("k", "first")
    assert cache.get("k") == "first"
    assert cache.hits == 1

    time.sleep(0.2)
    assert cache.get("k") is None, "entry must expire"
    assert cache.misses == 1


def test_keys_do_not_collide():
    cache = TTLCache(5)
    cache.set("services:None:100", ["a"])
    cache.set("services:github-mcp:100", ["b"])
    assert cache.get("services:None:100") == ["a"]
    assert cache.get("services:github-mcp:100") == ["b"]


def test_clear_drops_everything():
    cache = TTLCache(5)
    cache.set("k", 1)
    cache.clear()
    assert cache.get("k") is None


@pytest.fixture()
def warm_cache():
    """Turn the real cache on for one test, then put it back."""
    original = dashboard_cache.ttl
    dashboard_cache.ttl = 5.0
    dashboard_cache.clear()
    yield dashboard_cache
    dashboard_cache.ttl = original
    dashboard_cache.clear()


def test_dashboard_endpoints_are_served_from_cache(client, warm_cache):
    """The second identical request must not touch the database again."""
    observe(client)

    first = client.get("/v1/stats").json()
    assert first["real_observations_24h"] == 1

    # A write lands, but within the TTL the cached snapshot is still served.
    observe(client)
    second = client.get("/v1/stats").json()
    assert second == first, "served from cache, so identical"

    warm_cache.clear()
    third = client.get("/v1/stats").json()
    assert third["real_observations_24h"] == 2, "fresh read sees both writes"


def test_each_endpoint_caches_independently(client, warm_cache):
    observe(client)
    services = client.get("/v1/services").json()
    stats = client.get("/v1/stats").json()
    recovery = client.get("/v1/recovery-intelligence").json()

    assert services and stats and recovery == []
    # Distinct keys, so no endpoint can serve another's payload.
    assert client.get("/v1/services").json() == services
    assert client.get("/v1/stats").json() == stats


def test_query_parameters_are_part_of_the_key(client, warm_cache):
    """?service=x must never be served the unfiltered answer."""
    for _ in range(12):
        observe(client)
    observe(client, service="other-api", outcome="success", error_type=None,
            error_code=None, error_message=None)

    everything = client.get("/v1/services").json()
    filtered = client.get("/v1/services", params={"service": "other-api"}).json()

    assert len(everything) == 2
    assert len(filtered) == 1
    assert filtered[0]["service"] == "other-api"


def test_write_paths_are_never_cached(client, warm_cache):
    """Each query answers one caller about one failure, always freshly."""
    from tests.conftest import query

    first = query(client)
    observe(client)          # same scope, same fingerprint
    second = query(client)

    assert first["known"] is False
    assert second["known"] is True, "/v1/query must reflect the write immediately"
    assert second["observations"]["total"] == 1
