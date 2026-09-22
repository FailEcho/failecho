# How FailEcho could make money, and what has to be true first

Written 22 September 2026, after the outside review and the first releases.
This is an argument, not a plan of record. Every number in it that comes from
the lab is our own agents' (see `docs/report-48h.md`); production has **0
independent reporters**, and that single fact decides the order of everything
below.

## What exists already

The architecture people sketch for this product is mostly built:
ingestion with auth and rate limits, schema validation, privacy and
normalization, fingerprints, the recovery engine, confidence scoring, the
query API, and the three client paths (Claude Code plugin, the Python wrapper,
the MCP proxy). The pieces that are missing are the ones that would make the
data sellable:

| Missing piece | What it buys |
|---|---|
| **Reporter reputation and provenance** | Any answer to "why should I trust this number?" Today reporter ids are self-chosen and a restart mints a new one, so "unique reporters" counts processes |
| **Incident detection** | "42 agents hit this in the last 10 minutes" -- the most shareable free feature, and the basis of a status feed |
| **Reliability history** | Provider comparisons over time, which is the thing a router would pay for |

An event bus or queue is *not* missing. This project runs one uvicorn process
and SQLite on a small VPS by deliberate constraint; those stages stay functions
in one process until there is traffic that justifies otherwise.

## What must not be sold

Contributors send failure metadata under a plain promise: metadata only, no
prompts, arguments, results or bodies; reads store nothing; error text off by
default. **Selling their records, even aggregated, breaks that promise** unless
the terms said so before the data arrived -- and they did not. What can be sold
is what the network *computes* from the data, which no single contributor owns:
reliability, incidents, recovery evidence.

The distinction is not legal hair-splitting. The product's only real asset is
that its numbers are believable. Adoption counters that stay at zero, lab
results published with their losing cohorts, a review's findings written down
in full: that is the asset. Anything that makes a number worth inflating costs
more than it earns.

## Why paying for error data is the wrong first move

It is the obvious idea and it fails on its own terms:

1. **It pays for what cannot be verified.** Once data is worth money, the
   cheapest way to earn is to invent plausible failures and confirm plausible
   recoveries. Recovery rates are computed from exactly that input.
2. **Buyers will ask how you stop it.** For a reliability feed, "we pay
   contributors per report" is the wrong answer to "how do you know this is
   real?".
3. **It solves the wrong shortage.** The shortage is not volume; it is
   *trusted* volume from agents doing real work.

If contributors are ever paid, pay per **verified recovery** -- failure and fix
both confirmed by independent reporters, capped per reporter, paid after a
delay -- which is an anti-fraud system to build and operate. That is a later,
funded problem, not a first move.

Crypto belongs in the same bucket, harder: custody and regulatory work, and a
token would make every counter look like an incentive to inflate it.

## What could actually sell

| Product | Buyer | Why they would pay | Needs first |
|---|---|---|---|
| **Provider reliability feed** (which model provider or API is degraded now; what recovery works) | Model routers, agent platforms, gateways | They route real traffic on this and have no cross-vendor view. It is closest to our strongest measured result: provider failures recovered 78% vs 17% | Incidents, reliability history, reputation |
| **Private/team network** (your own agents' evidence, not shared) | Teams with repeatable API workflows | The reviewer's own verdict: useful today *for teams with their own recovery history*. Needs no public network at all | Auth and tenancy; the smallest build |
| **Hosted or supported self-host** | Companies that cannot send anything outside | No data leaves; they pay for the thing running and staying patched | Packaging, upgrade path |
| **Vendor reliability profile** (an API vendor's own failure and recovery picture, and the right to correct it) | API vendors, model providers | Their incentive is aligned: they want the profile accurate. They will not fabricate failures against themselves | The feed above |

Note which one is first to build and last to be glamorous: the **private team
network**. It is the only line that works at zero independent reporters,
because a team's own history is useful on its own -- five recoveries and the
network starts recommending what worked.

## The order

1. **Independent reporters.** Still 0. Nothing above has value until this
   moves, and the honest way to move it is the automatic paths (plugin,
   wrapper, proxy) plus a launch that says plainly where FailEcho helps and
   where it does not.
2. **Reputation and provenance**, so the numbers survive the question "who
   says?".
3. **Incidents**, free, because it is the most useful shareable signal and it
   makes the network visible.
4. **Private team networks**, the first paid thing.
5. **Provider reliability feed**, once 2 and 3 exist.

## What would kill it

- Selling contributor data, or being seen to.
- Paying per report.
- A token.
- Any claim the measurements do not support: general task-completion uplift,
  guaranteed secret exclusion, verified independent consensus. The review
  listed these; `docs/review-2026-09-20.md` keeps the list.
