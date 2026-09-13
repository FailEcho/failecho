"""The demo seeder must produce a legible, clearly-labelled network."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def seeded(client):
    """Run scripts/seed_demo.py against the test database, as a real CLI would."""
    env = dict(os.environ)
    result = subprocess.run(
        [sys.executable, "scripts/seed_demo.py", "--reset"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    return client


def test_seed_produces_visible_status_data(seeded):
    rows = seeded.get("/v1/services").json()
    by_service = {r["service"]: r for r in rows}

    assert {"github", "search-api", "stripe", "example-agent-tool"} <= set(
        by_service
    )
    assert by_service["github"]["status"] == "MAJOR"
    assert by_service["search-api"]["status"] == "DEGRADED"
    assert by_service["stripe"]["status"] == "HEALTHY"
    assert rows[0]["status"] == "MAJOR", "worst service must sort first"


def test_seed_is_labelled_synthetic(seeded):
    stats = seeded.get("/v1/stats").json()
    assert stats["demo_data"] is True
    assert stats["synthetic_observations"] == stats["observations_total"] > 0
    assert stats["fingerprints_total"] >= 4
    assert stats["recovery_outcomes_total"] > 0


def test_seed_gives_a_recommendable_failure(seeded):
    intel = seeded.post(
        "/v1/query",
        json={
            "service": "github-mcp",
            "operation": "create_issue",
            "version": "2.8.1",
            "schema_hash": "a817ce",
            "error_type": "validation_error",
            "error_code": "422",
            "error_message": "Repository 424242 was not found",
        },
    ).json()

    assert intel["known"] is True
    assert intel["status"] == "MAJOR"
    assert intel["observations"]["unique_reporters"] > 1
    assert intel["recommendation"]["action"] == "refresh_schema"
    assert 0.8 < intel["recommendation"]["confidence"] < 1.0

    actions = {a["action"]: a for a in intel["recovery_actions"]}
    assert actions["retry"]["success_rate"] < actions["refresh_schema"]["success_rate"]


def test_seed_withholds_thin_evidence(seeded):
    """example-agent-tool has only 3 recovery attempts -> no recommendation."""
    intel = seeded.post(
        "/v1/query",
        json={
            "service": "example-agent-tool",
            "operation": "run",
            "version": "0.9.0",
            "schema_hash": "0b91fe",
            "error_type": "tool_error",
            "error_code": "500",
            "error_message": "Internal worker crashed (pid 55555)",
        },
    ).json()
    assert intel["known"] is True
    assert intel["recommendation"] is None


def test_purge_removes_synthetic_rows(seeded):
    result = subprocess.run(
        [sys.executable, "scripts/seed_demo.py", "--purge"],
        cwd=ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    stats = seeded.get("/v1/stats").json()
    assert stats["observations_total"] == 0
    assert stats["demo_data"] is False


def test_the_live_demo_refuses_to_write_into_a_real_network():
    """It defaults to 127.0.0.1:8000, which is also where a deployed FailEcho
    listens. Run it on the server and demo telemetry lands in production --
    labelled and excluded from adoption, but there, and the site starts
    showing its demonstration-data banner. This happened once."""
    import importlib.util
    from pathlib import Path

    demo = Path(__file__).resolve().parents[1] / "examples" / "live_agent" / "run_demo.py"
    spec = importlib.util.spec_from_file_location("run_demo", demo)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_demo"] = module
    spec.loader.exec_module(module)

    calls = {}

    def fake_stats(url, timeout=5):
        calls["url"] = url
        return {"real_observations_total": 0, "first_party_observations": 15}

    module.http_json = fake_stats

    with pytest.raises(SystemExit) as raised:
        module.refuse_if_this_is_a_real_network("https://failecho.com", force=False)
    assert "Refusing to run" in str(raised.value)
    assert "--force" in str(raised.value), "say how to override it"

    # An empty network is what the demo is for.
    module.http_json = lambda url, timeout=5: {
        "real_observations_total": 0, "first_party_observations": 0
    }
    module.refuse_if_this_is_a_real_network("http://127.0.0.1:8100", force=False)

    # And --force still means force.
    module.http_json = fake_stats
    module.refuse_if_this_is_a_real_network("https://failecho.com", force=True)
