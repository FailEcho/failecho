#!/usr/bin/env python3
"""Ask vs blind: run the comparison yourself.

    python scripts/ask_vs_blind.py --endpoint https://lab.failecho.com

Standard library only. Two cohorts of small agents do the same work against
httpbingo.org, a public service that returns whatever status you ask for:

    /status/200,200,503   fails one time in three; a retry usually works
    /status/503           fails every time; nothing works
    /delay/10             answers after 10 s; with a 3 s client timeout, never

One cohort retries blind, once, on any failure -- the default most agents
ship with. The other asks FailEcho first and does what it says: retry,
back off, or `skip` when the network reports that nothing anyone tried
recently has worked. A handful of explorer agents run first so the network
has something to say; they report their failures and what happened when
they retried, exactly as any agent would.

What is printed is what happened: calls, failures, attempts per failure,
seconds spent, successes after recovery, for each cohort. The endpoints are
synthetic by design -- they show the mechanism, not how often real APIs
fail. Nothing here is faked: every number is a real HTTP call.

The default endpoint is FailEcho's lab instance, which exists for exactly
this. Point it at a self-hosted FailEcho if you would rather nothing leave
your network. Do not point it at https://failecho.com: your run would
count as real adoption there, and a benchmark is not adoption.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import random
import sys
import time
import urllib.error
import urllib.request

TARGETS = [
    ("flaky_read", "https://httpbingo.org/status/200,200,503", 20),
    ("always_broken", "https://httpbingo.org/status/503", 20),
    ("slow_read", "https://httpbingo.org/delay/10", 3),
]
SERVICE = "httpbingo.org"
UA = "failecho-ask-vs-blind/0.1 (+https://failecho.com)"


def classify(exc: BaseException) -> tuple[str, str | None]:
    if isinstance(exc, urllib.error.HTTPError):
        code = str(exc.code)
        return ("rate_limit" if exc.code == 429 else "server_error" if exc.code >= 500 else "error"), code
    if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
        return "timeout", None
    return "connection_error", None


class Agent:
    def __init__(self, endpoint: str, reporter: str, mode: str):
        self.endpoint, self.reporter, self.mode = endpoint.rstrip("/"), reporter, mode
        self.calls = self.failures = self.attempts = self.recovered = self.skipped = 0
        self.seconds = 0.0

    # -- the network ---------------------------------------------------------

    def _post(self, path: str, body: dict) -> dict | None:
        req = urllib.request.Request(f"{self.endpoint}{path}", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": UA,
                                              "X-Reporter-ID": self.reporter})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.load(r)
        except Exception:  # noqa: BLE001 - the network being down never breaks the agent
            return None

    def observe(self, operation: str, outcome: str, error_type: str | None = None, code: str | None = None) -> str | None:
        body = {"service": SERVICE, "operation": operation, "outcome": outcome}
        if error_type:
            body["error_type"] = error_type
        if code:
            body["error_code"] = code
        answer = self._post("/v1/observe", body)
        return (answer or {}).get("fingerprint")

    def ask(self, operation: str, error_type: str, code: str | None) -> dict | None:
        body = {"service": SERVICE, "operation": operation, "error_type": error_type}
        if code:
            body["error_code"] = code
        return self._post("/v1/query", body)

    def outcome(self, fingerprint: str | None, action: str, ok: bool) -> None:
        if fingerprint:
            self._post("/v1/outcome", {"fingerprint": fingerprint, "action": action, "successful": ok})

    # -- the work -------------------------------------------------------------

    def fetch(self, url: str, timeout: int) -> None:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()

    def run(self, rounds: int) -> None:
        for _ in range(rounds):
            for operation, url, timeout in TARGETS:
                started = time.monotonic()
                self.calls += 1
                try:
                    self.fetch(url, timeout)
                    self.observe(operation, "success")
                except Exception as exc:  # noqa: BLE001
                    self.failures += 1
                    self.attempts += 1
                    et, code = classify(exc)
                    fp = self.observe(operation, "failure", et, code)
                    action = "retry"
                    if self.mode == "ask":
                        advice = self.ask(operation, et, code) or {}
                        rec = (advice.get("recommendation") or {}).get("action")
                        if rec == "skip":
                            self.skipped += 1
                            self.seconds += time.monotonic() - started
                            continue
                        if rec in ("retry", "backoff"):
                            action = rec
                    if action == "backoff":
                        time.sleep(2)
                    self.attempts += 1
                    try:
                        self.fetch(url, timeout)
                        self.recovered += 1
                        self.outcome(fp, action, True)
                    except Exception:  # noqa: BLE001
                        self.outcome(fp, action, False)
                self.seconds += time.monotonic() - started


def cohort(endpoint: str, mode: str, n: int, rounds: int, tag: str) -> list[Agent]:
    agents = [Agent(endpoint, f"bench-{tag}-{mode}-{i}-{random.randrange(10**6)}", mode) for i in range(n)]
    with cf.ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(lambda a: a.run(rounds), agents))
    return agents


def summarise(name: str, agents: list[Agent]) -> dict:
    calls = sum(a.calls for a in agents)
    failures = sum(a.failures for a in agents)
    attempts = sum(a.attempts for a in agents)
    return {"cohort": name, "agents": len(agents), "calls": calls, "failures": failures,
            "attempts_per_failure": round(attempts / failures, 2) if failures else None,
            "recovered": sum(a.recovered for a in agents), "skipped": sum(a.skipped for a in agents),
            "seconds": round(sum(a.seconds for a in agents), 1)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--endpoint", default="https://lab.failecho.com", help="a FailEcho instance (not production)")
    ap.add_argument("--agents", type=int, default=4, help="agents per cohort")
    ap.add_argument("--rounds", type=int, default=3, help="rounds over the three targets per agent")
    ap.add_argument("--explorers", type=int, default=6, help="agents that seed the evidence first (0 to skip)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if "failecho.com" in args.endpoint and "lab." not in args.endpoint:
        print("refusing: that is the production network; a benchmark is not adoption. Use the lab or a self-hosted instance.")
        return 2
    tag = f"{int(time.time()) % 100000}"
    print(f"endpoint {args.endpoint}; targets: {', '.join(t[0] for t in TARGETS)}", file=sys.stderr)
    if args.explorers:
        print(f"seeding: {args.explorers} explorers, 1 round each, retrying blind and reporting outcomes...", file=sys.stderr)
        cohort(args.endpoint, "blind", args.explorers, 1, tag + "-seed")
        time.sleep(2)   # the network's dashboard cache; queries are live
    print(f"measuring: {args.agents} blind agents and {args.agents} asking agents, {args.rounds} rounds each...", file=sys.stderr)
    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        blind_f = pool.submit(cohort, args.endpoint, "blind", args.agents, args.rounds, tag)
        ask_f = pool.submit(cohort, args.endpoint, "ask", args.agents, args.rounds, tag)
        blind, ask = blind_f.result(), ask_f.result()
    rows = [summarise("blind", blind), summarise("ask", ask)]
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    print()
    head = f"{'cohort':<8}{'agents':>7}{'calls':>7}{'failures':>10}{'attempts/failure':>18}{'recovered':>11}{'skipped':>9}{'seconds':>9}"
    print(head)
    print("-" * len(head))
    for r in rows:
        apf = "-" if r["attempts_per_failure"] is None else f"{r['attempts_per_failure']:.2f}"
        print(f"{r['cohort']:<8}{r['agents']:>7}{r['calls']:>7}{r['failures']:>10}{apf:>18}{r['recovered']:>11}{r['skipped']:>9}{r['seconds']:>9}")
    print()
    print("attempts/failure: retries spent per failure (1.00 = never retried). seconds: wall time inside the")
    print("calls, including timeouts. The asking cohort skips what the network says nothing fixes and keeps")
    print("retrying what it says a retry fixes. The targets are synthetic; the calls, and the numbers, are real.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
