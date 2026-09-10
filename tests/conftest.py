"""Test fixtures.

The database URL is pinned to a throwaway file *before* the app is imported,
so tests never touch ./data/failure_network.db.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

TEST_DB = Path(tempfile.mkdtemp(prefix="fin-tests-")) / "test.db"

os.environ["FIN_DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB}"
os.environ["FIN_REPORTER_SALT"] = "test-salt"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.ratelimit import write_limiter  # noqa: E402
from app.main import app  # noqa: E402

TABLES = (
    "observations",
    "recovery_outcomes",
    "fingerprints",
    "hourly_stats",
    "hourly_recovery_stats",
    "daily_counters",
)


@pytest.fixture(scope="session")
def db_path() -> Path:
    return TEST_DB


@pytest.fixture()
def client():
    """A TestClient with a clean database and a fresh rate-limit budget."""
    with TestClient(app) as test_client:
        _truncate()
        write_limiter.reset()
        yield test_client
        _truncate()
        write_limiter.reset()


def _truncate() -> None:
    if not TEST_DB.exists():
        return
    connection = sqlite3.connect(TEST_DB)
    try:
        for table in TABLES:
            connection.execute(f"DELETE FROM {table}")
        connection.commit()
    finally:
        connection.close()


@pytest.fixture()
def rows():
    """Read raw rows straight from SQLite, bypassing the ORM.

    Used by the privacy tests: the point is to inspect exactly what landed on
    disk, not what the application says it stored.
    """

    def _read(table: str) -> list[dict]:
        connection = sqlite3.connect(TEST_DB)
        connection.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in connection.execute(f"SELECT * FROM {table}")]
        finally:
            connection.close()

    return _read


def observe(client, **overrides) -> dict:
    """POST /v1/observe with sensible defaults."""
    payload = {
        "service": "github-mcp",
        "operation": "create_issue",
        "version": "2.8.1",
        "schema_hash": "a817ce",
        "outcome": "failure",
        "error_type": "validation_error",
        "error_code": "422",
        "error_message": "Repository 918272 was not found",
    }
    headers = overrides.pop("headers", None)
    payload.update(overrides)
    response = client.post("/v1/observe", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def query(client, **overrides) -> dict:
    payload = {
        "service": "github-mcp",
        "operation": "create_issue",
        "version": "2.8.1",
        "schema_hash": "a817ce",
        "error_type": "validation_error",
        "error_code": "422",
        "error_message": "Repository 555812 was not found",
    }
    payload.update(overrides)
    response = client.post("/v1/query", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Direct-to-SQLite helpers.
#
# Retention tests need rows with timestamps hours in the past. Writing them
# through the API is impossible (the server stamps "now") and writing them
# through the ORM would need a second event loop, so they go in with plain
# sqlite3 -- the same format SQLAlchemy uses.
# ---------------------------------------------------------------------------

SQLITE_TIME = "%Y-%m-%d %H:%M:%S.%f"


def _connect():
    return sqlite3.connect(TEST_DB)


def insert_observation(
    *,
    minutes_ago: int = 0,
    service: str = "github-mcp",
    operation: str = "create_issue",
    version: str | None = "2.8.1",
    schema_hash: str | None = "a817ce",
    outcome: str = "failure",
    fingerprint: str | None = None,
    error_type: str | None = "validation_error",
    error_code: str | None = "422",
    normalized_error: str | None = "Repository <N> was not found",
    latency_ms: int | None = 100,
    reporter_hash: str | None = None,
    source: str = "agent",
) -> None:
    created = (datetime.utcnow() - timedelta(minutes=minutes_ago)).strftime(SQLITE_TIME)
    connection = _connect()
    try:
        connection.execute(
            "INSERT INTO observations (created_at, service, operation, version,"
            " schema_hash, outcome, fingerprint, error_type, error_code,"
            " normalized_error, latency_ms, reporter_hash, source)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                created,
                service,
                operation,
                version,
                schema_hash,
                outcome,
                fingerprint,
                error_type if outcome == "failure" else None,
                error_code if outcome == "failure" else None,
                normalized_error if outcome == "failure" else None,
                latency_ms,
                reporter_hash,
                source,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def insert_recovery(
    *,
    fingerprint: str,
    action: str = "refresh_schema",
    successful: bool = True,
    minutes_ago: int = 0,
    reporter_hash: str | None = None,
    source: str = "agent",
) -> None:
    created = (datetime.utcnow() - timedelta(minutes=minutes_ago)).strftime(SQLITE_TIME)
    connection = _connect()
    try:
        connection.execute(
            "INSERT INTO recovery_outcomes (created_at, fingerprint, action,"
            " successful, reporter_hash, source) VALUES (?,?,?,?,?,?)",
            (created, fingerprint, action, int(successful), reporter_hash, source),
        )
        connection.commit()
    finally:
        connection.close()


def insert_fingerprint(
    fingerprint: str,
    *,
    service: str = "github-mcp",
    operation: str = "create_issue",
    version: str | None = "2.8.1",
    schema_hash: str | None = "a817ce",
    error_type: str | None = "validation_error",
    error_code: str | None = "422",
    normalized_error: str | None = "Repository <N> was not found",
    observation_count: int = 1,
) -> None:
    now = datetime.utcnow().strftime(SQLITE_TIME)
    connection = _connect()
    try:
        connection.execute(
            "INSERT OR REPLACE INTO fingerprints (fingerprint, service, operation,"
            " version, schema_hash, error_type, error_code, normalized_error,"
            " first_seen, last_seen, observation_count) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                fingerprint,
                service,
                operation,
                version,
                schema_hash,
                error_type,
                error_code,
                normalized_error,
                now,
                now,
                observation_count,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def run_prune(*args: str) -> subprocess.CompletedProcess:
    """Run scripts/prune.py against the test database, as cron would."""
    result = subprocess.run(
        [sys.executable, "scripts/prune.py", *args],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    return result


def mcp_call(client, tool: str, arguments: dict) -> dict:
    """Invoke one MCP tool over the mounted Streamable HTTP endpoint."""
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
        headers={"accept": "application/json, text/event-stream"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "error" not in body, body
    return body["result"]["structuredContent"]
