#!/bin/bash
# Restrict origin ports 80/443 to Cloudflare's published ranges.
#
# Why: with Cloudflare proxying failecho.com, anyone who learns the origin IP
# can bypass the CDN entirely -- and with it Cloudflare's rate limiting and DDoS
# protection. Locking the web ports to Cloudflare closes that door.
#
# SSH (22) is deliberately left open to the world. Locking it here is how people
# lose access to their own server.
#
# The ranges change rarely but they do change, so this runs weekly from a timer.
set -euo pipefail

V4=$(curl -fsS --max-time 20 https://www.cloudflare.com/ips-v4)
V6=$(curl -fsS --max-time 20 https://www.cloudflare.com/ips-v6)

# Refuse to touch the firewall on a short or malformed answer: a truncated
# fetch that removed every rule would take the site offline.
count=$(printf '%s\n%s\n' "$V4" "$V6" | grep -c '/' || true)
if [ "$count" -lt 10 ]; then
  echo "refusing to apply: only $count ranges fetched, expected 20+" >&2
  exit 1
fi

# Drop the previous Cloudflare rules (highest number first, so the numbering
# stays valid as we delete).
while true; do
  # `|| true`: an empty grep exits 1, which would abort the script under
  # `set -e -o pipefail` before it ever added a rule.
  num=$(ufw status numbered | grep -i 'cloudflare' | tail -1 \
        | sed 's/^\[ *\([0-9]*\).*/\1/' || true)
  [ -z "$num" ] && break
  yes | ufw delete "$num" >/dev/null
done

# ...and the blanket web rules, if they are still there.
for spec in "80/tcp" "443/tcp"; do
  yes | ufw delete allow "$spec" >/dev/null 2>&1 || true
done

added=0
while read -r cidr; do
  [ -z "$cidr" ] && continue
  ufw allow proto tcp from "$cidr" to any port 80,443 comment 'cloudflare' >/dev/null
  added=$((added + 1))
done <<< "$(printf '%s\n%s\n' "$V4" "$V6")"

echo "origin restricted to $added Cloudflare ranges"
