"""Remove `demo_agent` rows from a FailEcho database.

`seed_demo.py --purge` only removes `synthetic` rows. The live-agent demo in
examples/ writes `demo_agent` instead -- correctly, since those are real calls
by agents that are ours -- and nothing until now could take them out again.

Which matters because the demo defaults to 127.0.0.1:8000, and that is also
the port the production service listens on. Point it at a machine running
FailEcho and it writes demo telemetry straight into the live network. It is
labelled, disclosed, and kept out of every adoption number, so nothing is
faked by it; it is still not what should be in the database on launch day.

    python scripts/purge_demo_agent.py <db>            # dry run
    python scripts/purge_demo_agent.py <db> --apply    # delete
"""

from __future__ import annotations

import argparse
import sqlite3

SOURCED_TABLES = (
    "observations",
    "recovery_outcomes",
    "hourly_stats",
    "hourly_recovery_stats",
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("database")
    ap.add_argument("--source", default="demo_agent")
    ap.add_argument("--apply", action="store_true", help="write; otherwise dry run")
    args = ap.parse_args()

    conn = sqlite3.connect(args.database)
    conn.row_factory = sqlite3.Row

    counts = {}
    for table in SOURCED_TABLES:
        cols = {r[1] for r in conn.execute(f"pragma table_info({table})")}
        if "source" not in cols:
            continue
        counts[table] = conn.execute(
            f"select count(*) from {table} where source = ?", (args.source,)
        ).fetchone()[0]

    total = sum(counts.values())
    print(f"rows with source = {args.source!r}:")
    for table, n in counts.items():
        print(f"  {table:24} {n}")
    print(f"  {'total':24} {total}")

    if not args.apply:
        print("\ndry run; pass --apply to delete")
        return 0
    if not total:
        print("nothing to do")
        return 0

    with conn:
        for table in counts:
            conn.execute(f"delete from {table} where source = ?", (args.source,))
        # Catalogue rows whose observations have all gone would otherwise keep
        # answering "known: true" for a failure with no evidence behind it.
        orphans = [
            r[0] for r in conn.execute(
                "select fingerprint from fingerprints where fingerprint not in "
                "(select fingerprint from observations where fingerprint is not null)"
            )
        ]
        for fp in orphans:
            conn.execute("delete from fingerprints where fingerprint = ?", (fp,))
        print(f"deleted {total} rows and {len(orphans)} orphaned fingerprints")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
