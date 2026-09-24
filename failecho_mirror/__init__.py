"""Mirror the lab's real-service evidence into production, labelled first-party.

On 24 Sep a new user's agent, pointed at production, got no answer for a Groq
429 while the lab knew "switch_model, worked 310/341": the lab's evidence
stayed in the lab. Both networks are fed by our own agents, and production
already shows first-party reports the way it should -- counted apart from
adoption, never in the independent-reporter numbers. This copies what the
lab's agents report about **real public services** into production, so the
first outside user gets the answers the lab has already paid for.

What crosses, and nothing else:
* observations from the lab's own agents (`source = agent`) against
  ALLOWED_SERVICES: service, operation, outcome, error class and code,
  latency, mutates -- the same metadata the wrapper sends;
* recovery outcomes on those services' fingerprints: fingerprint, action,
  success.

Never: the lab's synthetic test endpoints (httpbingo), a team's private
evidence (separate tables this does not read), error text (the lab's agents
send none), prompts, arguments, results, bodies.

Reporters stay distinct but pseudonymous: each lab agent becomes
`lab-<first 16 of its lab reporter hash>`, so "backed by 9 reporters" still
means nine of our agents, and production re-hashes it like any reporter id.
Production stamps each record on arrival; a backfill therefore lands in one
hour bucket, where the per-reporter hourly cap keeps it from counting more
than live traffic would.

Inert unless FAILECHO_MIRROR_ENABLED=1. Refuses to run without the operator
token (CLAUDE.md: nothing that reports points at production without it).
Never faster than FAILECHO_MIRROR_PER_MINUTE writes.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from typing import Callable

#: Real public services only. The lab's other traffic -- httpbingo's
#: always-broken and slow endpoints, the guest MCP server "packages" -- exists
#: to test the product, and no user of production will ever call it.
ALLOWED_SERVICES = frozenset({
    "api.github.com", "github.com", "pypi.org", "files.pythonhosted.org", "registry.npmjs.org",
    "crates.io", "api.stackexchange.com", "peps.python.org", "python.org",
    "api.groq.com", "generativelanguage.googleapis.com", "openrouter.ai",
    "integrate.api.nvidia.com", "api.mistral.ai", "ollama.com", "api.xkiro.com",
})

USER_AGENT = "failecho-mirror/1"

Post = Callable[[str, dict, dict], int]


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _state_path() -> str:
    return os.path.join(_env("STATE_DIRECTORY", "/var/lib/failecho-mirror"), "cursor.json")


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(path: str, state: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, sort_keys=True)
    os.replace(tmp, path)


def connect(db_path: str) -> sqlite3.Connection:
    """The lab database, read-only: the mirror never writes to it."""
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def start_cursor(db: sqlite3.Connection, backfill_hours: float) -> dict:
    """Where a first run begins: the rows of the last `backfill_hours`."""
    since = (dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(hours=backfill_hours)).isoformat(" ")
    cursor = {}
    for table, key in (("observations", "observations"), ("recovery_outcomes", "outcomes")):
        row = db.execute(f"select min(id) from {table} where created_at >= ?", (since,)).fetchone()
        first = row[0] if row and row[0] is not None else None
        if first is None:
            first = (db.execute(f"select max(id) from {table}").fetchone()[0] or 0) + 1
        cursor[key] = first - 1
    return cursor


def reporter_id(lab_hash: str | None) -> str | None:
    return f"lab-{lab_hash[:16]}" if lab_hash else None


def observation_body(row: sqlite3.Row) -> dict:
    body = {"service": row["service"], "operation": row["operation"], "outcome": row["outcome"]}
    for key in ("version", "schema_hash", "error_type", "error_code", "latency_ms", "mutates"):
        if row[key] is not None:
            body[key] = bool(row[key]) if key == "mutates" else row[key]
    return body


def pending_observations(db: sqlite3.Connection, after: int, limit: int) -> list[tuple[int, dict | None, str | None]]:
    """(id, body or None when skipped, lab reporter hash), in id order."""
    rows = db.execute(
        "select id, service, operation, version, schema_hash, outcome, error_type, error_code, "
        "normalized_error, latency_ms, mutates, reporter_hash, source from observations "
        "where id > ? order by id limit ?", (after, limit)).fetchall()
    out = []
    for row in rows:
        keep = (row["source"] == "agent" and row["service"] in ALLOWED_SERVICES
                and row["normalized_error"] is None)
        out.append((row["id"], observation_body(row) if keep else None, row["reporter_hash"]))
    return out


def pending_outcomes(db: sqlite3.Connection, after: int, limit: int) -> list[tuple[int, dict | None, str | None]]:
    rows = db.execute(
        "select r.id, r.fingerprint, r.action, r.successful, r.reporter_hash, r.source, f.service "
        "from recovery_outcomes r left join fingerprints f on f.fingerprint = r.fingerprint "
        "where r.id > ? order by r.id limit ?", (after, limit)).fetchall()
    out = []
    for row in rows:
        keep = row["source"] == "agent" and row["service"] in ALLOWED_SERVICES
        body = {"fingerprint": row["fingerprint"], "action": row["action"],
                "successful": bool(row["successful"])} if keep else None
        out.append((row["id"], body, row["reporter_hash"]))
    return out


def http_post(url: str, body: dict, headers: dict) -> int:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def run(*, endpoint: str, token: str, db_path: str, state_path: str, max_rows: int = 500,
        per_minute: int = 60, backfill_hours: float = 6.0, post: Post = http_post,
        sleep: Callable[[float], None] = time.sleep) -> dict:
    """One pass: send what is new since the cursor, oldest first; stop at the
    first refusal and keep the cursor there, so nothing is skipped or sent
    twice. Returns what happened."""
    db = connect(db_path)
    db.row_factory = sqlite3.Row
    state = load_state(state_path)
    cursor = state.get("cursor") or start_cursor(db, backfill_hours)
    gap = 60.0 / max(1, per_minute)
    result = {"sent_observations": 0, "sent_outcomes": 0, "skipped": 0, "stopped": None}
    budget = max_rows
    for kind, key, fetch, path in (("observe", "observations", pending_observations, "/v1/observe"),
                                   ("outcome", "outcomes", pending_outcomes, "/v1/outcome")):
        # observations first: an outcome's fingerprint should exist before it
        while budget > 0 and result["stopped"] is None:
            batch = fetch(db, cursor[key], min(budget, 200))
            if not batch:
                break
            for row_id, body, lab_hash in batch:
                if body is None:
                    result["skipped"] += 1
                    cursor[key] = row_id
                    continue
                if budget <= 0:
                    break
                headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT,
                           "X-FailEcho-Operator": token}
                rid = reporter_id(lab_hash)
                if rid:
                    headers["X-Reporter-ID"] = rid
                status = post(endpoint + path, body, headers)
                if not 200 <= status < 300:
                    result["stopped"] = f"{path} answered {status}"
                    break
                cursor[key] = row_id
                result["sent_observations" if kind == "observe" else "sent_outcomes"] += 1
                budget -= 1
                sleep(gap)
            save_state(state_path, {"cursor": cursor, "last_run": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                                    "last_result": result})
    db.close()
    save_state(state_path, {"cursor": cursor, "last_run": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                            "last_result": result})
    return result


def main() -> int:
    if _env("FAILECHO_MIRROR_ENABLED") != "1":
        print("failecho-mirror: not enabled (FAILECHO_MIRROR_ENABLED=1); doing nothing")
        return 0
    endpoint = (_env("FAILECHO_MIRROR_ENDPOINT") or "").rstrip("/")
    token = _env("FIN_FIRST_PARTY_TOKEN") or _env("FAILECHO_OPERATOR_TOKEN")
    if not endpoint:
        print("failecho-mirror: FAILECHO_MIRROR_ENDPOINT is not set; refusing to guess", file=sys.stderr)
        return 2
    if not token:
        print("failecho-mirror: no operator token; refusing to report unlabelled", file=sys.stderr)
        return 2
    if "lab.failecho.com" in endpoint:
        print("failecho-mirror: the target is the lab itself", file=sys.stderr)
        return 2
    result = run(endpoint=endpoint, token=token,
                 db_path=_env("FAILECHO_MIRROR_LAB_DB", "/srv/failecho-lab/data/lab.db"),
                 state_path=_state_path(),
                 max_rows=int(_env("FAILECHO_MIRROR_MAX_ROWS", "500")),
                 per_minute=int(_env("FAILECHO_MIRROR_PER_MINUTE", "60")),
                 backfill_hours=float(_env("FAILECHO_MIRROR_BACKFILL_HOURS", "6")))
    print("failecho-mirror:", json.dumps(result, sort_keys=True))
    return 1 if result["stopped"] and "429" not in result["stopped"] else 0
