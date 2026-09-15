"""The local scanner: your own transcripts, nothing sent anywhere.

It reads files that contain everything an agent saw, so most of what is
tested is what must never come out the other side: error text, paths,
anything that is not a tool name, a category and a count.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from failecho_scan import classify, render_html, render_table, scan

HERE = Path(__file__).resolve().parent.parent


# -- the two properties that make it trustworthy ---------------------------


def test_the_scanner_imports_nothing_that_can_open_a_socket():
    """"Never opens a network connection" is only worth saying if it is
    checkable. This reads the import statements rather than trusting a
    docstring."""
    src = (HERE / "failecho_scan" / "__init__.py").read_text()
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    banned = {"urllib", "socket", "http", "requests", "aiohttp", "httpx", "ssl",
              "asyncio", "subprocess", "ftplib", "smtplib", "xmlrpc"}
    assert not (mods & banned), f"scanner imports {sorted(mods & banned)}"


def test_the_three_error_tables_are_one_table():
    """Hook, wrapper, scanner. A failure observed three ways must get one
    name, or the scanner's `--share` will never line up with the network."""
    def names(path):
        text = (HERE / path).read_text()
        block = text[text.index("ERROR_CLASSES = ("):]
        block = block[: block.index("\n)")]
        return re.findall(r'\("([a-z_]+)", re\.compile', block)

    hook = names("plugin/hooks/failecho_hook.py")
    assert names("failecho_autoreport/__init__.py") == hook
    assert names("failecho_scan/__init__.py") == hook


# -- a fixture that looks like a real transcript ---------------------------


def _line(ts, sid, content, kind="assistant"):
    return json.dumps({
        "timestamp": ts, "sessionId": sid, "type": kind, "cwd": "/home/x/proj",
        "message": {"role": kind, "content": content},
    })


def _use(id_, name, args=None):
    return {"type": "tool_use", "id": id_, "name": name, "input": args or {}}


def _result(id_, text, error=False):
    return {"type": "tool_result", "tool_use_id": id_, "is_error": error,
            "content": [{"type": "text", "text": text}]}


SECRET = "Authorization: Bearer sk-live-SUPERSECRET-9f8e7d"


def _write_sessions(root: Path):
    proj = root / "-home-x-proj"
    proj.mkdir(parents=True)

    # Monday: the 422, a blind retry, then it worked after a schema refresh
    monday = [
        _line("2026-09-08T09:00:00Z", "mon", [_use("a1", "mcp__github__create_issue")]),
        _line("2026-09-08T09:00:01Z", "mon",
              [_result("a1", f"422 validation_error: unknown field 'body' {SECRET}", True)], "user"),
        _line("2026-09-08T09:00:02Z", "mon", [_use("a2", "mcp__github__create_issue")]),
        _line("2026-09-08T09:00:03Z", "mon", [_result("a2", "422 validation_error again", True)], "user"),
        _line("2026-09-08T09:00:04Z", "mon", [_use("a3", "mcp__github__refresh_schema")]),
        _line("2026-09-08T09:00:05Z", "mon", [_result("a3", "ok")], "user"),
        _line("2026-09-08T09:00:06Z", "mon", [_use("a4", "mcp__github__create_issue")]),
        _line("2026-09-08T09:00:07Z", "mon", [_result("a4", "issue #42 created")], "user"),
        # a local failure that must not appear: not shared infrastructure
        _line("2026-09-08T09:00:08Z", "mon", [_use("a5", "Bash")]),
        _line("2026-09-08T09:00:09Z", "mon", [_result("a5", "grep: no matches", True)], "user"),
    ]
    # Thursday: same failure, from zero, in a new session. This is the tax.
    thursday = [
        _line("2026-09-11T14:00:00Z", "thu", [_use("b1", "mcp__github__create_issue")]),
        _line("2026-09-11T14:00:01Z", "thu", [_result("b1", "422 validation_error: unknown field", True)], "user"),
        _line("2026-09-11T14:00:02Z", "thu", [_use("b2", "mcp__github__create_issue")]),
        _line("2026-09-11T14:00:03Z", "thu", [_result("b2", "422 validation_error: unknown field", True)], "user"),
    ]
    (proj / "mon-0001.jsonl").write_text("\n".join(monday))
    (proj / "thu-0002.jsonl").write_text("\n".join(thursday))


@pytest.fixture
def transcripts(tmp_path):
    _write_sessions(tmp_path)
    return tmp_path


# -- what it finds ---------------------------------------------------------


