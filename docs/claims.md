# Claims we can defend, and the exact evidence

Updated 23 September 2026, 17:42 UTC, from one snapshot of the lab report,
after the grader and scoreboard fixes of that day (367f7f1, 7d46658,
9192334, bd63b80). Every number here is from the lab (`lab.failecho.com`),
produced by **our own agents**, over the lab's ledger window of 21 Sep 06:21
to 23 Sep 17:42 UTC (the slow arms reach back to 20 Sep 19:11); production has 0 independent
reporters. If a line below is used anywhere public, the "our own agents" part
goes with it.

Two rules. A claim not in the first table does not get made. A number that
moves gets re-checked here before it is repeated.

## Safe to say

| Claim | Exact evidence | Where to check |
|---|---|---|
| When a fix exists, agents recover far more often | Model-provider failures: **74.1% recovered with FailEcho, 30.1% without**; 81 vs 83 failures met, so both sides met the same wall | lab scoreboard, provider group |
| That turns into finished work | **91.7% vs 83.5%** of runs finished, 436 runs a side, p = 0.0002 | same |
| It costs less, not more, where it helps | **1,502 vs 1,571 tokens** per finished run in that group | same |
| It cuts wasted retries | Flaky endpoints: **15.4 s vs 22.1 s** lost inside failures per run, 1.5 vs 2.0 attempts per failure, **297** retries the network said to skip | test group |
| It does the same with the live network's own advice | Advice read from production: **1.41 vs 1.99** attempts per failure, **1.09 s vs 2.06 s** lost per run, 57 skips | prod group |
| The skip verdict is right | Where askers were told to skip, blind agents retried anyway: Stack Exchange **0 of 275** recovered, flaky endpoints **0 of 149** (503) and **0 of 149** (timeouts), GitHub **20 of 416** (4.8%) | advice table |
| Against careful recovery it still saves time, not outcomes | Both sides recover like a good engineer: **100% vs 99.2%** finished (128 runs a side, p = 0.31: no difference), **8.1 s vs 9.2 s** lost in failures per run (about 12% less), 1.85 vs 2.0 attempts per failure | local group |
| Advice arrives without the model choosing to ask | The Claude Code plugin (a hook), `failecho-autoreport` (advice on the exception), `failecho-mcp proxy` (a line in the failed tool's error), the OpenCode plugin (a line in every failing tool's output) | source, registries |
| Metadata only | No field exists for prompts, arguments, results or bodies; error text off by default; the proxy and the OpenCode plugin never send it; reads write no observation | source, `/about` |
| Private mode keeps a team's evidence to the team | Separate tables, never pooled or counted; tested end to end through the hook and the wrapper; the team can delete everything or rotate a leaked token | `tests/test_private.py`, `/setup` |
| Signing proves a reporter holds its key | Ed25519, checked against RFC 8032; a bad signature is refused, not downgraded. **Not** proof of a person | `tests/test_identity.py` |
| Nobody outside uses it yet | Production: **0 independent reporters**, 0 sparse rows, lab traffic labelled as ours | `/v1/stats`, front page |
| It works before anyone joins | Five recoveries of your own and it starts recommending what worked, marked as your own history | `/llms.txt`, "Working alone" |

## Say only with the qualifier attached

