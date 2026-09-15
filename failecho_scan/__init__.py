"""Your agent has already solved this. Here is the proof, from your own logs.

Reads the Claude Code transcripts already on this machine and shows which
external tool failures you have hit in more than one session -- the same 422,
rediscovered on Monday, and again on Thursday, by an agent that could not
remember Monday.

    python -m failecho_scan                # the table
    python -m failecho_scan --html out.html  # sessions across, failures down
    python -m failecho_scan --json          # everything, for your own tooling

Three things it shows:

1. **The repeat.** Failures seen in two or more separate sessions.
2. **The retry tax.** Failures that happened *after* an earlier session had
   already got past the same failure. Calls spent re-solving solved problems.
3. **What happened next.** For a repeated failure, the tool your agent reached
   for immediately afterwards, and whether the failing tool later succeeded in
   that session. A heuristic, labelled as one: it is what happened, not
   necessarily what fixed it.

What it never does: open a network connection. There is no `urllib`, no
`socket`, no `http` anywhere in this file, and a test asserts that. It reads
files under `~/.claude/projects` and writes to stdout or to the path you name.
Transcripts contain everything your agent saw; this file reads tool names,
error status and timestamps from them and nothing else. Error text is
classified into a short category and then dropped -- it is never printed and
never written, not even to the JSON output.

Only external tools count. `Bash`, `Read`, `Edit`, `grep` finding nothing --
those are local, and nobody else calling the same service can help with
them. MCP tools and the web tools are shared infrastructure; those are the
ones another session, or another agent, could have warned you about.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import html
import json
import os
import re
import sys

__all__ = ["scan", "classify", "main"]
__version__ = "0.1.0"

DEFAULT_ROOT = os.path.expanduser("~/.claude/projects")

#: Tools whose failures are worth knowing about across sessions. Everything
#: else is local and stays out of the report entirely.
EXTERNAL_PREFIXES = ("mcp__",)
EXTERNAL_TOOLS = {"WebFetch", "WebSearch"}

#: Kept identical to the Claude Code hook and the autoreport wrapper, and a
#: test reads all three files and checks. One failure must get one name
#: however it was observed.
ERROR_CLASSES = (
    ("timeout", re.compile(r"time[d ]?\s?out|deadline exceeded|ETIMEDOUT", re.I)),
    ("rate_limit", re.compile(r"\b429\b|rate.?limit|too many requests|quota", re.I)),
    ("auth_error", re.compile(
        r"\b40[13]\b|unauthori[sz]ed|forbidden|permission denied|authenticat|invalid (api )?key",
        re.I)),
    ("not_found", re.compile(r"\b404\b|not found|no such", re.I)),
    ("validation_error", re.compile(
        r"\b4(00|22)\b|invalid|validation|required|must be|schema", re.I)),
    ("connection_error", re.compile(
        r"ECONN(REFUSED|RESET)|ENOTFOUND|EPIPE|connection (refused|reset|closed|error)"
        r"|network|socket", re.I)),
    ("server_error", re.compile(
        r"\b5\d\d\b|internal (server )?error|service unavailable|bad gateway|upstream", re.I)),
)
HTTP_CLASSES = {"rate_limit", "auth_error", "not_found", "validation_error", "server_error"}
_STATUS = re.compile(r"\b([45]\d\d)\b")


def classify(text: str) -> tuple[str, str | None]:
    """A failure's class and code, from its text. The text is not kept."""
    for name, pattern in ERROR_CLASSES:
        if pattern.search(text):
            code = None
            if name in HTTP_CLASSES:
                match = _STATUS.search(text)
                if match:
                    code = match.group(1)
            return name, code
    return "error", None


def is_external(tool: str) -> bool:
    return tool.startswith(EXTERNAL_PREFIXES) or tool in EXTERNAL_TOOLS


def split_tool(tool: str) -> tuple[str, str]:
    """`mcp__github__create_issue` -> ("github", "create_issue")."""
    if tool.startswith("mcp__"):
        parts = tool.split("__", 2)
        if len(parts) == 3:
            return parts[1], parts[2]
    return tool, "call"


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text"
        )
    return ""


