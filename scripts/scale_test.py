"""Drive a lot of varied failures at a FailEcho and see what it does.

Answers questions the unit tests cannot: does fingerprinting stay sane across
hundreds of distinct signatures, does the query path hold its latency once the
tables are not empty, does the recommendation logic behave the same at volume
as it does with five rows, and how big does the database actually get.

Writes over HTTP like any other client. Refuses a target that holds real or
first-party evidence, for the same reason examples/live_agent/run_demo.py
does: this writes telemetry, and telemetry belongs in a scratch database.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
import urllib.error
import urllib.request

SERVICES = [
    ("github", ["create_issue", "get_file", "search_code"]),
    ("slack", ["post_message", "list_channels"]),
    ("stripe", ["create_refund", "get_charge"]),
    ("filesystem", ["read_text_file", "write_file"]),
    ("fetch", ["fetch"]),
    ("postgres", ["query"]),
    ("brave-search", ["search"]),
    ("puppeteer", ["screenshot", "navigate"]),
]
FAILURES = [
    ("rate_limit", "429", "Rate limit exceeded, retry after 30s"),
    ("auth_error", "401", "Invalid credentials for token abc123"),
    ("forbidden", "403", "Insufficient scope for this resource"),
    ("not_found", "404", "Resource 8823 does not exist"),
    ("validation_error", "422", 'field "body" is no longer accepted, use "content"'),
    ("server_error", "500", "Internal error, request 7f3a-991"),
    ("timeout", "504", "Upstream timed out after 30000ms"),
    ("conflict", "409", "Version mismatch, expected 4 got 3"),
]
# What actually fixes each class, and how reliably. The point is to check the
# scoring separates them, not to claim these are true of any real service.
RECOVERIES = {
    "rate_limit": [("wait_and_retry", 0.9), ("retry", 0.2)],
    "auth_error": [("refresh_token", 0.85), ("retry", 0.05)],
    "forbidden": [("request_scope", 0.4), ("retry", 0.0)],
    "not_found": [("adjust_arguments", 0.7), ("retry", 0.1)],
    "validation_error": [("refresh_schema", 0.95), ("retry", 0.0)],
    "server_error": [("retry", 0.6), ("fallback_provider", 0.8)],
    "timeout": [("retry", 0.5), ("increase_timeout", 0.75)],
    "conflict": [("refetch_and_merge", 0.8), ("retry", 0.15)],
}


def post(url, payload, reporter):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Reporter-ID": reporter,
                 "X-Reporter-Kind": "demo"},
    )
    started = time.perf_counter()
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode())
            return data, (time.perf_counter() - started) * 1000
        except urllib.error.HTTPError as error:
            # The write limiter is 120/min per IP by design. A scale test is
            # measuring the engine, not the doorman, so back off and continue
            # rather than reporting the limiter as a failure.
            if error.code != 429 or attempt == 5:
                raise
            time.sleep(2 ** attempt)
            req = urllib.request.Request(
                url, data=body, method="POST", headers=req.headers)
    raise RuntimeError("unreachable")


def get(url):
    started = time.perf_counter()
    with urllib.request.urlopen(url, timeout=30) as response:
        data = json.loads(response.read().decode())
    return data, (time.perf_counter() - started) * 1000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", default="http://127.0.0.1:8100")
    ap.add_argument("--failures", type=int, default=1000)
    ap.add_argument("--reporters", type=int, default=25)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    rng = random.Random(args.seed)

    stats, _ = get(f"{args.network}/v1/stats")
    if not args.force and (stats["real_observations_total"] or stats["first_party_observations"]):
        raise SystemExit(
            f"Refusing: {args.network} holds real or first-party evidence. "
            "Use a scratch instance, or --force."
        )

    reporters = [f"scale-agent-{i:03d}" for i in range(args.reporters)]
    writes, queries = [], []
    t0 = time.perf_counter()

    for n in range(args.failures):
        service, operations = rng.choice(SERVICES)
        operation = rng.choice(operations)
        etype, code, message = rng.choice(FAILURES)
        reporter = rng.choice(reporters)

        result, ms = post(f"{args.network}/v1/observe", {
            "service": service, "operation": operation, "outcome": "failure",
            "error_type": etype, "error_code": code, "error_message": message,
            "latency_ms": rng.randint(20, 4000),
        }, reporter)
        writes.append(ms)
        fingerprint = result["fingerprint"]

        # Successes too, or every failure rate reads as 100%.
        for _ in range(rng.randint(0, 3)):
            _, ms = post(f"{args.network}/v1/observe", {
                "service": service, "operation": operation, "outcome": "success",
                "latency_ms": rng.randint(10, 400),
            }, reporter)
            writes.append(ms)

        # And what the agent tried next.
        action, rate = rng.choice(RECOVERIES[etype])
        _, ms = post(f"{args.network}/v1/outcome", {
            "fingerprint": fingerprint, "action": action,
            "successful": rng.random() < rate,
        }, reporter)
        writes.append(ms)

        if n % 25 == 0:
            _, ms = post(f"{args.network}/v1/query", {
                "service": service, "operation": operation,
                "error_type": etype, "error_code": code, "error_message": message,
            }, "scale-reader")
            queries.append(ms)

    elapsed = time.perf_counter() - t0

    def q(values, p):
        return statistics.quantiles(values, n=100)[p - 1] if len(values) > 2 else max(values)

    print(f"\n  {len(writes)} writes + {len(queries)} queries in {elapsed:.1f}s "
          f"({(len(writes)+len(queries))/elapsed:.0f} req/s)")
    print(f"  write   p50 {statistics.median(writes):6.1f} ms   p95 {q(writes,95):6.1f} ms   max {max(writes):6.1f} ms")
    print(f"  query   p50 {statistics.median(queries):6.1f} ms   p95 {q(queries,95):6.1f} ms   max {max(queries):6.1f} ms")

    after, _ = get(f"{args.network}/v1/stats")
    print(f"\n  fingerprints        {after['fingerprints_total']}")
    print(f"  observations        {after['observations_total']}")
    print(f"  recovery outcomes   {after['recovery_outcomes_total']}")
    print(f"  real (must be 0)    {after['real_observations_total']}")

    services, ms = get(f"{args.network}/v1/services?limit=8")
    print(f"\n  /v1/services in {ms:.0f} ms, worst first:")
    for row in services[:6]:
        rate = row.get("failure_rate_1h")
        rate = f"{rate:.0%}" if rate is not None else "n/a"
        print(f"    {row['service']:14} {row['operation']:18} {row['status']:16} {rate:>5}")

    echoes, ms = get(f"{args.network}/v1/recovery-intelligence?limit=8")
    print(f"\n  /v1/recovery-intelligence in {ms:.0f} ms:")
    for e in echoes[:8]:
        print(f"    {e['service']:14} {e['operation']:18} {e['action']:18} "
              f"{e['successes']}/{e['attempts']} conf {e['confidence']:.2f} "
              f"reporters {e['unique_reporters']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
