#!/bin/sh
# Nightly FailEcho backup. Uses sqlite3 .backup so it is consistent while the
# service keeps serving -- no downtime, no WAL surprises.
set -eu
DB=/srv/failecho/data/failure_network.db
DEST=/srv/failecho/backups
STAMP=$(date -u +%Y%m%d-%H%M%S)
mkdir -p "$DEST"
sqlite3 "$DB" ".backup '$DEST/failecho-$STAMP.db'"
gzip -f "$DEST/failecho-$STAMP.db"
# Keep 14 days.
find "$DEST" -name 'failecho-*.db.gz' -mtime +14 -delete
echo "backup complete: $DEST/failecho-$STAMP.db.gz"
