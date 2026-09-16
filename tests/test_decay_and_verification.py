"""Two signals from one r/AI_Agents comment.

The decaying fix: a (fingerprint, action) that went 5/5 last month and 0/5
this week is one root cause that changed under a stable error shape. The
early warning is the drop, not the failure rate.

The unverified success: a write-shaped operation with many successes and no
failure ever is either flawless or unobservable, and from outside those look
the same. So it is reported as unverified rather than as success.
"""

from __future__ import annotations

from tests.conftest import insert_observation as _insert_observation
from tests.conftest import insert_recovery, observe, query

DAY = 24 * 60


def insert_observation(**kw):
    """Rows written straight to SQLite skip the edge, where the service name
    is canonicalised. The API stores `github-mcp` as `github`, so the fixture
    must too or the query never finds them."""
    kw.setdefault("service", "github")
    return _insert_observation(**kw)


def _seed_failure(client) -> str:
    return observe(client)["fingerprint"]


# -- decaying fix ------------------------------------------------------------


def test_a_fix_that_stopped_working_is_flagged_not_hidden(client):
    fp = _seed_failure(client)
    # last month: it worked, from three reporters
    for i in range(6):
        insert_recovery(fingerprint=fp, action="refresh_schema", successful=True,
                        minutes_ago=30 * DAY - i, reporter_hash=f"r{i % 3}")
    # this week: it does not
    for i in range(4):
        insert_recovery(fingerprint=fp, action="refresh_schema", successful=False,
                        minutes_ago=60 + i, reporter_hash=f"r{i % 3}")

    d = query(client)
    row = next(a for a in d["recovery_actions"] if a["action"] == "refresh_schema")
    assert row["attempts"] == 10 and row["successes"] == 6
    assert row["recent_attempts"] == 4 and row["recent_success_rate"] == 0.0
    assert row["decaying"] is True

    rec = d["recommendation"]
    assert rec is not None and rec["action"] == "refresh_schema", (
        "it is still the best evidence on record; decay flags, it does not re-rank"
    )
    assert rec["decaying"] is True
    assert "100%" in rec["warning"] and "0%" in rec["warning"]
    assert "root cause may have changed" in rec["warning"]


def test_two_recent_failures_are_not_yet_decay(client):
    """A fix does not become suspect on two bad tries. Three is the floor."""
    fp = _seed_failure(client)
    for i in range(6):
        insert_recovery(fingerprint=fp, action="refresh_schema", successful=True,
                        minutes_ago=30 * DAY - i, reporter_hash=f"r{i % 3}")
    for i in range(2):
        insert_recovery(fingerprint=fp, action="refresh_schema", successful=False,
                        minutes_ago=60 + i, reporter_hash="r0")
    row = next(a for a in query(client)["recovery_actions"] if a["action"] == "refresh_schema")
    assert row["recent_attempts"] == 2 and row["decaying"] is False


def test_a_fix_that_never_worked_cannot_decay(client):
    """Decay is a drop from a fix. Something that was always 0/5 is not
    decaying; it is just bad, and the confidence already says so."""
    fp = _seed_failure(client)
    for i in range(6):
        insert_recovery(fingerprint=fp, action="retry", successful=False,
                        minutes_ago=30 * DAY - i, reporter_hash=f"r{i % 3}")
    for i in range(4):
        insert_recovery(fingerprint=fp, action="retry", successful=False,
                        minutes_ago=60 + i, reporter_hash="r0")
    row = next(a for a in query(client)["recovery_actions"] if a["action"] == "retry")
    assert row["decaying"] is False


def test_a_fix_that_still_works_is_not_flagged(client):
    fp = _seed_failure(client)
    for i in range(6):
        insert_recovery(fingerprint=fp, action="refresh_schema", successful=True,
                        minutes_ago=30 * DAY - i, reporter_hash=f"r{i % 3}")
    for i in range(4):
        insert_recovery(fingerprint=fp, action="refresh_schema", successful=True,
                        minutes_ago=60 + i, reporter_hash=f"r{i % 3}")
    d = query(client)
    row = next(a for a in d["recovery_actions"] if a["action"] == "refresh_schema")
    assert row["decaying"] is False and row["recent_success_rate"] == 1.0
    assert d["recommendation"]["decaying"] is False
    assert d["recommendation"]["warning"] is None


