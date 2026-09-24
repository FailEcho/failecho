"""The lab-to-production mirror: what crosses, what never does, and that it
is inert and labelled. No network: every send goes to a recording fake."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3

import pytest

import failecho_mirror as M

SCHEMA = """
create table observations (id integer primary key, created_at datetime not null, service varchar not null,
  operation varchar not null, version varchar, schema_hash varchar, outcome varchar not null, fingerprint varchar,
  error_type varchar, error_code varchar, normalized_error varchar, latency_ms integer, mutates boolean,
  reporter_hash varchar, source varchar not null);
create table recovery_outcomes (id integer primary key, created_at datetime not null, fingerprint varchar not null,
  action varchar not null, successful boolean not null, reporter_hash varchar, source varchar not null);
create table fingerprints (fingerprint varchar primary key, service varchar not null, operation varchar not null,
  version varchar, schema_hash varchar, error_type varchar, error_code varchar, normalized_error varchar,
  first_seen datetime not null, last_seen datetime not null, observation_count integer not null);
"""


def _now(minutes_ago=0):
    return (dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(minutes=minutes_ago)).isoformat(" ")


@pytest.fixture
def lab(tmp_path):
    path = tmp_path / "lab.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    obs = [
        ("api.groq.com", "chat.completions", "failure", "rate_limit", "429", None, 412, "aaaa1111bbbb2222cccc", "agent"),
        ("httpbingo.org", "always_broken", "failure", "server_error", "503", None, 30, "aaaa1111bbbb2222cccc", "agent"),
        ("api.github.com", "github_repo", "success", None, None, None, 120, "dddd3333eeee4444ffff", "agent"),
        ("api.github.com", "github_repo", "failure", "rate_limit", "403", None, 90, None, "demo_agent"),
        ("pypi.org", "pypi_latest", "failure", "not_found", "404", "some <id> text", 80, "dddd3333eeee4444ffff", "agent"),
    ]
    for i, (svc, op, outcome, et, code, err, lat, rh, src) in enumerate(obs, start=1):
        db.execute("insert into observations (id, created_at, service, operation, outcome, error_type, error_code, "
                   "normalized_error, latency_ms, reporter_hash, source) values (?,?,?,?,?,?,?,?,?,?,?)",
                   (i, _now(30 - i), svc, op, outcome, et, code, err, lat, rh, src))
    for fp, svc in (("fp-groq", "api.groq.com"), ("fp-bingo", "httpbingo.org")):
        db.execute("insert into fingerprints values (?,?,?,?,?,?,?,?,?,?,?)",
                   (fp, svc, "op", None, None, "rate_limit", "429", None, _now(60), _now(1), 3))
    for i, (fp, action, ok, src) in enumerate((("fp-groq", "switch_model", 1, "agent"), ("fp-bingo", "retry", 0, "agent"),
                                               ("fp-groq", "retry", 0, "demo_agent")), start=1):
        db.execute("insert into recovery_outcomes values (?,?,?,?,?,?,?)",
                   (i, _now(20 - i), fp, action, ok, "aaaa1111bbbb2222cccc", src))
    db.commit()
    db.close()
    return str(path)


class Recorder:
    def __init__(self, statuses=None):
        self.calls, self.statuses = [], list(statuses or [])

    def __call__(self, url, body, headers):
        self.calls.append((url, body, headers))
        return self.statuses.pop(0) if self.statuses else 200


def _run(lab, tmp_path, post, **kw):
    return M.run(endpoint="http://target.invalid", token="op-token", db_path=lab,
                 state_path=str(tmp_path / "cursor.json"), post=post, sleep=lambda s: None, **kw)


def test_only_real_services_from_our_own_agents_cross(lab, tmp_path):
    post = Recorder()
    result = _run(lab, tmp_path, post)
    sent = [(url.rsplit("/", 1)[1], body) for url, body, _ in post.calls]
    assert sent == [
        ("observe", {"service": "api.groq.com", "operation": "chat.completions", "outcome": "failure",
                     "error_type": "rate_limit", "error_code": "429", "latency_ms": 412}),
        ("observe", {"service": "api.github.com", "operation": "github_repo", "outcome": "success", "latency_ms": 120}),
        ("outcome", {"fingerprint": "fp-groq", "action": "switch_model", "successful": True}),
    ]
    # httpbingo (a test endpoint), demo traffic, and a row carrying error text never cross
    assert result == {"sent_observations": 2, "sent_outcomes": 1, "skipped": 5, "stopped": None}


def test_every_write_is_labelled_first_party_with_a_pseudonymous_reporter(lab, tmp_path):
    post = Recorder()
    _run(lab, tmp_path, post)
    for _, _, headers in post.calls:
        assert headers["X-FailEcho-Operator"] == "op-token"
    assert {h["X-Reporter-ID"] for _, _, h in post.calls} == {"lab-aaaa1111bbbb2222", "lab-dddd3333eeee4444"}


def test_a_second_run_sends_nothing_twice(lab, tmp_path):
    _run(lab, tmp_path, Recorder())
    again = Recorder()
    assert _run(lab, tmp_path, again)["sent_observations"] == 0 and again.calls == []


def test_a_refusal_stops_the_pass_and_keeps_the_row(lab, tmp_path):
    first = Recorder(statuses=[200, 429])
    result = _run(lab, tmp_path, first)
    assert result["stopped"] == "/v1/observe answered 429" and result["sent_observations"] == 1
    retry = Recorder()
    _run(lab, tmp_path, retry)
    assert retry.calls[0][1]["service"] == "api.github.com", "the refused row is sent next time, not skipped"


def test_the_row_budget_is_respected(lab, tmp_path):
    post = Recorder()
    assert _run(lab, tmp_path, post, max_rows=1)["sent_observations"] == 1 and len(post.calls) == 1


def test_the_lab_database_is_opened_read_only(lab):
    db = M.connect(lab)
    with pytest.raises(sqlite3.OperationalError):
        db.execute("delete from observations")


def test_backfill_starts_at_the_window(lab, tmp_path):
    post = Recorder()
    # all fixture rows are under an hour old; a tiny window leaves them out
    assert _run(lab, tmp_path, post, backfill_hours=0.001)["sent_observations"] == 0


def test_inert_unless_enabled_and_never_unlabelled(monkeypatch, capsys):
    for name in ("FAILECHO_MIRROR_ENABLED", "FIN_FIRST_PARTY_TOKEN", "FAILECHO_OPERATOR_TOKEN", "FAILECHO_MIRROR_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(M, "run", lambda **kw: pytest.fail("ran while not enabled or unlabelled"))
    assert M.main() == 0 and "not enabled" in capsys.readouterr().out
    monkeypatch.setenv("FAILECHO_MIRROR_ENABLED", "1")
    monkeypatch.setenv("FAILECHO_MIRROR_ENDPOINT", "http://127.0.0.1:1")
    assert M.main() == 2, "no operator token: refuse rather than report as an outside agent"
    monkeypatch.setenv("FIN_FIRST_PARTY_TOKEN", "tok")
    monkeypatch.setenv("FAILECHO_MIRROR_ENDPOINT", "https://lab.failecho.com")
    assert M.main() == 2, "the lab is the source, never the target"


def test_httpbingo_and_other_test_endpoints_are_not_on_the_list():
    assert not {"httpbingo.org", "httpbin.org", "packages", "localhost"} & M.ALLOWED_SERVICES
    assert not any(s.endswith("failecho.com") for s in M.ALLOWED_SERVICES)
