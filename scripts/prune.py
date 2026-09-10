"""FailEcho: fold expired raw observations into hourly aggregates and delete them.

    python scripts/prune.py                  # use FIN_RETENTION_HOURS (48h)
    python scripts/prune.py --hours 24       # override the retention window
    python scripts/prune.py --dry-run        # report only, change nothing
    python scripts/prune.py --vacuum         # also reclaim file space (locks)

Safe to run repeatedly: raw rows are aggregated and deleted in one
transaction, so a second run finds nothing left to fold. Nothing is
double-counted.

Recommended cron (hourly, at :15):

    15 * * * * /path/to/.venv/bin/python /path/to/scripts/prune.py >> /var/log/failure-network-prune.log 2>&1
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.core.clock import isoformat_z  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.retention import aggregate_and_prune  # noqa: E402
from app.db.database import SessionLocal, engine, init_db  # noqa: E402


def _database_size() -> str:
    marker = "sqlite+aiosqlite:///"
    if not settings.database_url.startswith(marker):
        return "n/a (not SQLite)"
    path = Path(settings.database_url[len(marker) :])
    if not path.exists():
        return "n/a"
    total = path.stat().st_size
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            total += sidecar.stat().st_size
    return f"{total / 1024 / 1024:.2f} MB"


async def main(hours: int | None, dry_run: bool, vacuum: bool) -> None:
    await init_db()
    async with SessionLocal() as session:
        report = await aggregate_and_prune(
            session, retention_hours=hours, dry_run=dry_run
        )

    print(f"Retention window: {hours or settings.retention_hours}h")
    print(f"Cutoff:           {isoformat_z(report.cutoff)}")
    print(f"Aggregated {report.observations_aggregated} observations "
          f"into {report.observation_buckets} hourly buckets")
    print(f"Aggregated {report.recovery_outcomes_aggregated} recovery outcomes "
          f"into {report.recovery_buckets} hourly buckets")
    print(f"Deleted {report.observations_deleted} raw observations")
    print(f"Deleted {report.recovery_outcomes_deleted} raw recovery outcomes")
    for note in report.notes:
        print(f"Note: {note}")

    if vacuum and not dry_run:
        # VACUUM cannot run inside a transaction and briefly locks the file.
        async with engine.connect() as connection:
            await connection.execute(text("VACUUM"))
        print("Vacuumed database file")

    print(f"Database size:    {_database_size()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=None, help="retention window")
    parser.add_argument("--dry-run", action="store_true", help="report only")
    parser.add_argument("--vacuum", action="store_true", help="reclaim file space")
    args = parser.parse_args()
    asyncio.run(main(hours=args.hours, dry_run=args.dry_run, vacuum=args.vacuum))