def test_it_finds_the_repeat_across_sessions(transcripts):
    report = scan(str(transcripts))
    assert report["sessions_scanned"] == 2
    row = next(r for r in report["rows"] if r["tool"] == "mcp__github__create_issue")
    assert row["sessions"] == 2
    assert row["failures"] == 4
    assert row["error_type"] == "validation_error" and row["error_code"] == "422"
    assert report["repeated_failures"] == 1


def test_the_retry_tax_counts_only_failures_after_it_was_first_solved(transcripts):
    """Monday's two failures came before anything had solved it: not tax.
    Thursday's two came after Monday got past it: tax."""
    report = scan(str(transcripts))
    row = next(r for r in report["rows"] if r["tool"] == "mcp__github__create_issue")
    assert row["retry_tax"] == 2
    assert row["recovered_in"] == 1
    assert report["retry_tax_total"] == 2


def test_what_happened_next_is_reported_as_what_happened(transcripts):
    report = scan(str(transcripts))
    row = next(r for r in report["rows"] if r["tool"] == "mcp__github__create_issue")
    nxt = {x["tool"]: x["times"] for x in row["next"]}
    # after the first 422 the agent retried; after the second it refreshed
    assert nxt["mcp__github__create_issue"] >= 1
    assert nxt["mcp__github__refresh_schema"] == 1
    assert "not necessarily what fixed it" in render_table(report)


def test_local_tools_are_not_counted(transcripts):
    report = scan(str(transcripts))
    assert all(not r["tool"].startswith("Bash") for r in report["rows"])
    assert all(r["tool"].startswith("mcp__") or r["tool"] in ("WebFetch", "WebSearch")
               for r in report["rows"])


def test_min_sessions_filters(transcripts):
    assert scan(str(transcripts), min_sessions=2)["distinct_failures"] == 1
    assert scan(str(transcripts), min_sessions=3)["distinct_failures"] == 0


# -- what it never emits ---------------------------------------------------


def test_error_text_never_reaches_any_output(transcripts):
    """The transcript carried a bearer token inside an error message. It must
    not be in the table, the JSON, or the HTML -- only the category is."""
    report = scan(str(transcripts))
    blob = json.dumps(report) + render_table(report) + render_html(report)
    assert "SUPERSECRET" not in blob
    assert "Bearer" not in blob
    assert "unknown field" not in blob
    assert "validation_error" in blob


def test_no_paths_or_cwd_leak_into_the_html(transcripts):
    report = scan(str(transcripts))
    page = render_html(report)
    assert "/home/x" not in page
    assert str(transcripts) not in page
    assert "nothing was sent anywhere" in page


def test_empty_root_is_a_clear_message_not_a_crash(tmp_path):
    report = scan(str(tmp_path))
    assert report["sessions_scanned"] == 0
    assert "no transcripts found there" in render_table(report)


def test_classification_is_the_shared_table():
    assert classify("429 too many requests") == ("rate_limit", "429")
    assert classify("nothing anyone has a pattern for") == ("error", None)


# -- an empty result must say which kind of empty it is --------------------


def _local_only(root: Path):
    proj = root / "-home-x-proj"
    proj.mkdir(parents=True)
    (proj / "s.jsonl").write_text("\n".join([
        _line("2026-09-10T10:00:00Z", "s", [_use("1", "Bash")]),
        _line("2026-09-10T10:00:01Z", "s", [_result("1", "exit 1", True)], "user"),
        _line("2026-09-10T10:00:02Z", "s", [_use("2", "Read")]),
        _line("2026-09-10T10:00:03Z", "s", [_result("2", "no such file", True)], "user"),
    ]))


def test_local_only_sessions_are_counted_as_read_not_hidden(tmp_path):
    """The first laptop run printed "scanned 0 sessions" on a machine full of
    transcripts, because sessions with no external calls were dropped before
    being counted. Reading 65 and reporting 25 hides the other 40."""
    _local_only(tmp_path)
    report = scan(str(tmp_path))
    assert report["transcripts_read"] == 1
    assert report["sessions_scanned"] == 0
    table = render_table(report)
    assert "read 1 transcript" in table
    assert "0 used an MCP server or a web tool" in table


def test_local_only_result_names_where_the_failures_were(tmp_path):
    """Built-in tool names only -- Bash, Read -- never arguments or text."""
    _local_only(tmp_path)
    table = render_table(scan(str(tmp_path)))
    assert "all in local tools (Bash x1, Read x1)" in table
    assert "no such file" not in table and "exit 1" not in table


