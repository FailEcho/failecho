# The fleet test: what the network looks like with agents in it

Written 2026-09-16. A plan, not a build. Nothing in it runs until the two
decisions at the end are made.

## The question it answers

Every real number so far points the same way: the laptop's 28 sessions had no
repeated failure, and our own agent's first day was nine clean runs against
well-behaved public APIs. The product's premise -- the same failure hitting
different agents, and the second one being told what fixed it for the first
-- lives at *volume*, in fleets and scheduled jobs. Nobody has run one
against FailEcho yet, including us.

So the test is a fleet. Not a load test: a realistic population of small
agents doing real work against real services, for two days, with half of them
using the network and half not, so the difference is measurable rather than
argued.

Three things it will show, whatever the answer:

1. **Do failures repeat across independent agents at all?** Same service, same
   operation, same shape, different reporter. If the fleet produces zero
   cross-agent fingerprints in 48 hours against real services, the shared
   layer's premise is in trouble and we should know.
2. **Does asking help?** Half the fleet asks the network before retrying and
   follows a recommendation when there is one; half retries blindly by fixed
   rules. Attempts-to-success per failure, per cohort. That number is the
   product.
3. **Does the naming hold?** The fleet reports through three different paths
   -- the decorator with hand-chosen names, `auto` with route-shaped names
   (`GET /repos`), and the MCP relay with tool names. If the same GitHub 404
   lands on three fingerprints because three paths named it three ways, the
   join that everything depends on is broken in practice, and that is the most
   important thing this test can find.

## Where it runs: a lab, not production

This morning 91 rows of test traffic reached production as adoption. That
settles it: the fleet runs against **a separate FailEcho instance** with its
own database and its own token, on this box, on its own port, reachable at a
lab hostname. It is a full copy of the software, so everything -- the front
page, `/network`, the counters, `related_failures`, decay -- behaves exactly
as it would in production, and the user can watch it there.

The lab instance is labelled as a lab everywhere it renders, its agents report
as plain `agent` (so the lab's own counters show what adoption *would* look
like), and its numbers are never quoted anywhere as FailEcho's. Production's
counter stays at whatever real strangers make it.

    lab.failecho.com  ->  127.0.0.1:8089  ->  /srv/failecho-lab/data/lab.db

Production's `failecho-agent.timer` keeps running as it is: one first-party
agent, every 30 minutes, labelled. The lab does not touch it.

## The fleet

Twelve agents. Each is a *persona*: a name, a client path, a model provider,
a task list and a policy. They are separate processes with separate reporter
ids and no shared state, run one at a time by a scheduler so the box never
holds more than one of them in memory (~40MB each; 535MB available).

| # | reporter | reports via | provider | asks first? | workload |
|---|---|---|---|---|---|
| 1 | fleet-decor-ask-a | decorator, hand-named | groq | yes | PyPI + npm version checks |
| 2 | fleet-decor-ask-b | decorator, hand-named | gemini | yes | GitHub repo metadata |
| 3 | fleet-decor-blind-a | decorator, hand-named | groq | **no** | PyPI + npm version checks |
| 4 | fleet-decor-blind-b | decorator, hand-named | gemini | **no** | GitHub repo metadata |
| 5 | fleet-auto-ask-a | `auto` (route names) | groq | yes | mixed, via requests |
| 6 | fleet-auto-ask-b | `auto` (route names) | gemini | yes | mixed, via httpx |
| 7 | fleet-auto-blind-a | `auto` (route names) | groq | **no** | mixed, via requests |
| 8 | fleet-auto-blind-b | `auto` (route names) | gemini | **no** | mixed, via httpx |
| 9 | fleet-mcp-ask | MCP relay, `check_tool_failure` | groq | yes | GitHub via the relay |
| 10 | fleet-mcp-blind | MCP relay, reports only | gemini | **no** | GitHub via the relay |
| 11 | fleet-cron-a | decorator | none (scripted) | yes | nightly-style: same 6 calls every run |
| 12 | fleet-cron-b | decorator | none (scripted) | **no** | nightly-style: same 6 calls every run |

Pairs 1/3, 2/4, 5/7, 6/8, 9/10, 11/12 are the experiment: identical workload
and path, one asks, one does not. Eleven and twelve have no model at all --
they are the "queue worker that hits the same wall every night" from the
Reddit post, and they exist to see whether *repetition alone* produces the
pattern.

**Real services, read-only:** PyPI, the npm registry, the GitHub API
(unauthenticated -- 60 requests/hour *per IP*, shared by the whole fleet,
which is exactly how a real fleet behind one NAT experiences it), raw
GitHub, and the two model providers on their free tiers. No writes anywhere.
Nothing manufactured: the failures are whatever these services actually do
when twelve agents share an IP for two days. Today's single agent already saw
Gemini time out twice and return a 503 once in nine runs.

