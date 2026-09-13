"""Build `failecho-mcp` as its own PyPI distribution.

The repository ships as `failecho-server`, which depends on FastAPI,
SQLAlchemy and uvicorn because it *is* the server. The stdio relay needs none
of that -- it is one module that speaks MCP and forwards over HTTP -- and
somebody running `uvx failecho-mcp` should not install a web framework to get
it.

Rather than keep a second copy of the source in the tree, this assembles the
distribution in a temporary directory from the canonical `failecho_mcp/`, so
there is exactly one place the relay is edited.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "failecho_mcp"
DIST = ROOT / "dist"

PYPROJECT = '''\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "failecho-mcp"
version = "{version}"
description = "Stdio MCP server that relays to the FailEcho network: check what other agents hit before retrying a failed tool."
readme = "README.md"
requires-python = ">=3.11"
license = {{ text = "MIT" }}
keywords = ["mcp", "model-context-protocol", "agents", "reliability", "retries"]
dependencies = ["mcp>=2.0"]

[project.urls]
Homepage = "https://failecho.com"
Documentation = "https://failecho.com/setup"
Source = "https://github.com/FailEcho/failecho"

[project.scripts]
failecho-mcp = "failecho_mcp:main"

[tool.hatch.build.targets.wheel]
packages = ["failecho_mcp"]
'''

README = '''\
# failecho-mcp

Stdio MCP server that relays to [FailEcho](https://failecho.com): before your
agent retries a failed tool, check what other agents already tried and whether
it worked.

For hosts that can only start a local process. If your client speaks
Streamable HTTP, point it straight at `https://failecho.com/mcp` instead --
this package exists for the ones that cannot.

```bash
uvx failecho-mcp
```

```json
{
  "mcpServers": {
    "failecho": { "command": "uvx", "args": ["failecho-mcp"] }
  }
}
```

Four tools: `check_tool_failure` before a retry, and `report_tool_failure`,
`report_tool_success`, `report_recovery_outcome` to contribute. No account, no
API key. Set `FAILECHO_URL` to relay to your own server instead.

The relay stores nothing itself. MIT.
'''


def version() -> str:
    text = (SOURCE / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', text, re.M)
    if not match:
        raise SystemExit("no __version__ in failecho_mcp/__init__.py")
    return match.group(1)


def main() -> int:
    v = version()
    with tempfile.TemporaryDirectory() as tmp:
        build = Path(tmp)
        shutil.copytree(
            SOURCE, build / "failecho_mcp",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        (build / "pyproject.toml").write_text(PYPROJECT.format(version=v), encoding="utf-8")
        (build / "README.md").write_text(README, encoding="utf-8")
        DIST.mkdir(exist_ok=True)
        result = subprocess.run(
            [sys.executable, "-m", "build", "--outdir", str(DIST), str(build)],
            check=False,
        )
        if result.returncode:
            return result.returncode
    print(f"\nbuilt failecho-mcp {v} into {DIST}")
    print("publish with:  python -m twine upload dist/failecho_mcp-*")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