# -- unverified success ------------------------------------------------------


def test_a_write_that_never_fails_is_unverified(client):
    """20 create_issue successes, zero failures ever. From outside that is a
    backend that either always works or never does the write. Say so."""
    for _ in range(20):
        insert_observation(outcome="success", error_type=None, error_code=None,
                           normalized_error=None, minutes_ago=5)
    d = query(client)
    ev = d["success_evidence"]
    assert ev["successes_total"] == 20 and ev["failures_total"] == 0
    assert ev["write_like"] is True
    assert ev["verified"] is False


def test_one_failure_ever_verifies_the_writes(client):
    """The moment a write has been seen to fail, its successes are
    observable: the backend is at least capable of saying no."""
    for _ in range(20):
        insert_observation(outcome="success", error_type=None, error_code=None,
                           normalized_error=None, minutes_ago=5)
    observe(client)  # one real failure
    assert query(client)["success_evidence"]["verified"] is True


def test_reads_are_never_flagged(client):
    """A lookup that has never failed is merely a lookup that has never
    failed. Only write-shaped names carry the suspicion."""
    for _ in range(40):
        insert_observation(operation="get_issue", outcome="success", error_type=None,
                           error_code=None, normalized_error=None, minutes_ago=5)
    d = query(client, operation="get_issue")
    assert d["success_evidence"]["write_like"] is False
    assert d["success_evidence"]["verified"] is True


def test_too_few_calls_to_judge_stays_verified(client):
    for _ in range(5):
        insert_observation(outcome="success", error_type=None, error_code=None,
                           normalized_error=None, minutes_ago=5)
    assert query(client)["success_evidence"]["verified"] is True


def test_nothing_observed_means_no_verdict(client):
    assert query(client, service="never-seen", operation="create_thing")["success_evidence"] is None


def test_write_like_is_a_name_heuristic_and_says_so(client):
    from app.core.intelligence import write_like

    assert write_like("create_issue") and write_like("deleteRepo") and write_like("put")
    assert not write_like("get_issue") and not write_like("list_repos") and not write_like("search")
    # the schema field description admits what it is
    from app.schemas.query import SuccessEvidence

    assert "heuristic" in SuccessEvidence.model_fields["write_like"].description


# -- the caller's declaration beats the name --------------------------------


def test_a_declared_write_with_a_read_shaped_name_is_still_a_write(client):
    """GraphQL: everything is POST and the operation is called `query`. Only
    the caller knows it mutates. When they say so, the name heuristic is not
    consulted at all."""
    for _ in range(20):
        observe(client, operation="query", outcome="success", error_type=None,
                error_code=None, error_message=None, mutates=True)
    ev = query(client, operation="query")["success_evidence"]
    assert ev["write_like"] is True and ev["write_source"] == "declared"
    assert ev["verified"] is False


def test_a_declared_read_with_a_write_shaped_name_is_a_read(client):
    """`create_issue` on a service where that call is actually a dry run.
    Declared reads are never flagged, whatever they are called."""
    for _ in range(20):
        observe(client, outcome="success", error_type=None, error_code=None,
                error_message=None, mutates=False)
    ev = query(client)["success_evidence"]
    assert ev["write_like"] is False and ev["write_source"] == "declared"
    assert ev["verified"] is True


def test_undeclared_falls_back_to_the_name_and_says_so(client):
    for _ in range(20):
        insert_observation(outcome="success", error_type=None, error_code=None,
                           normalized_error=None, minutes_ago=5)
    ev = query(client)["success_evidence"]
    assert ev["write_source"] == "name"