**Schedule:** one agent run every 3 minutes, round-robin, 24 hours a day, for
48 hours. ~960 runs, ~80 per persona, ~3,000 tool calls, ~2,000 model calls.
Inside both providers' free-tier daily limits with room; if a provider
throttles, that is a real 429 and it is reported like any other.

## What the user watches

**`lab.failecho.com/network`** -- the same page production has, filled with
the fleet's data. Services, failure rates, recovery evidence, recommendations,
with the lab banner on it.

**`lab.failecho.com/fleet`** -- one new page, lab only, the experiment's
scoreboard, refreshed every minute:

- runs, tool calls, failures so far, by persona
- **attempts-to-success per failure: askers vs blind.** The headline.
- fingerprints seen by 2+ reporters (the "does it repeat" count)
- `cross_agent_help_24h` -- how many times an asker got a recommendation
  built on somebody else's evidence
- fingerprints per (service, operation): the naming fragmentation check. Three
  fingerprints for one GitHub 404 is the finding to fear.
- recovery outcomes accumulating; first `decaying` flag, first
  `related_failures` with `shares_a_fix_with_you`, if they happen

And a daily summary file sent here, so the numbers survive the test.

## What would count as what

- **The shared layer has a premise:** cross-reporter fingerprints appear
  within hours; askers reach success in fewer attempts than their blind twins
  on the same failures; `cross_agent_help_24h` climbs.
- **The premise is weak at this scale:** failures exist but rarely repeat
  across reporters; askers and blind twins tie because the network never has a
  recommendation when they ask. Honest result, and it says the product needs
  much larger fleets than one box can simulate -- or that the value is in the
  single-agent memory only.
- **The naming is broken:** the same failure sits on several fingerprints
  because the three paths named it differently. Then the fix is in the
  canonicaliser, not in marketing, and the fleet has told us exactly which
  names to merge.

Whatever it shows gets written up as it is. The Reddit post promised that.

## Safeguards, each one tested before the first run

- The fleet's `FAILECHO_ENDPOINT` is the lab. The scheduler refuses to start if
  it resolves to anything else, and every persona's environment is asserted
  against production's hostname at startup.
- The lab instance has its own `FIN_FIRST_PARTY_TOKEN`, different from
  production's, so a stray production token in a persona's environment lands
  as `demo_agent`, not first_party.
- One persona process at a time, `MemoryMax=120M`, `TimeoutStartSec=180`.
- GitHub calls carry a `User-Agent` naming the project and a contact address,
  and stay under the documented unauthenticated limit *on purpose* except when
  the fleet's natural pace crosses it -- which is the experiment, not abuse.
- Daily budget caps per provider, in the persona code, so a misconfigured
  timer can only run less.
- Kill switch: `systemctl stop failecho-fleet.timer`. The lab instance can be
  deleted whole; nothing in it is anyone's.

## The software under test is frozen

The lab runs from its own checkout, `/srv/failecho-lab/app`, pinned at the
commit recorded in `/srv/failecho-lab/PINNED_AT`. `deploy/deploy.sh` only
touches `/srv/failecho`, so production can keep shipping without changing
the experiment under the fleet. For the first two hours it did not work this
way, and every deploy restarted the lab with new code; that is noted here
rather than hidden, and the runs from then are still in the state file.

## Change during the run: two builders, 2026-09-16 15:40 UTC

Six hours in, two personas were added: `fleet-build-ask` and
`fleet-build-blind`, which write small programs and run them in a throwaway
VM (`docs/sandbox.md`). The sixteen original rows were not touched -- a test
pins them byte for byte -- and the lab was re-pinned to the commit that adds
the builders, which restarted the lab instance once. The timer went from
every 3 minutes to every 2 at the same time; the provider daily caps pace
the model-backed personas regardless. Runs before this change are still in
the state file and on the scoreboard, and the builders' rows start from zero.

What the builders add: a second ledger, **local** failures (the agent's own
bug, counted and never reported) against **shared** ones (the world's,
reported), on realistic development tasks. That share is the number the
rest of the fleet cannot produce.

## Change during the run: askers follow `skip`, 2026-09-17 04:40 UTC

The first day ended in a tie: askers and blind both took 2.0 attempts per
failure on the test targets, because when every retry on record had failed
the network had no recommendation and the asker retried anyway. The network
now recommends `skip` in that case (`app/core/intelligence.py::futility`),
and the ask personas honour it: no second attempt, `skipped` counted on the
scoreboard. Blind personas are unchanged. From this point the ask/blind
comparison measures the product as it is; the tie before it is in the log.

