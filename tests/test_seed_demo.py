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

    assert {"github-mcp", "search-api", "stripe-mcp", "example-agent-tool"} <= set(
        by_service
    )
    assert by_service["github-mcp"]["status"] == "MAJOR"
    assert by_service["search-api"]["status"] == "DEGRADED"
    assert by_service["stripe-mcp"]["status"] == "HEALTHY"
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
