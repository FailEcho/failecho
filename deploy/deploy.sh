#!/usr/bin/env bash
# Deploy what is on origin/main to the running service, and prove it took.
#
# This replaces the four commands that were typed by hand nineteen times on
# 2026-09-15: fetch, reset, restart, curl. Same steps, in the same order, with
# the checks that were done by eye done by the script instead.
#
#   deploy/deploy.sh            # deploy origin/main
#   deploy/deploy.sh --check    # only run the post-deploy checks
#
# Runs as root (it restarts a unit). The checkout is owned by the failecho
# user, so git runs as that user; nothing else touches the tree.
#
# What it refuses to do: deploy a tree with local modifications. The deploy
# checkout must be a clean copy of origin/main -- a hand-edit there is drift,
# and drift is how a fix gets lost on the next deploy.

set -euo pipefail

SRV=/srv/failecho
UNIT=failecho
SITE=https://failecho.com
PAGES="/ /setup /about /demo /network /docs /llms.txt /v1/stats"

say() { printf '%s\n' "$*"; }
die() { printf 'deploy: %s\n' "$*" >&2; exit 1; }

check() {
  local ok=1
  say "checking $SITE"
  for p in $PAGES; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$SITE$p" || echo 000)
    if [ "$code" = "200" ]; then
      printf '  %-10s 200\n' "$p"
    else
      printf '  %-10s %s  <-- not 200\n' "$p" "$code"
      ok=0
    fi
  done
  # POST is the one that matters for agents; GET on it is 405 by design
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -X POST "$SITE/v1/query" \
          -H 'Content-Type: application/json' \
          -d '{"service":"api.github.com","operation":"create_issue","error_type":"rate_limit","error_code":"429"}' || echo 000)
  printf '  %-10s %s  (POST)\n' "/v1/query" "$code"
  [ "$code" = "200" ] || ok=0
  say "  real_observations_total: $(curl -s --max-time 15 "$SITE/v1/stats" | grep -o '"real_observations_total":[0-9]*' | cut -d: -f2)"
  [ "$ok" = 1 ] || die "a check failed; see above"
}

if [ "${1:-}" = "--check" ]; then
  check
  exit 0
fi

[ "$(id -u)" = 0 ] || die "run as root (it restarts $UNIT)"

# The tree must be clean before it is moved. Untracked files (backups/) are
# fine; modified tracked files are not.
if sudo -u failecho git -C "$SRV" status --porcelain | grep -q '^ M\|^M '; then
  sudo -u failecho git -C "$SRV" status --porcelain | grep '^ M\|^M ' >&2
  die "the deploy checkout has local modifications; they would be lost. Commit them upstream or discard them deliberately."
fi

before=$(sudo -u failecho git -C "$SRV" rev-parse --short HEAD)
sudo -u failecho git -C "$SRV" fetch -q origin main
sudo -u failecho git -C "$SRV" reset --hard -q origin/main
after=$(sudo -u failecho git -C "$SRV" rev-parse --short HEAD)

if [ "$before" = "$after" ]; then
  say "already at $after; nothing to deploy"
else
  say "deployed $before -> $after"
  sudo -u failecho git -C "$SRV" log --oneline "$before..$after" | sed 's/^/  /'
fi

systemctl restart "$UNIT"
# The service binds and answers /health within a couple of seconds; give it
# up to twenty before calling the deploy bad.
for _ in $(seq 1 10); do
  if [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8000/health)" = "200" ]; then
    break
  fi
  sleep 2
done
systemctl is-active --quiet "$UNIT" || die "$UNIT is not active after restart"
say "$UNIT active, $(systemctl show "$UNIT" -p MemoryCurrent --value | awk '{printf "%d MB", $1/1048576}') resident"

check
