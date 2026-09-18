# FailEcho lab: the first 48 hours, with and without

*Window: 2026-09-16 09:17 to 2026-09-18 12:34 UTC. Everything below is from
the lab (lab.failecho.com), a separate instance the fleet reports to. None of
it is adoption; production has 0 independent observations. Numbers are the
scoreboard's own at 12:34 UTC; the live page is
[lab.failecho.com/fleet](https://lab.failecho.com/fleet).*

## What was run

1,476 runs by 37 personas on one machine, one run every two minutes. Each
persona is a small agent with a fixed workload against real services (PyPI,
npm, the GitHub API, crates.io, Stack Exchange), a test service that returns
what it is asked for (httpbingo), or a coding task inside a throwaway VM.
Personas come in twins: same workload, same model, same order lottery; the
*ask* twin calls FailEcho before retrying a failed call and follows what it
says, the *blind* twin retries the way an agent without it does. Six
explorers try what the network has no evidence for and report the outcome;
askers inherit what the network then recommends.

Models: gpt-oss-20b/120b (groq), gemini-flash (Gemini), three free models
on OpenRouter, gpt-oss:20b, nemotron-3-nano and gemma4 (Ollama cloud); from
18 Sep also nemotron-3-super (NVIDIA), ministral-8b (Mistral) and qwen3.6-27b
(xKiro). All free tiers, all under their documented caps.

The lab holds 14,012 observations over 17 services, 1,352 recovery
outcomes, 369 recommendations built on another agent's evidence.

## The five claims and where each stands

| Claim | Evidence | Status |
|---|---|---|
| Faster on failures | test cohort 18.1 vs 26.2 s/run; 14.4 vs 22.6 s lost inside failures; holds every hour for two days | **proven** |
| The skip verdict is almost never wrong | in the hours and shapes the network told askers to skip, blind retried 174 times and recovered once | **proven** |
| The right fix when one exists | advised second attempts recover 31/42 (74%) vs blind 39/98 (40%) | **shown on one shape** (httpbingo 503); more shapes needed |
| Costs nothing extra | 487 vs 493 tokens per run; asking takes 72 ms per run | **proven** |
| Finishes tasks a blind agent gives up on | provider-quota experiment started 18 Sep 05:30; 44 vs 46 runs, tie so far, askers have no `switch_model` recommendation to inherit yet (3 of 5 attempts) | **open** |

## Ask vs blind, the vendor table

Real APIs (275 vs 278 runs, since the twins alternated order at 17 Sep 06:30):

| metric | ask | blind |
|---|---:|---:|
| tasks completed | 62.5 % | 65.1 % |
| tokens per run | 487 | 493 |
| tokens per completed task | 779 | 758 |
| seconds per run | 5.3 | 4.9 |
| seconds lost inside failures, per run | 1.90 | 1.99 |
| retry attempts per failure | 1.43 | 1.56 |
| pointless retries avoided | 79 | 0 |

Flaky, broken and slow endpoints (42 vs 42):

| metric | ask | blind |
|---|---:|---:|
| seconds per run | 18.1 | 26.2 |
| seconds lost inside failures, per run | 14.4 | 22.6 |
| retry attempts per failure | 1.68 | 2.00 |
| pointless retries avoided | 100 | 0 |

Coding agents in a VM (32 vs 32):

| metric | ask | blind |
|---|---:|---:|
| tasks completed | 78.1 % | 78.1 % |
| tokens per completed task | 4,160 | 3,985 |
| seconds per run | 25.2 | 31.8 |
| seconds waiting on rate limits, per run | 4.7 | 10.9 |

Read: where the network can change an outcome, it saves time and retries.
Completion is a tie, and the real-API completion gap is not the product:
the twins share one IP and GitHub's 60-an-hour budget, and whoever meets
the 403s finishes fewer tasks. Nobody recovers a 403 (6 of 182 asker
retries, 9 of 150 blind); the honest answer is "stop", and that is what
the asker does.

## What the advice is worth, by failure shape

Since 17 Sep 06:30, twins only. "Advised" is a second attempt the network
recommended; "no advice" is an asker with nothing from the network, which
then does what blind does; the last column is blind's yield in the same
hour on the same shape the network said skip.

| shape | advised | no advice | blind | skipped | blind recovered where skipped |
|---|---:|---:|---:|---:|---:|
| httpbingo 503 | 31/42 (74%) | – | 39/98 (40%) | 47 | 0/47 |
| httpbingo 429 | – | 25/50 (50%) | 22/47 (47%) | 0 | – |
| httpbingo timeout | – | – | 0/47 | 47 | 0/47 |
| GitHub 403 | – | 6/182 (3%) | 9/150 (6%) | 46 | 1/43 |
| Stack Exchange 429 | – | 0/4 | 0/35 | 31 | 0/35 |
| OpenRouter 429 | – | 0/2 | 0/4 | 2 | 0/2 |

Two things this table settles. Where the network has a recommendation,
second attempts recover 74% against 40%. Where it has none, the asker
behaves exactly like blind (50 vs 47, 3 vs 6) -- the control is clean and
the asker's edge is inherited, not invented.

## Does it get better with evidence?

Share of an asker's failures the network had a recommendation for, per half
day: 0% (17 Sep morning), 7% (17 Sep afternoon), 26% (18 Sep morning). The
network's own learning curve. Completion per half day moves with the
GitHub budget, not with this.

## Onboarding from llms.txt

Every hour a cheap model is dropped into a clean VM with the one line "set
up FailEcho for this project" and llms.txt. Five scenes: clean project, a
project that already has another MCP server, one that already has ours, a
home directory full of projects, a read-only checkout. 46 runs, 26 graded
(the rest were the provider refusing), 11 passed.

| scene | graded | passed | what fails |
|---|---:|---:|---|
| clean | 4 | 3 | did not verify (1) |
| other server present | 10 | 6 | did not verify (3), clobbered the other entry (1) |
| ours already present | 2 | 1 | grader error, since fixed |
| home directory | 8 | 1 | wrote a config anyway instead of asking (5), did not ask (7) |
| read-only | 2 | 0 | did not verify (2) |

Per model: gemma4-31b 4/5, gpt-oss-120b 2/2, ministral-14b 1/1,
qwen3.6-27b 1/1, gpt-oss:20b 2/6, nex-n2.5-mini 1/2, nemotron-3-nano 0/5,
lfm-2.5-2.6b 0/2, nemotron-3-super 0/1. Gemini and two OpenRouter models
never got a graded run: their free tiers were exhausted before their turn.

Three document changes came out of this, each after the same grade fell
across models: "look first" for the home directory, the verify step made an
instruction with the request inline, and a shorter default section. The
model that had missed "verify" four times ran the query itself on its first
run after the rewrite.

## Builders

Two coding-agent twins (groq), from 18 Sep three pairs (NVIDIA and xKiro
added). 32 vs 32 runs graded on whether the task came out done: 78.1% both.
The ask builder spends 4.7 s a run waiting on rate limits against 10.9 --
it is told when a wait is pointless. Inside the VM the wrapper filed 116
shared failures and 15 local ones for the ask twin, 75 and 24 for blind;
the coverage counter (how much of a program's traffic the wrapper saw) is
known to be wrong and is not quoted.

## What broke and was fixed, in the open

125 commits in the window; the day logs (docs/night-2026-09-16.md,
docs/day-2026-09-17.md) carry each one with numbers. The ones that changed
a number:

- The twins ran in a fixed order (ask first) and the second met the 403s
  the first had used the budget for. Alternated from 17 Sep 06:30; the
  proof table counts from there.
- Askers retried after a skip verdict and tied with blind. From 17 Sep
  04:40 they honour it.
- Blind gave up on a provider failure at once, which flattered the ask
  side. From 18 Sep 05:30 it retries once.
- A provider marked out of quota was held dead until midnight UTC;
  groq's day is not UTC. Marks expire after two hours.
- Fence refusals (the VM's egress allowlist) were filed twice as failures
  of the host; both fixed.
- Two grader misgrades (a `find -name` read as a home edit; a request for
  the choice without a question mark not read as asking).

## What is not shown

- No gain in tasks completed on real APIs. The failures there are hard
  limits nobody recovers from; saving time on them is the win, finishing
  them is not possible.
- The provider-quota experiment is a day old. Askers will inherit
  `switch_model` once explorers reach the five-attempt floor; today they
  behave like blind.
- One shape with advised recoveries. Three would make the third claim.
- Zero independent adoption. The lab proves mechanism, not demand.
- Everything ran from one IP on one machine; GitHub's per-IP budget shapes
  the real-API numbers more than anything we did.

## Reproduce it

`scripts/ask_vs_blind.py` runs the ask/blind comparison against any
FailEcho instance except production (it refuses failecho.com). The fleet,
the sandbox and the onboarding harness are in the repository under
`failecho_fleet/` and `failecho_sandbox/`; docs/fleet-test-plan.md is the
plan with every change dated.