def test_the_column_is_added_to_a_database_that_predates_it(tmp_path):
    """create_all never alters an existing table. A production database made
    before `mutates` existed must gain the column on startup, and a second
    startup must be a no-op."""
    import sqlite3

    from sqlalchemy import create_engine

    from app.db.database import _add_missing_columns

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE observations (id INTEGER PRIMARY KEY, service TEXT)")
    con.commit(); con.close()

    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as c:
        _add_missing_columns(c)
    with engine.begin() as c:
        _add_missing_columns(c)  # idempotent
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(observations)")}
    assert "mutates" in cols


# -- one root cause wearing three masks -------------------------------------


def _shape(client, error_type, code, fixed_by=None, fixed=True, n=1):
    fp = observe(client, error_type=error_type, error_code=code, error_message=None)["fingerprint"]
    if fixed_by:
        for i in range(n):
            insert_recovery(fingerprint=fp, action=fixed_by, successful=fixed,
                            minutes_ago=10 + i, reporter_hash=f"r{i}")
    return fp


def test_three_shapes_one_fix_shows_the_neighbours_and_the_shared_fix(client):
    """An expired token seen as not_found, auth_error and a timeout. Three
    fingerprints, none of which alone reaches the recommendation floor. What
    joins them is that refresh_token fixed all three."""
    me = _shape(client, "not_found", "404", fixed_by="refresh_token", n=2)
    _shape(client, "auth_error", "401", fixed_by="refresh_token", n=3)
    _shape(client, "timeout", None, fixed_by="refresh_token", n=1)

    d = query(client, error_type="not_found", error_code="404", error_message=None)
    assert d["fingerprint"] == me
    rel = d["related_failures"]
    assert {r["error_type"] for r in rel} == {"auth_error", "timeout"}, "me excluded, both masks present"
    assert all(r["shares_a_fix_with_you"] for r in rel)
    assert all(r["fixed_by"][0]["action"] == "refresh_token" for r in rel)
    assert len(rel) == 2


def test_a_neighbour_nobody_has_fixed_carries_no_signal(client):
    _shape(client, "not_found", "404", fixed_by="refresh_token")
    _shape(client, "server_error", "503", fixed_by="retry", fixed=False, n=4)  # tried, never worked
    _shape(client, "rate_limit", "429")  # never even tried
    rel = query(client, error_type="not_found", error_code="404", error_message=None)["related_failures"]
    assert rel == []


def test_a_different_fix_is_a_neighbour_but_not_a_shared_one(client):
    _shape(client, "not_found", "404", fixed_by="refresh_token")
    _shape(client, "rate_limit", "429", fixed_by="backoff", n=2)
    rel = query(client, error_type="not_found", error_code="404", error_message=None)["related_failures"]
    assert len(rel) == 1 and rel[0]["error_type"] == "rate_limit"
    assert rel[0]["shares_a_fix_with_you"] is False


def test_neighbours_are_scoped_to_the_same_operation(client):
    _shape(client, "not_found", "404", fixed_by="refresh_token")
    observe(client, operation="get_issue", error_type="auth_error", error_code="401", error_message=None)
    fp_other = query(client, operation="get_issue", error_type="auth_error", error_code="401",
                     error_message=None)["fingerprint"]
    insert_recovery(fingerprint=fp_other, action="refresh_token", successful=True, minutes_ago=5)
    rel = query(client, error_type="not_found", error_code="404", error_message=None)["related_failures"]
    assert rel == [], "a different operation is a different scope"


def test_an_unknown_shape_still_sees_its_neighbours(client):
    """The case this exists for: a brand-new mask on a service where two
    other masks were both fixed the same way. Nothing is known about the
    new shape, and the neighbours are the only evidence there is."""
    _shape(client, "auth_error", "401", fixed_by="refresh_token", n=3)
    _shape(client, "timeout", None, fixed_by="refresh_token", n=2)
    d = query(client, error_type="connection_error", error_code=None, error_message=None)
    assert d["known"] is False and d["recommendation"] is None
    assert {r["error_type"] for r in d["related_failures"]} == {"auth_error", "timeout"}
    assert all(r["shares_a_fix_with_you"] is False for r in d["related_failures"]), (
        "nothing has fixed the new shape yet, so nothing can be shared with it"
    )