def test_a_missing_root_says_so_and_suggests_the_flag(tmp_path):
    table = render_table(scan(str(tmp_path)))
    assert "no transcripts found there" in table
    assert "--root" in table


# -- subagents belong to their session -------------------------------------


def test_a_subagent_transcript_is_the_parent_sessions_not_a_new_one(tmp_path):
    """Layout is <project>/<session>.jsonl plus <project>/<session>/subagents/
    .../<agent>.jsonl. Half the transcripts on the build box were nested and
    the scanner never read them. Worse than missing them would be counting
    them as sessions: one run with five subagents must not look like six
    sessions that all hit the same failure."""
    proj = tmp_path / "-home-x-proj"
    sub = proj / "sess-1111" / "subagents" / "workflows" / "wf_1"
    sub.mkdir(parents=True)
    (proj / "sess-1111.jsonl").write_text("\n".join([
        _line("2026-09-08T09:00:00Z", "sess-1111", [_use("m1", "mcp__github__create_issue")]),
        _line("2026-09-08T09:00:01Z", "sess-1111", [_result("m1", "429 rate limit", True)], "user"),
    ]))
    (sub / "agent-aaaa.jsonl").write_text("\n".join([
        _line("2026-09-08T09:00:30Z", "sess-1111", [_use("s1", "mcp__github__create_issue")]),
        _line("2026-09-08T09:00:31Z", "sess-1111", [_result("s1", "429 rate limit", True)], "user"),
        _line("2026-09-08T09:00:40Z", "sess-1111", [_use("s2", "mcp__github__create_issue")]),
        _line("2026-09-08T09:00:41Z", "sess-1111", [_result("s2", "created")], "user"),
    ]))
    report = scan(str(tmp_path))
    assert report["transcripts_read"] == 2
    assert report["sessions_read"] == 1, "the subagent was counted as its own session"
    row = report["rows"][0]
    assert row["sessions"] == 1
    assert row["failures"] == 2, "the subagent's failure was not read"
    # the subagent's later success counts as this session having got past it
    assert row["recovered_in"] == 1
    assert report["repeated_failures"] == 0


def test_diagnose_prints_structure_and_never_content(transcripts, monkeypatch):
    """--diagnose exists because the first laptop run found 2 transcripts with
    no tool calls and there was no way to tell why. It may print counts, record
    types and key names. It must not print a message, an argument or an error."""
    import failecho_scan

    monkeypatch.setattr(failecho_scan, "CANDIDATE_ROOTS", (str(transcripts),))
    text = failecho_scan.diagnose()
    assert "record types" in text and "tool_result" in text
    assert "SUPERSECRET" not in text and "unknown field" not in text
    assert "issue #42" not in text
    assert "no content was read past the JSON parser" in text


def test_under_wsl_the_windows_home_is_scanned_too(tmp_path, monkeypatch):
    """The first laptop run happened inside WSL. The interpreter's home was
    /home/lenovo with two stray sessions; the desktop app's months of history
    were under /mnt/c/Users/LENOVO. Both must be read, as one report."""
    import failecho_scan

    linux_home = tmp_path / "linux" / ".claude" / "projects"
    win_home = tmp_path / "mnt" / "c" / "Users" / "LENOVO" / ".claude" / "projects"
    _write_sessions(win_home)                      # the real history
    (linux_home / "-stray").mkdir(parents=True)    # WSL's own, empty
    (linux_home / "-stray" / "x.jsonl").write_text(
        _line("2026-09-12T00:00:00Z", "x", "hello", "user"))

    monkeypatch.setattr(failecho_scan, "DEFAULT_ROOT", str(linux_home))
    monkeypatch.setattr(failecho_scan, "_under_wsl", lambda: True)
    monkeypatch.setattr(failecho_scan.os.path, "isdir",
                        lambda p: (p == "/mnt/c/Users") or __import__("os").path.exists(p))
    monkeypatch.setattr(failecho_scan.os, "listdir",
                        lambda p: ["LENOVO", "Public"] if p == "/mnt/c/Users" else __import__("os").listdir(p))
    real_join = failecho_scan.os.path.join
    monkeypatch.setattr(failecho_scan.os.path, "join",
                        lambda *a: str(win_home) if a[:3] == ("/mnt/c/Users", "LENOVO", ".claude") else real_join(*a))

    roots = failecho_scan.default_roots()
    assert str(win_home) in roots, roots
    report = failecho_scan.scan(roots)
    assert report["transcripts_read"] == 3
    assert report["repeated_failures"] == 1, "the Windows history was not read"
