# Claims we can defend, and the exact evidence

Updated 23 September 2026, 21:23 UTC, from one snapshot of the lab report,
after the grader and scoreboard fixes of that day (367f7f1, 7d46658,
9192334, bd63b80, b458d5e), **with a significance test on every comparison**
(Fisher or two-proportion z for rates, Welch for means). Every number here is
from the lab (`lab.failecho.com`), produced by **our own agents**, over the
lab's ledger window of 21 Sep 09:20 to 23 Sep 21:23 UTC (the slow arms reach
back to 20 Sep 19:11); production has 0 independent
reporters. If a line below is used anywhere public, the "our own agents" part
goes with it.

Three rules. A claim not in the first table does not get made. A number that
moves gets re-checked here before it is repeated. A difference without a
p-value under 0.05 is described as no difference.

## Safe to say

| Claim | Exact evidence | Where to check |
|---|---|---|
| When switching model fixes a rate limit, agents recover far more often | Model-provider failures: **74.7% recovered with FailEcho, 32.1% without**, 83 vs 84 failures met, p < 0.0001. Two shapes make all of it: Groq 429 (81% vs 17%) and Gemini 429 (100% vs 10%), where the network said `switch_model` and it worked 42 of 49 times | lab scoreboard, provider group |
| That turns into finished work | **91.5% vs 83.2%** of runs finished, 422 runs a side, p = 0.0003 -- all of it in the runs that met a provider failure (65.8% vs 18.1%, 79 vs 72 runs); the other 82% of runs finish alike (97.4% vs 96.6%, p = 0.66) | same |
| It costs no more tokens | 1,390 vs 1,422 tokens per finished run in that group, p = 0.71: no difference | same |
| It cuts wasted retries | Flaky endpoints: **15.2 s vs 21.9 s** lost inside failures per run (149 runs a side, p < 0.0001), 1.5 vs 2.0 attempts per failure, **297** retries the network said to skip | test group |
| It does the same with the live network's own advice (before 24 Sep 10:48; since then production also holds the lab's real-service evidence, first-party, via `failecho_mirror`) | Advice read from production: **1.40 vs 1.99** attempts per failure (p < 0.0001), **1.07 s vs 2.13 s** lost per run (126 runs a side, p = 0.014), 61 skips | prod group |
| The skip verdict is right | Where askers were told to skip, blind agents retried anyway: Stack Exchange **0 of 339** recovered (95% upper bound 1.1%), flaky endpoints **0 of 149** (503) and **0 of 149** (timeouts), GitHub **22 of 455** (4.8%, upper bound 7.2%) | advice table |
| Against careful recovery there is no measured gain | Both sides recover like a good engineer: **100% vs 98.6%** finished (143 runs a side, p = 0.16), **8.5 s vs 8.9 s** lost in failures per run (p > 0.8: none), 1.87 vs 2.0 attempts per failure (p = 0.005, small). The "12-18% less time" said earlier on 23 Sep was never significant | local group |
| Advice arrives without the model choosing to ask | The Claude Code plugin (a hook), `failecho-autoreport` (advice on the exception), `failecho-mcp proxy` (a line in the failed tool's error), the OpenCode plugin (a line in every failing tool's output) | source, registries |
| Metadata only | No field exists for prompts, arguments, results or bodies; error text off by default; the proxy and the OpenCode plugin never send it; reads write no observation | source, `/about` |
| Private mode keeps a team's evidence to the team | Separate tables, never pooled or counted; tested end to end through the hook and the wrapper; the team can delete everything or rotate a leaked token | `tests/test_private.py`, `/setup` |
| Signing proves a reporter holds its key | Ed25519, checked against RFC 8032; a bad signature is refused, not downgraded. **Not** proof of a person | `tests/test_identity.py` |
| Nobody outside uses it yet | Production: **0 independent reporters**, 0 sparse rows, lab traffic labelled as ours -- including, since 24 Sep, the lab evidence mirrored into production (every row first_party) | `/v1/stats`, front page |
| Production answers what the lab has learned | Since 24 Sep 10:48 the lab's own agents' reports on real public services are copied into production, first-party, every ten minutes: a new user's agent gets the same kind of answer the lab's agents get. The evidence is still all ours | `failecho_mirror`, `/v1/stats` first_party count |
| It works before anyone joins | Five recoveries of your own and it starts recommending what worked, marked as your own history | `/llms.txt`, "Working alone" |

## Say only with the qualifier attached

