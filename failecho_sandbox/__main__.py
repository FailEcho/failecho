"""python -m failecho_sandbox {selftest|exec|status}

    selftest   boot a VM and prove the fence holds, before any persona uses it
    exec       run one Python snippet or file inside a fresh VM, print the result
    status     what is installed and whether a VM could boot right now
"""

from __future__ import annotations

import json
import sys
import time

from . import Sandbox, SandboxError, available

# Each check is code the guest runs; the expectation is on the host. A check
# that "passes" by the guest lying is not possible: stdout is all it can say,
# and the host decides what counts.
CHECKS = [
    ("python runs", "print('alive')", lambda r: r.ok and "alive" in r.stdout),
    ("root is read-only",
     "import os\n"
     "try:\n    open('/usr/bin/marker', 'w').write('x'); print('WROTE')\n"
     "except OSError as e: print('denied', e.errno)",
     lambda r: "denied" in r.stdout and "WROTE" not in r.stdout),
    ("runs unprivileged", "import os; print(os.getuid())", lambda r: r.stdout.strip() == "1000"),
    ("no secrets in the environment",
     "import os; bad=[k for k in os.environ if any(s in k.upper() for s in ('KEY','TOKEN','SECRET','PASSWORD'))]; print(bad)",
     lambda r: r.stdout.strip() == "[]"),
    ("no route to the internet",
     "import socket\ns=socket.socket(); s.settimeout(4)\n"
     "try:\n    s.connect(('1.1.1.1', 443)); print('CONNECTED')\n"
     "except OSError as e: print('blocked', type(e).__name__)",
     lambda r: "blocked" in r.stdout and "CONNECTED" not in r.stdout),
    ("proxy refuses hosts off the list",
     "import urllib.request, urllib.error\n"
     "try:\n    urllib.request.urlopen('https://example.com', timeout=15); print('FETCHED')\n"
     "except urllib.error.HTTPError as e: print('http', e.code)\n"
     "except Exception as e: print('err', type(e).__name__, e)",
     lambda r: "FETCHED" not in r.stdout and ("403" in r.stdout or "Tunnel connection failed" in r.stdout)),
    ("proxy allows the package index",
     "import urllib.request, json\n"
     "d=json.load(urllib.request.urlopen('https://pypi.org/pypi/requests/json', timeout=20)); print('version', d['info']['version'])",
     lambda r: r.ok and "version" in r.stdout),
    ("pip installs through the proxy",
     None, None),   # special-cased below: argv, not python
    ("a timeout is enforced", "import time; time.sleep(30)", lambda r: r.get("timed_out") is True),
]


def selftest() -> int:
    why = available()
    if why:
        print(f"sandbox unavailable: {why}")
        return 2
    failed = 0
    t0 = time.monotonic()
    with Sandbox() as vm:
        print(f"booted in {vm.boot_seconds}s")
        for name, code, expect in CHECKS:
            if code is None:
                r = vm.run(["python3", "-m", "pip", "install", "--quiet", "--target", "/work/pkgs", "cowsay==6.1"], timeout=90)
                ok = r.ok
            elif name == "a timeout is enforced":
                r = vm.python(code, timeout=3)
                ok = expect(r)
            else:
                r = vm.python(code, timeout=40)
                ok = expect(r)
            failed += 0 if ok else 1
            print(f"  {'PASS' if ok else 'FAIL'}  {name}  ({r.get('seconds')}s)")
            if not ok:
                print(f"        exit={r.exit} stdout={r.stdout.strip()[-200:]!r} stderr={r.stderr.strip()[-300:]!r}")
    print(f"{'all checks passed' if not failed else f'{failed} check(s) FAILED'} in {time.monotonic() - t0:.1f}s")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "status":
        why = available()
        print("ready" if not why else f"not ready: {why}")
        return 0 if not why else 1
    if cmd == "selftest":
        try:
            return selftest()
        except SandboxError as e:
            print(f"sandbox error: {e}")
            return 2
    if cmd == "exec":
        src = argv[1] if len(argv) > 1 else "-"
        code = sys.stdin.read() if src == "-" else (open(src).read() if src.endswith(".py") else src)
        try:
            with Sandbox() as vm:
                r = vm.python(code, timeout=120)
        except SandboxError as e:
            print(f"sandbox error: {e}")
            return 2
        print(json.dumps(r, indent=2))
        return 0 if r.ok else 1
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