def _parse_ts(value) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def read_session(session_id: str, paths: list[str]) -> dict:
    """One session -- its main transcript plus any subagent transcripts --
    as one ordered list of external tool calls.

    Each call: {"tool", "ok", "ts", "error_type", "error_code"}. The text of a
    failure is classified and discarded inside this function; it does not
    leave it.
    """
    pending: dict[str, tuple[str, float | None]] = {}
    calls: list[dict] = []
    first_ts = None
    tool_results = 0
    local_failures: collections.Counter = collections.Counter()
    for path in paths:
        _read_file(path, pending, calls, local_failures, counters := {"n": 0, "first": first_ts})
        tool_results += counters["n"]
        if counters["first"] is not None and (first_ts is None or counters["first"] < first_ts):
            first_ts = counters["first"]
    calls.sort(key=lambda c: c["ts"] or 0)
    short = session_id.rsplit("/", 1)[-1][:8]
    return {"id": short, "files": len(paths), "started": first_ts,
            "calls": calls, "tool_results": tool_results, "local_failures": local_failures}


def _read_file(path, pending, calls, local_failures, counters) -> None:
    first_ts = None
    for line in open(path, encoding="utf-8", errors="ignore"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        ts = _parse_ts(d.get("timestamp"))
        if first_ts is None and ts is not None:
            first_ts = ts
        m = d.get("message")
        content = m.get("content") if isinstance(m, dict) else None
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            kind = c.get("type")
            if kind == "tool_use":
                name = c.get("name") or "?"
                pending[c.get("id")] = (name, ts)
            elif kind == "tool_result":
                name, started = pending.pop(c.get("tool_use_id"), ("?", None))
                counters["n"] += 1
                if not is_external(name):
                    if c.get("is_error"):
                        local_failures[name] += 1
                    continue
                if c.get("is_error"):
                    error_type, code = classify(_result_text(c.get("content"))[:600])
                    calls.append({"tool": name, "ok": False, "ts": started or ts,
                                  "error_type": error_type, "error_code": code})
                else:
                    calls.append({"tool": name, "ok": True, "ts": started or ts,
                                  "error_type": None, "error_code": None})
    counters["first"] = first_ts


def find_transcripts(root: str) -> dict[str, list[str]]:
    """Every transcript under the root, grouped by the session it belongs to.

    Layout is <root>/<project>/<session>.jsonl for the main thread, and
    <root>/<project>/<session>/subagents/.../<agent>.jsonl for anything the
    session delegated. A subagent's failure is the session's failure: it was
    the same task, the same day, the same human. Counting it as a separate
    session would let one run with five subagents look like six sessions,
    which inflates the one number this tool exists to get right.
    """
    sessions: dict[str, list[str]] = collections.defaultdict(list)
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).split(os.sep)
            if len(rel) < 2:
                continue  # a stray file at the root is not a transcript
            project, head = rel[0], rel[1]
            session = head[:-6] if head.endswith(".jsonl") else head
            sessions[f"{project}/{session}"].append(path)
    return {k: sorted(v) for k, v in sorted(sessions.items())}


# ---------------------------------------------------------------------------
# the analysis
# ---------------------------------------------------------------------------