| Claim | Required qualifier |
|---|---|
| The provider-group numbers | The control retries **once after 3 seconds**. The win is the network telling an agent to switch to another model on a per-model rate limit (Groq and Gemini free tiers here). An agent that already falls back to another model on a 429 would get most of it without FailEcho -- that comparison is **not measured** (the careful twins run on Mistral and met no provider failure). Outages and timeouts show no difference (NVIDIA timeouts 79% vs 72%, p = 0.71) |
| "Runs finished" | A model run counts when it **returned a non-empty answer**. A grader checks the answers against the APIs: on real APIs **98.5% vs 100%** of checked values correct on 67 vs 60 checkable runs since the regradable grader went live (05:17 UTC, 23 Sep), p = 1.0; careful recovery 98.2% vs 98.1% on 56 vs 53; production advice 100% vs 100% on 18 vs 16. No measured difference in correctness either way |
| Real-API finish rate (66.4% vs 64.6%) | Not significant (p = 0.43). And on real APIs asking **costs time**: 6.67 s vs 5.84 s lost inside failures per run (867 runs a side; per run p = 0.22, not significant). What *is* significant is per failure on GitHub's 403, the commonest failure there, which the network knows well and has no fix for (86% of real-API asks return nothing actionable): **+0.63 s per failure while the lab database was slow** (598 ms per ask, until its first prune at 09:05 on 23 Sep), **+0.16 s since** (137 ms per ask). The rest of the gap is the ask side meeting more 403s per run, +0.16 ±0.33 in 720 paired runs: noise, run order balanced. Say both |
| Coding agents in a VM (74.9% vs 73.4%, p = 0.51) | A tie: they fail on their own bugs, which no network of other agents' failures can help with |
| The MCP endpoint on its own | Models call a tool they have to choose **0.19-0.25 times per run**; the OpenCode pair with it is a tie (81.5% vs 76.9%, 27 vs 26 runs). Lead with the in-path integrations |
| A team's own history tells it when to stop | One failure shape, one day, our own two-agent team: after five failed attempts on Stack Exchange's exhausted quota its private history said skip (7.4 h after the arm started, 23 Sep 13:46); over the arm, **14.6 s vs 71.3 s** lost inside failures per run (54 vs 53 runs, p < 0.0001) and 110 retries skipped, with no difference in finishing (35.2% vs 30.2%) -- time saved on walls nobody could pass. No fix of its own yet (`wait_until_reset` on GitHub's 403 worked 4 of 21) |
| Lab scale | All ours |

## Do not say

| Not this | Why |
|---|---|
| "The proxy improves results" | Its group is **behind or tied** (88.0% vs 92.0% on 25 runs a side, 23 Sep) and has carried **one** advice line in a scheduled run (05:37 UTC, 23 Sep, a 503). Say it is built and verified end to end, never that it helps |
| "Just add the MCP endpoint and your agent gets smarter" | That path is the **weakest**: agents given the tools used them 0.19-0.24 times per run. Every public page now ranks it last and says so. Lead with the plugin (Claude Code, OpenCode), the proxy, or the wrapper |
| "The OpenCode plugin improves results" | Its lab arm started 23 Sep: **5 vs 6 runs**, no advice line yet. Built, tested end to end, unmeasured |
| "Private mode works with every FailEcho integration" | Not the MCP tools, by design (a secret does not belong in a tool argument), and not older package versions: `failecho-autoreport` before 0.1.7, `failecho-mcp` before 0.2.3 and the Claude Code plugin before 0.2.0 ignore `FAILECHO_TEAM` and report **publicly**. Say "from these versions" |
| "Signed reporters are real people" | A key is free to make. Signing rules out impersonation, nothing more |
| "Private mode makes a team's agents do better" | Works and is private -- that is tested. Its first result is one skip on one failure shape on one day (see the qualified row above): it saves time there and changes no outcome. Say that, not "better" |
| "FailEcho makes agents more correct" | No measured difference: 98.5% vs 100% of checked answers on real APIs (67 vs 60 runs, p = 1.0), 98.2% vs 98.1% with careful recovery (56 vs 53). It saves wasted retries and time, and recovers far more often where a fix exists |
| "Proven to raise task completion" | Only in the provider group, only against a naive retry, and only on runs that met a per-model rate limit; see the qualifiers |
| "It saves time even for a well-built agent" | Not measured to: against careful recovery the time lost is 8.5 s vs 8.9 s per run, p > 0.8 |
| "It uses fewer tokens" | 1,390 vs 1,422 per finished run, p = 0.71: no difference |
| "Independent agents confirm this" | Independent reporters: 0 |
| "Your secrets can never leak" | Defaults are narrow, but a URL path or an error string can carry an identifier. Say what is sent, not what cannot happen |
| "Three-second guarantee" | The advice budget is a socket timeout, not an end-to-end deadline |
| "Byte-for-byte for every MCP server" | True for a locally started server; a remote server's messages are parsed and re-serialised |

## Numbers to refresh before reuse

`lab.failecho.com/fleet` is live, and every table there now says the window it
covers. Production counters: `https://failecho.com/v1/stats`. The lab's
correctness numbers restarted at 05:17 UTC on 23 Sep, when grades began keeping
the truth they used; from then on every grader fix applies to all of them.
