#!/usr/bin/env bash
# Publish the OpenCode plugin to npm as `failecho-opencode`.
#
#   npm login          # once, interactive
#   ./scripts/publish_npm_opencode.sh
#
# The first publish claims the package name. A version number is never
# reusable, even after an unpublish.
set -euo pipefail
cd "$(dirname "$0")/../opencode-plugin"

echo "Running the plugin's tests first."
( cd .. && .venv/bin/python -m pytest -q tests/test_opencode_plugin.py )

echo
echo "Files that would ship:"
npm pack --dry-run

echo
npm whoami >/dev/null 2>&1 || { echo "Not logged in. Run: npm login" >&2; exit 1; }
echo "Publishing as: $(npm whoami)"

read -r -p "Publish failecho-opencode $(node -p 'require("./package.json").version') to npm? [y/N] " ok
[[ "$ok" == "y" || "$ok" == "Y" ]] || { echo "aborted"; exit 1; }

npm publish --access public

echo
echo "Published. Then tell Claude: the npm line goes back on /setup, llms.txt"
echo "and the READMEs, which today offer only the single-file install."
