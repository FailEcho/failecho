"""Privacy is a product feature, so it gets tests that read the disk directly."""

from __future__ import annotations

import json

from tests.conftest import observe


def test_reporter_id_is_hashed_never_stored_raw(client, rows):
    observe(client, headers={"X-Reporter-ID": "agent-alpha-secret-id"})
    stored = rows("observations")
    assert len(stored) == 1
    reporter_hash = stored[0]["reporter_hash"]
    assert reporter_hash
    assert reporter_hash != "agent-alpha-secret-id"
    assert len(reporter_hash) == 32
    dump = json.dumps(stored)
    assert "agent-alpha-secret-id" not in dump


def test_same_reporter_hashes_consistently(client, rows):
    observe(client, headers={"X-Reporter-ID": "agent-alpha"})
    observe(client, headers={"X-Reporter-ID": "agent-alpha"})
    observe(client, headers={"X-Reporter-ID": "agent-beta"})
    hashes = {row["reporter_hash"] for row in rows("observations")}
    assert len(hashes) == 2, "stable hashing enables unique-reporter counts"


def test_anonymous_is_supported(client, rows):
    observe(client)
    assert rows("observations")[0]["reporter_hash"] is None


def test_raw_error_message_is_not_persisted(client, rows):
    observe(
        client,
        error_message="Repository 918272 was not found for user carol@acme.com",
    )
    dump = json.dumps(rows("observations") + rows("fingerprints"))
    assert "918272" not in dump
    assert "carol@acme.com" not in dump
    assert "Repository <N> was not found for user <EMAIL>" in dump


def test_secrets_in_error_messages_are_redacted_on_disk(client, rows):
    observe(client, error_message="auth failed with token=sk_live_9aBc12345678xyz")
    dump = json.dumps(rows("observations") + rows("fingerprints"))
    assert "sk_live_9aBc12345678xyz" not in dump
    assert "<REDACTED>" in dump


def test_unknown_fields_are_dropped_before_storage(client, rows):
    """An agent that leaks a prompt into the payload cannot persist it here."""
    response = client.post(
        "/v1/observe",
        json={
            "service": "github-mcp",
            "operation": "create_issue",
            "outcome": "failure",
            "error_type": "validation_error",
            "prompt": "SYSTEM: you are a helpful assistant with key sk-abc",
            "tool_arguments": {"repo": "acme/private-repo"},
            "response_body": {"customer_email": "carol@acme.com"},
            "authorization": "Bearer supersecret",
        },
    )
    assert response.status_code == 200
    dump = json.dumps(rows("observations") + rows("fingerprints"))
    for leaked in (
        "helpful assistant",
        "acme/private-repo",
        "carol@acme.com",
        "supersecret",
    ):
        assert leaked not in dump


def test_query_endpoint_stores_nothing(client, rows):
    client.post(
        "/v1/query",
        json={
            "service": "github-mcp",
            "operation": "create_issue",
            "error_type": "validation_error",
            "error_message": "Repository 918272 was not found",
        },
    )
    assert rows("observations") == []
    assert rows("fingerprints") == []


def test_observation_columns_are_the_whole_contract(client, rows):
    """If a column is ever added, this test forces a deliberate decision."""
    observe(client)
    assert set(rows("observations")[0]) == {
        "id",
        "created_at",
        "service",
        "operation",
        "version",
        "schema_hash",
        "outcome",
        "fingerprint",
        "error_type",
        "error_code",
        "normalized_error",
        "latency_ms",
        "reporter_hash",
        "source",
        # A boolean the reporter declares: does this operation change state?
        # Exists so a write that has never once failed can be reported as an
        # unverified success rather than a success. Carries no content.
        # Added 2026-09-16, deliberately.
        "mutates",
    }


def test_the_node_relay_logs_lengths_not_protocol_content():
    """A malformed message is still a protocol message. The first 120
    characters of one are whatever happened to be at the front of it -- tool
    arguments, most likely -- and this process has no business copying that
    into the host's logs."""
    from pathlib import Path

    relay = (Path(__file__).resolve().parents[1]
             / "npm-relay" / "bin" / "failecho-mcp.js").read_text()
    assert ".slice(0, 120)" not in relay, "protocol fragments are being logged again"
    assert "bytes)`);" in relay, "the length is what should be logged"
