"""Two kinds of demo agent, both talking to the network over MCP.

Every agent hits the *same logical failure* with a *different* repository id,
which is what makes the demo honest: nothing but the network's normalizer makes
those messages line up.

Agent kinds:

* **explorer** -- has no network evidence to go on, so it works through its own
  local playbook (retry first, like most agents do, then refresh the schema)
  and reports what happened. Agents A, C, D, E, F are explorers.
* **beneficiary** -- queries the network first and acts on the recommendation
  it gets back, skipping the retry that the explorers already proved useless.
  Agent B is the beneficiary.

Nothing here imports the server. All four network calls go over MCP.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from client.auto_recovery import run_with_failure_intelligence  # noqa: E402
from network import MCPNetworkClient  # noqa: E402
from tool_client import IssueTool, ToolCallError  # noqa: E402

SERVICE = "demo-issues-api"
OPERATION = "create_issue"

#: Actions an explorer tries, in order, when it has nothing better to go on.
DEFAULT_PLAYBOOK = ("retry", "refresh_schema")


def classify_tool_error(exc: BaseException) -> tuple[str, str | None, str]:
    """Map a tool exception onto the network's metadata fields.

    PRIVACY: error type, status code and the server's own message. Never the
    request payload, never the issue text.
    """
    if isinstance(exc, ToolCallError):
        return exc.error_type, str(exc.status_code), exc.message
    return type(exc).__name__, None, str(exc)


def rule(char: str = "-") -> None:
    print(char * 62)


def say(text: str = "") -> None:
    print(text)


@dataclass
class AgentReport:
    name: str
    fingerprint: str = ""
    known_before: bool = False
    recovered: bool = False
    actions_tried: list[tuple[str, bool]] = field(default_factory=list)
    used_network_recommendation: bool = False
    observations_seen: int = 0
    reporters_seen: int = 0


class DemoAgent:
    """One autonomous loop: call tool, fail, learn, recover, report."""

    def __init__(
        self,
        name: str,
        reporter_id: str,
        repository: str,
        *,
        network_url: str,
        tool_url: str,
        trust_network: bool = False,
        playbook: tuple[str, ...] = DEFAULT_PLAYBOOK,
    ) -> None:
        self.name = name
        self.reporter_id = reporter_id
        self.repository = repository
        self.network_url = network_url
        self.tool_url = tool_url
        self.trust_network = trust_network
        self.playbook = playbook

    async def run(self) -> AgentReport:
        report = AgentReport(name=self.name)
        tool = IssueTool(self.tool_url)

        say()
        rule("=")
        say(f"{self.name}   (reporter_id={self.reporter_id}, repository={self.repository})")
        rule("=")

        async with MCPNetworkClient(self.reporter_id, url=self.network_url) as network:
            say()
            say("Calling tool...")

            outcome = await run_with_failure_intelligence(
                tool_call=lambda: tool.create_issue(self.repository, "test"),
                service=SERVICE,
                operation=OPERATION,
                version=tool.schema.version,
                schema_hash=tool.schema.schema_hash,
                network=network,
                classify=classify_tool_error,
            )

            if outcome.ok:
                say("+ tool call succeeded (nothing to learn today)")
                report.recovered = True
                return report

            error = outcome.error
            say("x tool failed")
            say()
            say(f"  {getattr(error, 'status_code', '?')} "
                f"{getattr(error, 'error_type', type(error).__name__)}")
            say(f"  {error}")
            say()
            say("Reporting failure...")
            say("+ accepted")

            decision = outcome.decision
            report.fingerprint = decision.fingerprint
            report.known_before = decision.known
            report.observations_seen = decision.observations
            report.reporters_seen = decision.unique_reporters

            say()
            say("Checking shared failure intelligence...")
            say()
            say(f"  Fingerprint:            {decision.fingerprint}")
            say(f"  Known failure:          {'YES' if decision.known else 'NO'}")
            say(f"  Observed failures:      {decision.observations}")
            say(f"  Independent reporters:  {decision.unique_reporters}")
            say(f"  Service status:         {decision.status}")

            actions = decision.intelligence.get("recovery_actions") or []
            if actions:
                say()
                say("  Recovery actions others reported:")
                for action in actions:
                    say(
                        f"    {action['action']:<22}"
                        f"{action['successes']}/{action['attempts']} "
                        f"({action['success_rate']:.1%}) "
                        f"confidence {action['confidence']:.2f} "
                        f"reporters {action['unique_reporters']}"
                    )

            plan = self._plan(decision, report)
            await self._recover(tool, network, decision.fingerprint, plan, report)

        return report

    # -- decide -----------------------------------------------------------
    def _plan(self, decision, report: AgentReport) -> tuple[str, ...]:
        say()
        if self.trust_network and decision.actionable:
            report.used_network_recommendation = True
            say("Best observed recovery:")
            say(f"  {decision.recommendation}")
            say()
            say(f"  Confidence: {decision.confidence:.2f}")
            if decision.demo_data_included:
                say("  (evidence includes demo data)")
            skipped = [
                a["action"]
                for a in decision.intelligence.get("recovery_actions", [])
                if a["action"] != decision.recommendation and a["success_rate"] < 0.5
            ]
            if skipped:
                say(
                    f"  Skipping {', '.join(skipped)}: "
                    "other agents already proved it does not work here."
                )
            # SAFETY: the wrapper never acts on its own. This agent chooses to.
            return (decision.recommendation,)

        if decision.actionable:
            say(f"Network suggests {decision.recommendation}, but this agent runs its")
            say("own playbook (it is one of the reporters building the evidence).")
        else:
            say("Network has no recommendation yet: not enough evidence.")
            say(f"Falling back to local playbook: {' -> '.join(self.playbook)}")
        return self.playbook

    # -- act --------------------------------------------------------------
    async def _recover(self, tool, network, fingerprint, plan, report) -> None:
        for action in plan:
            say()
            say(f"Applying recovery: {action}")

            if action == "refresh_schema":
                fresh = await tool.refresh_schema()
                say(f"  Refreshed tool schema -> v{fresh.version}, "
                    f"field '{fresh.content_field}'")
            elif action == "retry":
                say("  Retrying with the same cached schema...")
            else:
                say(f"  No handler for '{action}', skipping")
                continue

            say("  Retrying tool call...")
            retry = await run_with_failure_intelligence(
                tool_call=lambda: tool.create_issue(self.repository, "test"),
                service=SERVICE,
                operation=OPERATION,
                version=tool.schema.version,
                schema_hash=tool.schema.schema_hash,
                network=network,
                classify=classify_tool_error,
            )
            succeeded = retry.ok
            report.actions_tried.append((action, succeeded))

            say(f"  {'+ tool call succeeded' if succeeded else 'x still failing'}")
            say()
            say("Reporting recovery outcome...")
            await network.report_recovery_outcome(
                fingerprint=fingerprint, action=action, successful=succeeded
            )
            say(f"+ accepted   ({action} -> {'success' if succeeded else 'failure'})")

            if succeeded:
                report.recovered = True
                return
