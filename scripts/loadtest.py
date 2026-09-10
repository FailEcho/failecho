"""Load-test FailEcho. Answers one question: does it survive a launch spike?

    python scripts/loadtest.py --base-url http://127.0.0.1:8003 --profile homepage

Profiles:
  homepage   what a visitor costs: page + the three live endpoints it polls
  query      the read path an agent uses before retrying
  observe    the write path (rate limited, so expect 429s by design)
  mixed      everything at once

Run it against a throwaway instance, never against production with writes --
test telemetry would pollute the real counters, which is the one thing this
project refuses to do.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time

import httpx

FAILURE = {
    "service": "loadtest-api",
    "operation": "do_thing",
    "version": "1.0.0",
    "schema_hash": "abc123",
    "error_type": "validation_error",
    "error_code": "422",
}


def requests_for(profile: str, index: int) -> list[tuple[str, str, dict | None]]:
    if profile == "homepage":
        return [
            ("GET", "/", None),
            ("GET", "/v1/stats", None),
            ("GET", "/v1/services", None),
            ("GET", "/v1/recovery-intelligence?limit=3", None),
        ]
    if profile == "query":
        return [("POST", "/v1/query",
                 {**FAILURE, "error_message": f"Widget {100000 + index} was not found"})]
    if profile == "observe":
        return [("POST", "/v1/observe",
                 {**FAILURE, "outcome": "failure",
                  "error_message": f"Widget {100000 + index} was not found"})]
    return (requests_for("homepage", index) + requests_for("query", index)
            + requests_for("observe", index))


async def worker(client, profile, duration, results, stop_at):
    index = 0
    while time.monotonic() < stop_at:
        for method, path, body in requests_for(profile, index):
            started = time.perf_counter()
            try:
                if method == "GET":
                    response = await client.get(path)
                else:
                    response = await client.post(path, json=body)
                results.append((response.status_code, (time.perf_counter() - started) * 1000))
            except Exception:  # noqa: BLE001
                results.append((0, (time.perf_counter() - started) * 1000))
        index += 1


async def run(base_url: str, profile: str, concurrency: int, duration: int) -> int:
    results: list[tuple[int, float]] = []
    limits = httpx.Limits(max_connections=concurrency + 10, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(base_url=base_url, timeout=20.0, limits=limits) as client:
        stop_at = time.monotonic() + duration
        started = time.monotonic()
        await asyncio.gather(*[
            worker(client, profile, duration, results, stop_at) for _ in range(concurrency)
        ])
        elapsed = time.monotonic() - started

    codes: dict[int, int] = {}
    for status, _ in results:
        codes[status] = codes.get(status, 0) + 1
    latencies = sorted(ms for _, ms in results)

    def pct(p: float) -> float:
        return latencies[min(int(len(latencies) * p), len(latencies) - 1)] if latencies else 0.0

    print(f"profile      {profile}")
    print(f"concurrency  {concurrency}")
    print(f"duration     {elapsed:.1f}s")
    print(f"requests     {len(results)}")
    print(f"throughput   {len(results) / elapsed:.0f} req/s")
    print(f"statuses     {dict(sorted(codes.items()))}")
    if latencies:
        print(f"latency ms   p50 {pct(0.50):.0f}   p95 {pct(0.95):.0f}   "
              f"p99 {pct(0.99):.0f}   max {latencies[-1]:.0f}")
        print(f"             mean {statistics.mean(latencies):.0f}")
    errors = codes.get(0, 0) + sum(n for c, n in codes.items() if c >= 500)
    print(f"failures     {errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8003")
    parser.add_argument("--profile", default="homepage",
                        choices=["homepage", "query", "observe", "mixed"])
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--duration", type=int, default=20)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(
        run(args.base_url, args.profile, args.concurrency, args.duration)
    ))
