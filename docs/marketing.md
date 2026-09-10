# FailEcho — approved launch messaging

Everything here is checked against what the product actually does today. If a
claim below stops being true, fix the claim, not the reader's expectations.

**Never claim:** first ever · no competitors · the only network · revolutionary
· industry-leading · AI-powered anything. FailEcho is infrastructure. It should
sound like infrastructure.

---

## Names and one-liners

**Product name**

```
FailEcho
```

**Short description** (GitHub "About", directory listings, meta description)

```
Live failure intelligence for autonomous software.
```

**Tagline**

```
Before you retry, check the echo.
```

**One-sentence pitch**

```
FailEcho lets autonomous systems see whether other agents are hitting the same
tool failure right now — and which recovery actions actually worked.
```

**GitHub repository description** (max ~120 chars)

```
Live cross-agent failure and recovery intelligence for autonomous software.
```

---

## 30-second explanation

```
Agents repeatedly encounter the same API and tool failures in isolation.
FailEcho lets them share privacy-safe failure metadata and recovery outcomes,
turning isolated failures into collective reliability intelligence.
```

Expanded, if someone asks for more:

An agent calls a tool, the tool fails, the agent retries blindly. Somewhere
else, another agent hit the exact same failure ten minutes ago and already
found out that retrying never works and refreshing the tool schema does.
Neither agent can see the other. FailEcho is the shared layer between them:
failures are normalized into a fingerprint, recovery outcomes accumulate
against it, and the next agent to hit that failure gets the evidence instead of
rediscovering it.

**The sentence that does the work:**

```
Agent B benefits from evidence it never generated itself.
```

---

## What it is not

Useful for cutting conversations short:

- not an observability platform — FailEcho holds no traces and no per-tenant dashboards
- not an error database — errors without recovery outcomes are just a feed
- not an uptime monitor — status is derived from what agents actually observed
- not an LLM debugger — no model produces any number FailEcho returns

---

## Proof points

Each of these is verifiable from the repository or the live site:

- **Deterministic confidence.** A Wilson score lower bound over observed
  attempts, recomputable from the attempts and successes returned alongside it.
- **Thin evidence says so.** Below the threshold the answer is
  `INSUFFICIENT_DATA` and `null`.
- **Privacy by schema.** The API has no field for prompts, arguments, results
  or bodies; unknown fields are dropped before storage; raw error text is
  discarded after normalization.
- **Poisoning has a cost.** One reporter contributes at most 5 attempts per
  hour to any recovery action's confidence.
- **Honest counters.** Demo and synthetic telemetry are counted separately and
  never presented as adoption.

---

## Social launch post (developer-focused)

```
FailEcho — live failure intelligence for autonomous software.

Your agent hits a 422 from a tool. It retries. It fails again. Somewhere else,
five other agents hit the same failure an hour ago and already found out that
retrying never works, and refreshing the stale tool schema does.

FailEcho is the layer between them. Agents report tool failures, successes and
recovery outcomes; the network normalizes failures into a shared fingerprint
and tells the next agent what actually worked.

MCP endpoint: https://failecho.com/mcp
No account, no API key, free during the MVP.

Confidence is a Wilson lower bound over observed attempts — no model generates
it, and thin evidence returns INSUFFICIENT_DATA rather than a guess. Metadata
only: no prompts, arguments, results or bodies.
```

---

## Hacker News

**Title**

```
Show HN: FailEcho – shared failure intelligence for AI agents
```

**First comment** (context, not a pitch)

```
I kept watching agents burn retries on failures that could never succeed — a
renamed API field, a stale tool schema, a permission change. Every agent
rediscovers these alone, and the fix is often not "retry".

FailEcho is a small shared network for that. An agent reports a tool failure;
the server normalizes the message (identifiers stripped, secrets redacted) into
a fingerprint; other agents report what recovery action they tried and whether
it worked. The next agent to hit that fingerprint gets the evidence.

Deliberately boring stack: FastAPI, SQLite in WAL mode, one process, ~100 MB
RAM on a small VPS. No accounts, no keys, free.

Two design decisions I'd defend:

- Confidence is a Wilson score lower bound over observed attempts, capped, and
  discounted when fewer than three distinct reporters back it. No model
  produces it, and you can recompute it from the returned counts.
- Successes are collected as well as failures. 100 failures out of 200 calls
  and 100 out of 1,000,000 are very different, and a network that only hears
  about failures cannot tell them apart.

Open questions I'd genuinely like input on: how much cross-tenant failure
overlap really exists outside popular public APIs, and whether the recovery
outcome reports (the fragile part — an agent has to come back after it
recovered) actually arrive in practice.
```

---

## Reddit (r/LocalLLaMA, r/AI_Agents) — technical, not promotional

```
Sharing something small I built: a shared failure network for agents.

The problem: agents hit the same tool/API failures independently and retry
blindly. A retry is the right move for a 503 and useless for a renamed field.
No single agent has enough information to tell those apart from one error.

The approach: agents report failure metadata (service, operation, version,
schema hash, error type/code, normalized message, latency) plus successes and
recovery outcomes. The server normalizes error text deterministically — regex
only, no model — so "Repository 918272 was not found" and "Repository 555812
was not found" collapse to one fingerprint. Recovery outcomes accumulate
against that fingerprint.

When an agent queries, it gets: how many observations, how many independent
reporters, the failure rate for that service/operation, every recovery action
others tried with its success rate, and one recommendation when the evidence
clears a threshold. Otherwise INSUFFICIENT_DATA.

Available over MCP (four tools) and REST. FastAPI + SQLite, one process.
Metadata only — no prompts, arguments, results or bodies, and the schemas have
nowhere to put them.

It's live at failecho.com and the network is currently near-empty, which is the
honest state of a network effect on day one. Interested in whether the failure
overlap between unrelated agents is real outside the obvious cases.
```

---

## MCP directory listing

**Name:** `failecho`
**Title:** FailEcho
**Description:**

```
Check whether other autonomous systems recently hit the same tool failure, and
which recovery actions actually worked. Report failures, successes and recovery
outcomes. No account or API key required.
```

**Tools:** `check_tool_failure`, `report_tool_failure`, `report_tool_success`,
`report_recovery_outcome`
**Endpoint:** `https://failecho.com/mcp` (Streamable HTTP)

---

## Tone notes

- Lead with the failure, not the technology. Developers recognise the 422.
- Say what it does not do. It buys more credibility than any superlative.
- Never present demo data as adoption. The site enforces this; the copy must too.
- When the network is empty, say so. "Near-empty on day one" is more persuasive
  than a fabricated number, and it invites the reader to be the first reporter.
