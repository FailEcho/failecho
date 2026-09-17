"""The one-shot surface.

    python -m failecho_autoreport run my_agent.py [args...]
        Run a script with every outbound HTTP call observed. No code changes.

    python -m failecho_autoreport check <service> <operation> [error_type] [code]
        Ask the network what it knows about a failure, and print the answer
        the way an agent would read it. Stores nothing.
"""

from __future__ import annotations

import json
import os
import runpy
import sys
import urllib.request

from . import FailEcho, __version__

USAGE = __doc__


def _check(argv: list[str]) -> int:
    if len(argv) < 2:
        print(USAGE, file=sys.stderr)
        return 2
    service, operation = argv[0], argv[1]
    body = {"service": service, "operation": operation}
    if len(argv) > 2:
        body["error_type"] = argv[2]
    if len(argv) > 3:
        body["error_code"] = argv[3]
    endpoint = (os.environ.get("FAILECHO_ENDPOINT") or "https://failecho.com").rstrip("/")
    req = urllib.request.Request(
        f"{endpoint}/v1/query", data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "User-Agent": f"failecho-autoreport/{__version__}",
                 # optional; with it, from_other_agents stops being null
                 **({"X-Reporter-ID": os.environ["FAILECHO_REPORTER_ID"]}
                    if os.environ.get("FAILECHO_REPORTER_ID") else {})},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.load(r)

    print(f"{service} {operation}" + (f"  {body.get('error_type', '')} {body.get('error_code', '')}".rstrip()))
    print(f"  known: {d['known']}   status: {d['status']}   observations: {d['observations']['total']}"
          f"   reporters: {d['observations']['unique_reporters']}")
    fr = d["failure_rate"]
    print(f"  failure rate: 5m {fr['last_5m']}  1h {fr['last_1h']}")
    if d["recovery_actions"]:
        print("  recovery evidence:")
        for a in d["recovery_actions"]:
            flag = "  <- decaying" if a.get("decaying") else ""
            print(f"    {a['action']:<18} {a['successes']}/{a['attempts']}  "
                  f"reporters {a['unique_reporters']}  confidence {a['confidence']}{flag}")
    rec = d["recommendation"]
    if rec:
        print(f"  recommendation: {rec['action']}  (confidence {rec['confidence']}, "
              f"from other agents: {rec['from_other_agents']})")
        if rec.get("warning"):
            print(f"    warning: {rec['warning']}")
    else:
        print("  recommendation: none -- not enough evidence yet")
    if d.get("related_failures"):
        print("  other shapes on this operation that something fixed:")
        for r_ in d["related_failures"]:
            fixes = ", ".join(f"{f['action']} {f['successes']}/{f['attempts']}" for f in r_["fixed_by"])
            share = "  (shares a fix with yours)" if r_["shares_a_fix_with_you"] else ""
            print(f"    {r_['error_type']}/{r_['error_code']}: {fixes}{share}")
    ev = d.get("success_evidence")
    if ev and not ev["verified"]:
        print(f"  note: {ev['successes_total']} successes and no failure ever on a write-shaped "
              f"operation -- reported as unverified, not as success")
    return 0


def _run(argv: list[str]) -> int:
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2
    from . import auto

    patched = auto.enable()  # nothing is patched until this line
    print(f"[failecho] observing outbound HTTP via {', '.join(patched)}; "
          f"reporting to {auto.client().endpoint}", file=sys.stderr)
    script, args = argv[0], argv[1:]
    sys.argv = [script, *args]
    sys.path.insert(0, os.path.dirname(os.path.abspath(script)) or ".")
    try:
        runpy.run_path(script, run_name="__main__")
    finally:
        fe = auto.client()
        fe.flush(timeout=10)
        calls = fe.sent - fe.sent_outcomes
        print(f"[failecho] reported {calls} call{'s' if calls != 1 else ''}"
              + (f" ({fe.queued_failures} failure{'s' if fe.queued_failures != 1 else ''})" if fe.queued_failures else "")
              + (f", {fe.sent_outcomes} recovery outcome{'s' if fe.sent_outcomes != 1 else ''}"
                 + (" inferred" if fe.inferred else "") if fe.sent_outcomes else "")
              + (f", {fe.failed} could not be sent" if fe.failed else "")
              + (f", {fe.unmatched} outcome{'s' if fe.unmatched != 1 else ''} unmatched" if fe.unmatched else "")
              + (f", {fe.dropped} dropped" if fe.dropped else ""), file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0
    if argv[0] == "run":
        return _run(argv[1:])
    if argv[0] == "check":
        return _check(argv[1:])
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
