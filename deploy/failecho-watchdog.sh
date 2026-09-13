#!/usr/bin/env bash
# Is FailEcho actually answering, end to end?
#
# `Restart=always` covers a process that dies. It does not cover one that is
# alive and wedged -- a deadlocked event loop, a database lock nobody releases
# -- which looks perfectly healthy to systemd and completely broken to a user.
#
# So this asks the way a user does: through Caddy, over TLS, on the real
# hostname. Two consecutive failures before acting, because one timeout is
# usually the network and restarting on it would be its own outage.
set -uo pipefail

STATE=/run/failecho-watchdog.fails
LIMIT=2

probe() {
  curl -sS --max-time 8 -o /dev/null -w '%{http_code}' \
    --resolve failecho.com:443:127.0.0.1 https://failecho.com/health 2>/dev/null
}

code="$(probe)"
if [[ "$code" == "200" ]]; then
  [[ -f "$STATE" ]] && { echo "watchdog: healthy again (was failing)"; rm -f "$STATE"; }
  exit 0
fi

fails=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
echo "$fails" > "$STATE"
echo "watchdog: /health returned '${code:-no response}' (failure $fails of $LIMIT)"

if (( fails >= LIMIT )); then
  echo "watchdog: restarting failecho after $fails consecutive failures"
  systemctl restart failecho
  sleep 5
  after="$(probe)"
  if [[ "$after" == "200" ]]; then
    echo "watchdog: recovered after restart"
    rm -f "$STATE"
  else
    # Loud, and left in the journal: a restart that does not fix it means the
    # problem is not the process, and a second restart will not help either.
    echo "watchdog: STILL FAILING after restart, code '${after:-no response}'" >&2
    echo "watchdog: not restarting again; this needs a person" >&2
    rm -f "$STATE"
  fi
fi
exit 0