def scan(root: str = DEFAULT_ROOT, min_sessions: int = 1) -> dict:
    groups = find_transcripts(root)
    all_sessions = [read_session(sid, paths) for sid, paths in groups.items()]
    transcripts_read = sum(len(p) for p in groups.values())
    tool_results_seen = sum(s["tool_results"] for s in all_sessions)
    local_failures: collections.Counter = collections.Counter()
    for s in all_sessions:
        local_failures.update(s["local_failures"])
    # only sessions that touched shared infrastructure take part in the grid
    sessions = [s for s in all_sessions if s["calls"]]
    sessions.sort(key=lambda s: s["started"] or 0)

    # key -> per-session facts
    Key = tuple  # (tool, error_type, error_code)
    per_key_sessions: dict[Key, dict[str, dict]] = collections.defaultdict(dict)
    solved_in: dict[str, set] = collections.defaultdict(set)  # tool -> sessions where it later succeeded

    for s in sessions:
        calls = s["calls"]
        for i, call in enumerate(calls):
            if call["ok"]:
                continue
            key = (call["tool"], call["error_type"], call["error_code"])
            facts = per_key_sessions[key].setdefault(
                s["id"], {"count": 0, "next": collections.Counter(), "recovered": False,
                          "started": s["started"]})
            facts["count"] += 1
            # what happened next: the very next external call, and whether the
            # same tool succeeded later in this session
            if i + 1 < len(calls):
                facts["next"][calls[i + 1]["tool"]] += 1
            if any(c["ok"] and c["tool"] == call["tool"] for c in calls[i + 1:]):
                facts["recovered"] = True
                solved_in[call["tool"]].add(s["id"])

    rows = []
    for key, by_session in per_key_sessions.items():
        tool, error_type, code = key
        ordered = sorted(by_session.items(), key=lambda kv: kv[1]["started"] or 0)
        session_ids = [sid for sid, _ in ordered]
        total = sum(f["count"] for _, f in ordered)

        # retry tax: failures in any session that began after the first
        # session in which this tool was already got past
        first_solved = min(
            (f["started"] or float("inf") for sid, f in ordered if f["recovered"]),
            default=None,
        )
        tax = 0
        if first_solved is not None:
            tax = sum(f["count"] for sid, f in ordered
                      if (f["started"] or 0) > first_solved)

        nxt: collections.Counter = collections.Counter()
        for _, f in ordered:
            nxt.update(f["next"])
        service, operation = split_tool(tool)
        rows.append({
            "tool": tool, "service": service, "operation": operation,
            "error_type": error_type, "error_code": code,
            "sessions": len(session_ids), "session_ids": session_ids,
            "failures": total, "retry_tax": tax,
            "recovered_in": sum(1 for _, f in ordered if f["recovered"]),
            "next": [{"tool": t, "times": n} for t, n in nxt.most_common(3)],
        })

    rows = [r for r in rows if r["sessions"] >= min_sessions]
    rows.sort(key=lambda r: (-r["sessions"], -r["failures"], r["tool"]))
    return {
        "version": __version__,
        "root": root,
        "transcripts_read": transcripts_read,
        "sessions_read": len(all_sessions),
        "tool_results_seen": tool_results_seen,
        "local_failures": [{"tool": t, "failures": n} for t, n in local_failures.most_common(5)],
        "sessions_scanned": len(sessions),
        "session_ids": [s["id"] for s in sessions],
        "distinct_failures": len(rows),
        "repeated_failures": sum(1 for r in rows if r["sessions"] >= 2),
        "retry_tax_total": sum(r["retry_tax"] for r in rows),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


def render_table(report: dict) -> str:
    out = []
    n = report["sessions_scanned"]
    shown = report["root"]
    home = os.path.expanduser("~")
    if shown.startswith(home):
        shown = "~" + shown[len(home):]
    read = report["transcripts_read"]
    sess = report["sessions_read"]
    out.append(f"read {read} transcript{'s' if read != 1 else ''} from {sess} "
               f"session{'s' if sess != 1 else ''} under {shown}; "
               f"{n} used an MCP server or a web tool")
    out.append("")
    if read == 0:
        out.append("no transcripts found there. Claude Code writes one .jsonl per session "
                   "under <root>/<project>/; pass --root if yours live elsewhere.")
        return "\n".join(out)
    if not report["rows"]:
        out.append("no external tool failures found.")
        if report["local_failures"]:
            local = ", ".join(f"{x['tool']} x{x['failures']}" for x in report["local_failures"])
            out.append(f"the failures in these sessions were all in local tools ({local}). "
                       "those are not counted: nobody else calling the same service can "
                       "help with a local error, so there is nothing to remember across "
                       "sessions or share.")
        else:
            out.append(f"{report['tool_results_seen']} tool calls, none of them failed. nothing to report.")
        return "\n".join(out)
    out.append(f"{'tool':<40} {'error':<20} {'sessions':>8} {'failures':>8} {'tax':>5}   next")
    for r in report["rows"]:
        label = r["error_type"] + (f"/{r['error_code']}" if r["error_code"] else "")
        nxt = ", ".join(f"{x['tool'].replace('mcp__', '')} x{x['times']}" for x in r["next"]) or "-"
        out.append(f"{r['tool'].replace('mcp__', '')[:40]:<40} {label:<20} "
                   f"{r['sessions']:>8} {r['failures']:>8} {r['retry_tax']:>5}   {nxt[:40]}")
    out.append("")
    rep = report["repeated_failures"]
    out.append(f"{report['distinct_failures']} distinct failures; "
               f"{rep} hit in 2+ separate sessions.")
    tax = report["retry_tax_total"]
    if tax:
        out.append(f"retry tax: {tax} failure{'s' if tax != 1 else ''} on problems an earlier "
                   f"session had already got past.")
    out.append("")
    out.append("'next' is the tool called immediately after the failure -- what happened, "
               "not necessarily what fixed it.")
    return "\n".join(out)


def render_html(report: dict) -> str:
    """Sessions across the top in order, failures down the side. A cell is
    filled when that failure happened in that session; darker means more."""
    sessions = report["session_ids"]
    rows = report["rows"]
    esc = html.escape

    def cell(r, sid):
        n = r["session_ids"].count(sid)
        if not n:
            return '<td class="c"></td>'
        return f'<td class="c hit" title="{n} in session {esc(sid)}">{n}</td>'

    head = "".join(f'<th class="s"><span>{esc(s)}</span></th>' for s in sessions)
    body = []
    for r in rows:
        label = r["error_type"] + (f"/{r['error_code']}" if r["error_code"] else "")
        cells = "".join(cell(r, s) for s in sessions)
        body.append(
            f'<tr><th class="t">{esc(r["tool"].replace("mcp__", ""))}'
            f'<small>{esc(label)}</small></th>{cells}'
            f'<td class="n">{r["sessions"]}</td><td class="n">{r["retry_tax"]}</td></tr>'
        )
    tax = report["retry_tax_total"]
    return f"""<!doctype html><meta charset="utf-8">
<title>failecho-scan</title>
<style>
body{{margin:0;padding:28px 20px;background:#000;color:#edeae3;font:14px/1.45 system-ui,sans-serif}}
h1{{font-size:20px;font-weight:500;margin:0 0 4px}} p{{margin:0 0 18px;color:#9a968e}}
.wrap{{overflow-x:auto}} table{{border-collapse:collapse}}
th,td{{padding:0;border:1px solid #1c1c1c}}
th.s{{height:88px;width:22px;vertical-align:bottom}} th.s span{{display:block;transform:rotate(-90deg);transform-origin:left bottom;white-space:nowrap;font:11px monospace;color:#7a766e;width:22px;translate:2px -6px}}
th.t{{text-align:left;padding:5px 12px 5px 8px;font-weight:500;white-space:nowrap}} th.t small{{display:block;color:#7a766e;font:11px monospace}}
td.c{{width:22px;height:26px;text-align:center;font:11px monospace}} td.hit{{background:#7c3aed;color:#fff}}
td.n{{padding:0 10px;text-align:right;font:12px monospace;color:#9a968e}}
tfoot td{{padding:6px 10px;color:#7a766e;font:11px monospace}}
.k{{margin-top:20px;color:#9a968e;font-size:12.5px}}
</style>
<h1>Your agent has already solved this.</h1>
<p>{report["sessions_scanned"]} sessions, oldest to newest. A filled cell is a failure that happened in that session.
Retry tax: <b style="color:#edeae3">{tax}</b> failure{'s' if tax != 1 else ''} on problems an earlier session had already got past.</p>
<div class="wrap"><table>
<thead><tr><th></th>{head}<th class="n">sessions</th><th class="n">tax</th></tr></thead>
<tbody>{''.join(body)}</tbody>
</table></div>
<div class="k">Only external tools are counted (MCP servers and the web tools). Error text was classified into the category shown and then dropped; it is not in this file. Generated locally by failecho-scan {esc(report["version"])}; nothing was sent anywhere.</div>
"""


# ---------------------------------------------------------------------------
# --diagnose: where are the transcripts on this machine?
# ---------------------------------------------------------------------------

#: Places Claude Code has been seen to keep session data, across the CLI, the
#: desktop app and Windows. Structural facts only come out of this -- counts,
#: record types, key names -- never a line of content.
CANDIDATE_ROOTS = (
    "~/.claude/projects",
    "~/.claude/sessions",
    "~/.claude",
    "~/.config/claude",
    "~/Library/Application Support/Claude",
    "%APPDATA%/Claude",
    "%LOCALAPPDATA%/Claude",
    "%APPDATA%/claude-code",
    "%LOCALAPPDATA%/claude-code",
)


def _expand(p: str) -> str:
    return os.path.expandvars(os.path.expanduser(p))


def _shape(path: str, limit: int = 4000) -> dict:
    """What kind of records a transcript holds. Keys and types only."""
    types: collections.Counter = collections.Counter()
    top_keys: set = set()
    content_types: collections.Counter = collections.Counter()
    lines = 0
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            lines += 1
            if lines > limit:
                break
            try:
                d = json.loads(line)
            except ValueError:
                types["<not json>"] += 1
                continue
            if not isinstance(d, dict):
                types["<not object>"] += 1
                continue
            types[str(d.get("type"))] += 1
            top_keys.update(k for k in d.keys() if k in
                            ("message", "toolUseResult", "tool_use_result", "sessionId",
                             "timestamp", "isSidechain", "parentUuid", "summary", "leafUuid"))
            m = d.get("message")
            c = m.get("content") if isinstance(m, dict) else None
            if isinstance(c, list):
                for x in c:
                    if isinstance(x, dict):
                        content_types[str(x.get("type"))] += 1
            elif c is not None:
                content_types[f"<{type(c).__name__}>"] += 1
    return {"lines": lines, "record_types": dict(types.most_common(6)),
            "top_keys": sorted(top_keys), "content_block_types": dict(content_types.most_common(6))}


def diagnose() -> str:
    home = os.path.expanduser("~")
    out = [f"python {sys.version.split()[0]} on {sys.platform}",
           f"home resolves to {home}", ""]
    for cand in CANDIDATE_ROOTS:
        root = _expand(cand)
        if not os.path.isdir(root):
            out.append(f"  {cand:<44} absent")
            continue
        jsonl = []
        for dirpath, _d, files in os.walk(root):
            jsonl += [os.path.join(dirpath, f) for f in files if f.endswith(".jsonl")]
            if len(jsonl) > 2000:
                break
        subdirs = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))[:12]
        out.append(f"  {cand:<44} {len(jsonl):>5} .jsonl   dirs: {', '.join(subdirs) or '-'}")
    out.append("")
    root = _expand(CANDIDATE_ROOTS[0])
    groups = find_transcripts(root) if os.path.isdir(root) else {}
    shown = 0
    for sid, paths in groups.items():
        for path in paths:
            if shown >= 3:
                break
            sh = _shape(path)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            out.append(f"  {rel if len(rel) <= 76 else '...' + rel[-73:]}")
            out.append(f"     {sh['lines']} lines; record types {sh['record_types']}")
            out.append(f"     top-level keys seen {sh['top_keys']}")
            out.append(f"     message.content block types {sh['content_block_types']}")
            shown += 1
    if not shown:
        out.append("  (no transcripts under the default root to describe)")
    out.append("")
    out.append("this is structure only: counts, record types and key names. no content "
               "was read past the JSON parser and none is printed.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="failecho-scan", description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=DEFAULT_ROOT, help="transcripts root (default ~/.claude/projects)")
    ap.add_argument("--min-sessions", type=int, default=1,
                    help="only show failures seen in at least this many sessions")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    ap.add_argument("--html", metavar="PATH", help="write the bird's-eye view to this file")
    ap.add_argument("--diagnose", action="store_true",
                    help="show where transcripts are on this machine and what shape they are; "
                         "structure only, no content")
    args = ap.parse_args(argv)

    if args.diagnose:
        print(diagnose())
        return 0

    if not os.path.isdir(args.root):
        print(f"no transcripts at {args.root}", file=sys.stderr)
        return 2
    report = scan(args.root, args.min_sessions)
    if args.html:
        with open(args.html, "w", encoding="utf-8") as fh:
            fh.write(render_html(report))
        print(f"wrote {args.html}", file=sys.stderr)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_table(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
