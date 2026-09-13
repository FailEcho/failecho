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
#: Set when a restart did not fix things. While it exists the watchdog keeps
#: probing and keeps complaining, but never restarts again -- a process that
#: does not come back is not a process problem, and hammering it turns one
#: incident into a restart loop that buries the cause. Cleared automatically
#: when the service answers again, by a reboot (/run is tmpfs), or by hand.
GAVE_UP=/run/failecho-watchdog.gave-up
LIMIT=2

probe() {
  curl -sS --max-time 8 -o /dev/null -w '%{http_code}' \
    --resolve failecho.com:443:127.0.0.1 https://failecho.com/health 2>/dev/null
}

code="$(probe)"
if [[ "$code" == "200" ]]; then
  if [[ -f "$STATE" || -f "$GAVE_UP" ]]; then
    echo "watchdog: healthy again (was failing)"
    rm -f "$STATE" "$GAVE_UP"
  fi
  exit 0
fi

if [[ -f "$GAVE_UP" ]]; then
  # Still down, and a restart has already been tried and failed. Say so every
  # minute so the journal shows the outage continuing, and touch nothing.
  echo "watchdog: still failing, code '${code:-no response}'." \
       "A restart was already tried at $(cat "$GAVE_UP") and did not help." \
       "Not restarting again; clear $GAVE_UP once the cause is understood." >&2
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
    # The marker is what makes that true. Clearing the counter here instead
    # would let it rebuild to the limit and restart again a minute later,
    # which is the opposite of what the line above says.
    date -u '+%Y-%m-%dT%H:%M:%SZ' > "$GAVE_UP"
    rm -f "$STATE"
  fi
fi
exit 0