## Change during the run: two explorers, 2026-09-17 05:00 UTC

On a model-provider failure the fleet used to give up, every persona alike,
so the network never got evidence about the one class of failure that
happens daily and has non-obvious fixes (wait for the reset header rather
than three seconds; switch model; drop `tool_choice` after a tool-JSON parse
error). Two explorers now try the first action nobody has evidence for and
report the outcome; askers follow what the network then recommends; blind
personas still give up, as the control. The scoreboard's "when the model
provider fails" table is the comparison: recovery rate per cohort.

## Change during the run: real limits, real fixes, 2026-09-17 06:00 UTC

PyPI and npm do not fail under honest use; GitHub does (403 at 60 an hour
per address, 103 times on the first day). Three additions so the real-API
comparison has something to measure: `fleet-limits-ask` / `-blind`, twins
on services that push back under honest use (crates.io at one request a
second, Stack Exchange at 300 a day, GitHub search at 10 a minute) plus two
GitHub repos; a third explorer with no model that meets GitHub's 403 every
run; and two recovery actions an agent would not try on its own --
`wait_until_reset` from the `X-RateLimit-Reset` header, and
`conditional_request` (If-None-Match, a 304 that does not count against the
limit, from a per-persona ETag cache). Explorers discover, askers inherit,
blind keeps its default. The scoreboard's "real services, real limits"
table is the proof table: real services, controlled twins only.

## Correction: the twins ran in a fixed order, 2026-09-17 06:30 UTC

Every ask twin ran before its blind twin, two minutes apart. On GitHub's
hourly budget the one that runs second meets the 403s the first one used
the budget up for: `fleet-gh-ask` 0 failures, `fleet-gh-blind` 47,
identical work, 31 runs each. That would have made the proof table lie in
our favour. From 06:30 the twins trade places every cycle, and the
real-services table counts only runs from then on. The numbers before it
stay in the state file and in this note.

## Change during the run: provider failures become the comparison, 2026-09-18 05:30 UTC

Two days in, the real targets' failures are hard limits nobody recovers
from -- GitHub's 60 an hour, Stack Exchange's 300 a day -- so the skip
verdict saves seconds and retries but cannot move "tasks completed". The
one real, recoverable failure the fleet meets all day is its model
providers' 429s: a per-minute window that resets in seconds, and a daily
quota another model on the same provider is not under. The lab had
`switch_model` at 1 of 1 for a day, because an explorer tried each action
once and stopped, and one attempt is under the server's floor of five.

Three changes, all on the fleet side, nothing on the product:

- **The control retries.** Blind personas, and askers the network has
  nothing for, retry a failed provider once after three seconds -- what an
  agent without the network does. Until now they gave up at once, which
  flattered the ask side. Askers still follow a recommendation; their edge
  is inherited, not invented.
- **Explorers explore until the network can rule.** Below five attempts an
  explorer goes back to the action that has worked best; it explores past
  a `skip` verdict and honours it only when every candidate has been ruled
  on.
- **A provider is marked out of quota only by a failed switch.** A blind
  retry into the same wall says nothing about the provider's other models,
  and marking on it skipped the askers too. The personas keep running; a
  429 costs a second, and the difference between the cohorts under a real
  quota is the point.

`conditional_request` is dropped from the explorers' list: GitHub answers a
conditional GET with 403, not 304, once the IP is over its limit (probed by
hand; 0 of 2 in the lab). It prevents a limit, it does not recover from
one.

The scoreboard gains a fourth Ask/Blind group, "model providers under real
quotas", counted only from 05:30 on and only for the model-driven personas
(builders and explorers stay out). The three existing groups are
unchanged; their blind runs before 05:30 gave up on a provider failure and
from 05:30 retry once, which can only move them toward blind.

## What it costs

Zero dollars: free tiers throughout. About 40MB of RAM at any moment, one
SQLite file that will reach a few MB, and a Caddy site block. Roughly a day to
build the personas, the scheduler, the lab unit files and the `/fleet` page,
with the safeguards tested first; then 48 hours of watching.

## Two decisions needed

1. **A DNS record.** `lab.failecho.com` -> this server's IP, proxied through
   Cloudflare like the apex. Without it the lab is watchable only through an
   SSH tunnel, or via the daily summary file.
2. **Scale and length.** Twelve personas for 48 hours is the proposal. Six for
   24 hours is the cheap version and would still answer question 1; it would
   answer question 2 weakly.

Say go, and which, and the build starts with the safeguards.
