"""End-to-end example: an agent that consults FailEcho before retrying.

Run the server first, then:  python client/example_agent.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from failure_network import Client  # noqa: E402

TOOL = dict(
    service="github-mcp",
    operation="create_issue",
    version="2.8.1",
    schema_hash="a817ce",
)


def main() -> None:
    client = Client("http://localhost:8000", reporter_id="example-agent-1")

    # 1. The tool call failed. Report it.
    ack = client.observe_failure(
        **TOOL,
        error_type="validation_error",
        error_code="422",
        error_message="Repository 918272 was not found",
        latency_ms=421,
    )
    if ack is None:
        print("network unreachable; continuing without intelligence")
        return
    print("observed:", ack)

    # 2. Before retrying, ask what everyone else is seeing.
    intel = client.query(
        **TOOL,
        error_type="validation_error",
        error_code="422",
        error_message="Repository 555812 was not found",
    )
    print("status:", intel["status"], "| known:", intel["known"])
    for action in intel.get("recovery_actions", []):
        print(
            f"  {action['action']:<22} {action['successes']}/{action['attempts']}"
            f"  rate={action['success_rate']:.2%}  confidence={action['confidence']:.2f}"
        )

    # 3. Act on the recommendation, then close the loop.
    recommendation = intel.get("recommendation")
    if recommendation is None:
        print("no recommendation: not enough evidence yet")
        return
    print(f"-> trying {recommendation['action']} (confidence {recommendation['confidence']})")
    client.report_recovery(
        fingerprint=intel["fingerprint"],
        action=recommendation["action"],
        successful=True,
    )
    print("recovery outcome reported")


if __name__ == "__main__":
    main()
