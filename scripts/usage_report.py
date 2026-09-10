"""Who is actually using FailEcho?

    failecho usage           # on the server
    python scripts/usage_report.py --log /var/log/caddy/failecho.log

Four signals, in descending order of what they prove:

1. CONTRIBUTION  real observations and reporters -- someone's agent reported
2. CONSULTATION  queries against the network -- someone's agent asked
3. MCP TRAFFIC   distinct clients hitting /mcp -- someone connected
4. INTEREST      homepage and docs traffic -- someone looked

Only the first two mean the product works. The rest is discovery. The whole
point of separating them is to stop traffic from feeling like adoption.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import urllib.request

DEFAULT_LOG = "/var/log/caddy/failecho.log"
DEFAULT_STATS = "http://127.0.0.1:8000/v1/stats"

#: Clients that are crawling, not using. Counted separately so a directory
#: bot never gets mistaken for an agent.
KNOWN_BOTS = ("bot", "crawler", "spider", "scanner", "curl/", "wget")


def stats(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return json.loads(response.read())
    except Exception as exc:  # noqa: BLE001
        return {"_error": str(exc)}


def read_log(path: pathlib.Path):
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        request = event.get("request", {})
        headers = {k.lower(): v for k, v in request.get("headers", {}).items()}
        entries.append(
            {
                "uri": (request.get("uri") or "").split("?")[0],
                "ip": (headers.get("cf-connecting-ip")
                       or [request.get("remote_ip", "-")])[0],
                "ua": (headers.get("user-agent") or ["-"])[0],
                "status": event.get("status"),
            }
        )
    return entries


def is_bot(user_agent: str) -> bool:
    lowered = user_agent.lower()
    return any(marker in lowered for marker in KNOWN_BOTS)


def main(log_path: str, stats_url: str) -> int:
    data = stats(stats_url)
    entries = read_log(pathlib.Path(log_path))

    print("CONTRIBUTION — did anyone's agent report? (this is the product)")
    if "_error" in data:
        print(f"  stats unavailable: {data['_error']}")
    else:
        for key in (
            "real_observations_24h",
            "real_reporters_24h",
            "real_failures_24h",
            "real_successes_24h",
            "recovery_outcomes_total",
        ):
            print(f"  {key:28} {data.get(key)}")
        if not data.get("real_observations_24h"):
            print("  -> nobody has contributed telemetry yet")

    print()
    print("CONSULTATION — did anyone's agent ask?")
    if "_error" not in data:
        for key in ("known_query_hits_24h", "unknown_query_hits_24h",
                    "known_hit_rate_24h", "cross_agent_help_24h"):
            print(f"  {key:28} {data.get(key)}")

    print()
    print("MCP TRAFFIC — who connected to the endpoint?")
    mcp = [e for e in entries if e["uri"].startswith("/mcp")]
    clients = collections.defaultdict(collections.Counter)
    for entry in mcp:
        clients[entry["ip"]][entry["ua"][:44]] += 1
    humans = {ip: c for ip, c in clients.items()
              if not all(is_bot(ua) for ua in c)}
    print(f"  requests: {len(mcp)}   distinct clients: {len(clients)}"
          f"   excluding crawlers: {len(humans)}")
    for ip, agents in sorted(clients.items(), key=lambda kv: -sum(kv[1].values()))[:8]:
        ua, _ = agents.most_common(1)[0]
        mark = "bot " if is_bot(ua) else "    "
        print(f"    {mark}{sum(agents.values()):>4}x  {ip:<24} {ua}")

    print()
    print("INTEREST — who looked at the site?")
    pages = collections.Counter(
        e["uri"] for e in entries
        if e["uri"] in ("/", "/docs", "/llms.txt", "/openapi.json")
    )
    viewers = {e["ip"] for e in entries if e["uri"] == "/" and not is_bot(e["ua"])}
    print(f"  distinct homepage visitors (excluding crawlers): {len(viewers)}")
    for path, count in pages.most_common():
        print(f"    {count:>4}x  {path}")

    print()
    print("Elsewhere, checked manually:")
    print("  Smithery listing views/uses   https://smithery.ai/server/failecho/failecho")
    print("  GitHub stars and clones       https://github.com/FailEcho/failecho")
    print("  Google impressions            Search Console -> Performance")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", default=DEFAULT_LOG)
    parser.add_argument("--stats", default=DEFAULT_STATS)
    args = parser.parse_args()
    raise SystemExit(main(args.log, args.stats))