| Claim | Required qualifier |
|---|---|
| The provider-group numbers | The control retries **once after 3 seconds**, not like a careful engineer. Against careful recovery (local group) the gain is time, not finished runs |
| "Runs finished" | A model run counts when it **returned a non-empty answer**. A grader checks the answers against the APIs: on real APIs **98.2% vs 100%** of checked values correct on 57 vs 50 checkable runs since the regradable grader went live (05:17 UTC, 23 Sep), p = 1.0; careful recovery 100% vs 100% on 42 vs 40; production advice 100% vs 100% on 17 vs 15. No measured difference in correctness either way |
| Real-API finish rate (67.5% vs 65.2%) | Not significant (p = 0.31). And on real APIs asking **costs time**: 6.76 s vs 5.77 s lost inside failures per run (881 vs 882 runs; per run p = 0.14, not significant). What *is* significant is per failure on GitHub's 403, the commonest failure there, which the network knows well and has no fix for (86% of real-API asks return nothing actionable): **+0.63 s per failure while the lab database was slow** (598 ms per ask, until its first prune at 09:05 on 23 Sep), **+0.16 s since** (137 ms per ask). The rest of the gap is the ask side meeting more 403s per run, +0.16 ±0.33 in 720 paired runs: noise, run order balanced. Say both |
| Coding agents in a VM (75.9% vs 74.3%) | A tie: they fail on their own bugs, which no network of other agents' failures can help with |
| The MCP endpoint on its own | Models call a tool they have to choose **0.19-0.25 times per run**; the OpenCode pair with it is a tie (80.0% vs 76.0%, 25 runs a side). Lead with the in-path integrations |
| A team's own history tells it when to stop | One failure shape, one day, our own two-agent team: after five failed attempts on Stack Exchange's exhausted quota its private history said skip (7.4 h after the arm started, 23 Sep 13:46); since then **3.2 s vs 102.9 s** lost inside failures per run (16 vs 15 runs, p < 0.0001), and **0% vs 0%** finished -- time saved on a wall nobody could pass. No fix of its own yet (the best action, `wait_until_reset`, worked 5 of 20) |
| Lab scale | All ours |

## Do not say

| Not this | Why |
|---|---|
| "The proxy improves results" | Its group is **behind or tied** (87.5% vs 91.7% on 24 runs a side, 23 Sep) and has carried **one** advice line in a scheduled run (05:37 UTC, 23 Sep, a 503). Say it is built and verified end to end, never that it helps |
| "Just add the MCP endpoint and your agent gets smarter" | That path is the **weakest**: agents given the tools used them 0.19-0.24 times per run. Every public page now ranks it last and says so. Lead with the plugin (Claude Code, OpenCode), the proxy, or the wrapper |
| "The OpenCode plugin improves results" | Its lab arm started 23 Sep: **4 vs 5 runs**, no advice line yet. Built, tested end to end, unmeasured |
| "Private mode works with every FailEcho integration" | Not the MCP tools, by design (a secret does not belong in a tool argument), and not older package versions: `failecho-autoreport` before 0.1.7, `failecho-mcp` before 0.2.3 and the Claude Code plugin before 0.2.0 ignore `FAILECHO_TEAM` and report **publicly**. Say "from these versions" |
| "Signed reporters are real people" | A key is free to make. Signing rules out impersonation, nothing more |
| "Private mode makes a team's agents do better" | Works and is private -- that is tested. Its first result is one skip on one failure shape on one day (see the qualified row above): it saves time there and changes no outcome. Say that, not "better" |
| "FailEcho makes agents more correct" | No measured difference: 98.2% vs 100% of checked answers on real APIs (57 vs 50 runs, p = 1.0), 100% vs 100% with careful recovery (42 vs 40). It saves wasted retries and time, and recovers far more often where a fix exists |
| "Proven to raise task completion" | Only in the provider group, and only against a naive retry; see the qualifiers |
| "Independent agents confirm this" | Independent reporters: 0 |
| "Your secrets can never leak" | Defaults are narrow, but a URL path or an error string can carry an identifier. Say what is sent, not what cannot happen |
| "Three-second guarantee" | The advice budget is a socket timeout, not an end-to-end deadline |
| "Byte-for-byte for every MCP server" | True for a locally started server; a remote server's messages are parsed and re-serialised |

## Numbers to refresh before reuse

`lab.failecho.com/fleet` is live, and every table there now says the window it
covers. Production counters: `https://failecho.com/v1/stats`. The lab's
correctness numbers restarted at 05:17 UTC on 23 Sep, when grades began keeping
the truth they used; from then on every grader fix applies to all of them.
