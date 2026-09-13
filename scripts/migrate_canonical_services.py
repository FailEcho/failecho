"""One-off: rewrite service names and fingerprints to their canonical form.

`service` is part of the fingerprint, so introducing canonical names changes
every fingerprint that was computed from a non-canonical one. Rows written
before the change would still be in the database but unreachable: the query
side canonicalises, so it would look for a fingerprint the old rows do not
have, and the evidence would silently never be found again.

Small enough to do in one transaction. Run it once, immediately after
deploying the canonicalisation, against a database you have just backed up.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.aliases import canonical_service  # noqa: E402
from app.core.fingerprint import compute_fingerprint  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("database")
    ap.add_argument("--apply", action="store_true", help="write; otherwise dry run")
    args = ap.parse_args()

    conn = sqlite3.connect(args.database)
    conn.row_factory = sqlite3.Row

    remap: dict[str, str] = {}
    plan: list[tuple[str, int, str, str, str | None, str | None]] = []

    for table in ("observations", "fingerprints", "hourly_stats"):
        cols = {r[1] for r in conn.execute(f"pragma table_info({table})")}
        if "service" not in cols:
            continue
        for row in conn.execute(f"select rowid as _rid, * from {table}"):
            old = row["service"]
            new = canonical_service(old)
            if old != new:
                remap[old] = new
            old_fp = row["fingerprint"] if "fingerprint" in row.keys() else None
            new_fp = None
            if old_fp:
                patched = dict(row)
                patched["service"] = new
                new_fp = compute_fingerprint(
                    service=new,
                    operation=patched.get("operation"),
                    version=patched.get("version"),
                    schema_hash=patched.get("schema_hash"),
                    error_type=patched.get("error_type"),
                    error_code=patched.get("error_code"),
                    normalized_error=patched.get("normalized_error"),
                )
            if old != new or (old_fp and new_fp != old_fp):
                plan.append((table, row["_rid"], old, new, old_fp, new_fp))

    print(f"service names to rewrite: {len(remap)}")
    for old, new in sorted(remap.items()):
        print(f"  {old}  ->  {new}")
    print(f"rows to touch: {len(plan)}")

    fp_map = {old: new for _, _, _, _, old, new in plan if old and new and old != new}

    if not args.apply:
        print("\ndry run; pass --apply to write")
        return 0

    with conn:
        for table, rowid, _old, new, old_fp, new_fp in plan:
            if old_fp and new_fp:
                conn.execute(
                    f"update {table} set service = ?, fingerprint = ? where rowid = ?",
                    (new, new_fp, rowid),
                )
            else:
                conn.execute(
                    f"update {table} set service = ? where rowid = ?", (new, rowid)
                )
        # tables that carry only the fingerprint
        for table in ("recovery_outcomes", "hourly_recovery_stats"):
            cols = {r[1] for r in conn.execute(f"pragma table_info({table})")}
            if "fingerprint" not in cols:
                continue
            for old_fp, new_fp in fp_map.items():
                conn.execute(
                    f"update {table} set fingerprint = ? where fingerprint = ?",
                    (new_fp, old_fp),
                )
    print("applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
