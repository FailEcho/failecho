#!/usr/bin/env bash
# Publish the failecho-mcp relay to PyPI.
#
# Needs a PyPI API token. Get one at https://pypi.org/manage/account/token/
# scoped to the failecho-mcp project (or "entire account" for the first
# upload, since the project does not exist yet), then:
#
#   export TWINE_USERNAME=__token__
#   export TWINE_PASSWORD=pypi-AgEIcHl...
#   ./scripts/publish_relay.sh
#
# Do not put the token in a file in this repository.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -z "${TWINE_PASSWORD:-}" ]]; then
  echo "TWINE_PASSWORD is not set. See the header of this script." >&2
  exit 1
fi
: "${TWINE_USERNAME:=__token__}"
export TWINE_USERNAME

rm -f dist/failecho_mcp-*
.venv/bin/python scripts/build_relay_package.py

echo
echo "About to upload:"
ls -1 dist/failecho_mcp-*
echo
.venv/bin/python -m twine check dist/failecho_mcp-*

read -r -p "Upload to PyPI? This cannot be undone or re-uploaded. [y/N] " ok
[[ "$ok" == "y" || "$ok" == "Y" ]] || { echo "aborted"; exit 1; }

.venv/bin/python -m twine upload dist/failecho_mcp-*

echo
echo "Published. Now verify from a clean machine:"
echo "    uvx failecho-mcp"
echo
echo "Then tell Claude, and the uvx path goes on /setup and into the README."
