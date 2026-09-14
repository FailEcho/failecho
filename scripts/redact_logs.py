"""Remove credentials that Caddy logged before the header filter existed.

Run once, as root, then never again -- deploy/Caddyfile now filters request and
response headers out of the access log, so nothing new lands there.

    sudo /root/agentwebsite/.venv/bin/python scripts/redact_logs.py --dry-run
    sudo /root/agentwebsite/.venv/bin/python scripts/redact_logs.py

WHY THIS EXISTS
    Caddy's default access log records every request header. Until the filter
    went in, that included X-FailEcho-Operator, Authorization and the raw
    X-Reporter-ID -- the last of which the application is careful to hash
    before it stores it. The filter stopped new ones; this clears the tail.

WHAT IT DOES NOT DO
    It does not delete a single request. Method, URI, status, duration and
    remote IP all survive, so the 404 sweeps and abuse patterns in these files
    remain readable. Only the credential values go.

TWO FILES, TWO METHODS
    rotated .gz  Nothing holds them open, so they are rewritten wholesale with
                 the headers object dropped -- the same shape the live config
                 produces now.

    active log   Caddy has it open and is appending. Rewriting it would break
                 that handle or leave a hole in the file, so it is patched in
                 place, byte for byte: each secret is overwritten with the same
                 number of X's. Same inode, same length, no gap in logging, no
                 restart.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import pathlib
import sys

LOG_DIR = pathlib.Path("/var/log/caddy")
ENV_FILE = pathlib.Path("/etc/failecho.env")

#: Only values long enough to be a secret rather than a setting.
MIN_SECRET_LEN = 8
SECRET_KEYS = ("FIN_FIRST_PARTY_TOKEN", "FIN_REPORTER_SALT")


def secrets_from_env() -> list[str]:
    """The values to hunt for. Never printed, only counted."""
    found = []
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if key.strip() in SECRET_KEYS and len(value) >= MIN_SECRET_LEN:
            found.append(value)
    return found


def clean_rotated(path: pathlib.Path, dry_run: bool) -> tuple[int, int]:
    """Rewrite one .gz with the headers dropped. Returns (lines, with_headers)."""
    lines = carrying = 0
    rewritten: list[str] = []
    with gzip.open(path, "rt", errors="replace") as handle:
        for line in handle:
            lines += 1
            try:
                row = json.loads(line)
            except ValueError:
                rewritten.append(line)
                continue
            request = row.get("request")
            if isinstance(request, dict) and ("headers" in request or "tls" in request):
                carrying += 1
                request.pop("headers", None)
                request.pop("tls", None)
            row.pop("resp_headers", None)
            rewritten.append(json.dumps(row, separators=(",", ":")) + "\n")

    if dry_run or not carrying:
        return lines, carrying

    stat = path.stat()
    temporary = path.with_name(path.name + ".tmp")
    with gzip.open(temporary, "wt") as handle:
        handle.writelines(rewritten)
    os.chown(temporary, stat.st_uid, stat.st_gid)
    os.chmod(temporary, stat.st_mode & 0o777)
    os.replace(temporary, path)
    return lines, carrying


def patch_active(path: pathlib.Path, secrets: list[str], dry_run: bool) -> int:
    """Overwrite each secret in place with X's of the same length."""
    if not path.exists():
        return 0
    mode = "rb" if dry_run else "r+b"
    hits = 0
    with open(path, mode) as handle:
        blob = handle.read()
        for secret in secrets:
            raw = secret.encode()
            start = 0
            while True:
                at = blob.find(raw, start)
                if at == -1:
                    break
                hits += 1
                if not dry_run:
                    handle.seek(at)
                    handle.write(b"X" * len(raw))
                start = at + len(raw)
            if not dry_run:
                blob = blob.replace(raw, b"X" * len(raw))
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="count what would change and write nothing")
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("must run as root: the logs are owned by caddy", file=sys.stderr)
        return 1
    if not LOG_DIR.is_dir():
        print(f"no {LOG_DIR}", file=sys.stderr)
        return 1

    secrets = secrets_from_env()
    if not secrets:
        print(f"no secrets found in {ENV_FILE}; nothing to hunt for", file=sys.stderr)
        return 1
    print(f"{len(secrets)} secret(s) loaded from {ENV_FILE} (values not shown)")
    print("dry run -- nothing will be written\n" if args.dry_run else "")

    for gz in sorted(LOG_DIR.glob("*.log.gz")):
        lines, carrying = clean_rotated(gz, args.dry_run)
        verb = "carry headers" if args.dry_run else "rewritten without headers"
        print(f"  {gz.name}: {carrying} of {lines} lines {verb}")

    active = LOG_DIR / "failecho.log"
    hits = patch_active(active, secrets, args.dry_run)
    verb = "occurrences to overwrite" if args.dry_run else "occurrences overwritten in place"
    print(f"  {active.name}: {hits} secret {verb}")

    if args.dry_run:
        print("\nRe-run without --dry-run to apply.")
    else:
        print("\nDone. Verify with:")
        print("  sudo grep -c -i x-failecho-operator /var/log/caddy/failecho.log")
        print("  sudo zgrep -c -i x-failecho-operator /var/log/caddy/*.gz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
