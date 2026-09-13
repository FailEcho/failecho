#!/usr/bin/env bash
# Publish the Node relay to npm.
#
#   npm login          # once, interactive
#   ./scripts/publish_npm_relay.sh
#
# npm allows unpublishing within 72 hours, but a version number is never
# reusable afterwards, and the package name is claimed on first publish.
set -euo pipefail
cd "$(dirname "$0")/../npm-relay"

echo "Running the relay's tests first."
( cd .. && .venv/bin/python -m pytest -q tests/test_npm_relay.py )

echo
echo "Files that would ship:"
npm pack --dry-run

echo
npm whoami >/dev/null 2>&1 || { echo "Not logged in. Run: npm login" >&2; exit 1; }
echo "Publishing as: $(npm whoami)"

read -r -p "Publish failecho-mcp to npm? [y/N] " ok
[[ "$ok" == "y" || "$ok" == "Y" ]] || { echo "aborted"; exit 1; }

npm publish --access public

echo
echo "Published. Verify from a clean machine:"
echo "    npx -y failecho-mcp"
echo
echo "Then tell Claude, and the npx path goes on /setup and into the README."
