"""Build `failecho-autoreport` as its own PyPI distribution.

Same shape as build_relay_package.py, for the same reason: the repository
ships as `failecho-server` with FastAPI and SQLAlchemy behind it, and the
autoreport wrapper depends on nothing at all. Someone adding one decorator to
a LangChain tool should not install a web framework, and should not install
the MCP SDK either -- which is why this is not folded into `failecho-mcp`.

The distribution is assembled in a temporary directory from the canonical
`failecho_autoreport/`, so the wrapper is edited in exactly one place and the
published wheel is reproducible from the repository.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "failecho_autoreport"
DIST = ROOT / "dist"

PYPROJECT = '''\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "failecho-autoreport"
version = "{version}"
description = "Report the shape of your agent's tool failures automatically -- no model in the loop, no dependencies, never raises, never blocks."
readme = "README.md"
requires-python = ">=3.11"
license = {{ text = "MIT" }}
keywords = ["agents", "reliability", "retries", "langchain", "llamaindex", "mcp", "observability"]
dependencies = []

[project.urls]
Homepage = "https://failecho.com"
Documentation = "https://failecho.com/setup"
Source = "https://github.com/FailEcho/failecho"

[tool.hatch.build.targets.wheel]
packages = ["failecho_autoreport"]
'''

README = '''\
# failecho-autoreport

Automatic failure reporting for agents that are not Claude Code.

Claude Code reports tool failures through a hook that fires after every call,
with no model deciding anything. Everywhere else -- LangChain, LlamaIndex, a
cron job, a local model driving a scraper -- reporting depends on the model
choosing to call a tool, and a small model reliably will not. This removes the
decision: wrap the call site once.

```python
from failecho_autoreport import FailEcho

fe = FailEcho()

tools = fe.wrap(tools, service="github-mcp")          # a framework's tool list

@fe.watch(service="api.github.com", operation="create_issue")
def create_issue(title):                              # or one call site
    ...

fe.recovered("api.github.com", "create_issue", "refresh_schema")  # what fixed it
```

What it sends is a failure's *shape*: service, operation, a short error
class, an HTTP-ish code when there is one, and duration. Never arguments,
never return values, never the prompt, never a credential. Error text is off
unless `FAILECHO_SEND_ERRORS=1`.

Three properties it has, each with a test rather than a promise:

- **Never raises.** A reporting bug cannot become your application's exception.
- **Never blocks.** Reports go to a worker thread; an unreachable FailEcho costs
  the caller about 3 ms, not a timeout. (Advice, below, is the opt-in exception.)
- **Never changes behaviour.** Returns and re-raises exactly what your code did.

## Advice for the agent that failed (opt-in)

Reporting helps the next agent. With `advise=True` (or `FAILECHO_ADVISE=1`),
a failure in a watched call is also followed by one read of the network --
`/v1/query`, which stores nothing -- and the answer is attached to the
exception before it goes on up:

```python
fe = FailEcho(advise=True)

try:
    create_issue("...")
except Exception as exc:
    exc.failecho          # the network's answer, a dict, or None
    fe.advice_text(exc)   # one line, e.g. "FailEcho: try <action>, worked n/m ..."
```

Put that line in the tool error your model sees and the model has the
evidence when it decides whether to retry, without having to think of
asking. Nothing is acted on for you; the exception's type and message are
unchanged (on Python 3.11+ the line is also added as a note). The read waits
at most 3 seconds, on failures only -- the one place this package waits,
which is why it is off by default.

The `recovered()` line is the one worth bothering with. Failures alone give
the network a failure *rate*; only an outcome records what fixed it, which is
the half another agent can act on. It cannot be inferred, so it stays one
explicit call.

Zero dependencies. `FAILECHO_DISABLED=1` turns it off. Point `endpoint=` at
your own server to send nothing to anyone else. The network at failecho.com
is public and, as of this release, has no independent reporters yet -- the
front page says so. Works on
your own history alone from five recoveries.

MIT. Source: https://github.com/FailEcho/failecho
'''


def version() -> str:
    text = (SOURCE / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', text, re.M)
    if not match:
        raise SystemExit("no __version__ in failecho_autoreport/__init__.py")
    return match.group(1)


def main() -> int:
    v = version()
    with tempfile.TemporaryDirectory() as tmp:
        build = Path(tmp)
        shutil.copytree(
            SOURCE, build / "failecho_autoreport",
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
    print(f"\nbuilt failecho-autoreport {v} into {DIST}")
    print("publish with:  python -m twine upload dist/failecho_autoreport-*")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
