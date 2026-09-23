# Claims we can defend, and the exact evidence

Updated 23 September 2026, 11:50 UTC, from one snapshot of the lab report.
Every number here is from the lab (`lab.failecho.com`), produced by **our own
agents**, over the lab's ledger window of 21 Sep 01:38 to 23 Sep 11:50 UTC
(the slow arms reach back to 20 Sep 19:11); production has 0 independent
reporters. If a line below is used anywhere public, the "our own agents" part
goes with it.

Two rules. A claim not in the first table does not get made. A number that
moves gets re-checked here before it is repeated.

## Safe to say

| Claim | Exact evidence | Where to check |
|---|---|---|
| When a fix exists, agents recover far more often | Model-provider failures: **73.9% recovered with FailEcho, 26.7% without**; 88 vs 86 failures met, so both sides met the same wall | lab scoreboard, provider group |
| That turns into finished work | **92.0% vs 83.9%** of runs finished, 463 vs 461 runs, p = 0.0002 | same |
| It costs less, not more, where it helps | **1,601 vs 1,663 tokens** per finished run in that group | same |
| It cuts wasted retries | Flaky endpoints: **15.7 s vs 22.1 s** lost inside failures per run, 1.5 vs 2.0 attempts per failure, **297** retries the network said to skip | test group |
| It does the same with the live network's own advice | Advice read from production: **1.38 vs 1.99** attempts per failure, **1.18 s vs 2.26 s** lost per run, 57 skips | prod group |
| The skip verdict is right | Where askers were told to skip, blind agents retried anyway: Stack Exchange **0 of 226** recovered, flaky endpoints **0 of 149** (503) and **0 of 149** (timeouts), GitHub **22 of 456** (4.8%) | advice table |
| Against careful recovery it still saves time, not outcomes | Both sides recover like a good engineer: **100% vs 100%** finished, **8.6 s vs 10.5 s** lost in failures per run (about 18% less), 1.86 vs 2.0 attempts per failure | local group |
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
| "Runs finished" | A model run counts when it **returned a non-empty answer**. A grader checks the answers against the APIs: on real APIs **93.1% vs 88.9%** of checked values correct on 29 vs 27 checkable runs since the regradable grader went live (05:17 UTC, 23 Sep), p = 0.66; careful recovery 100% vs 94.7% on 20 vs 19, p = 0.49. No measured difference in correctness |
| Real-API finish rate (67.6% vs 65.3%) | Not significant (p = 0.30). And on real APIs asking **costs time**: 6.93 s vs 5.88 s lost inside failures per run (908 vs 907 runs), because each ask is a round trip and GitHub's 403, the commonest failure there, has no fix to find; attempts per failure are 1.8 vs 1.9. Say both |
| Coding agents in a VM (79.9% vs 78.3%) | A tie: they fail on their own bugs, which no network of other agents' failures can help with |
| The MCP endpoint on its own | Models call a tool they have to choose **0.19-0.25 times per run**; the OpenCode pair with it is a tie (79.2% vs 73.9%, 24 vs 23 runs). Lead with the in-path integrations |
| Lab scale | All ours |

## Do not say

| Not this | Why |
|---|---|
| "The proxy improves results" | Its group is **behind or tied** (86.4% vs 90.9% on 22 runs a side, 23 Sep) and has carried **one** advice line in a scheduled run (05:37 UTC, 23 Sep, a 503). Say it is built and verified end to end, never that it helps |
| "Just add the MCP endpoint and your agent gets smarter" | That path is the **weakest**: agents given the tools used them 0.19-0.24 times per run. Every public page now ranks it last and says so. Lead with the plugin (Claude Code, OpenCode), the proxy, or the wrapper |
| "The OpenCode plugin improves results" | Its lab arm started 23 Sep: **2 vs 3 runs**, no advice line yet. Built, tested end to end, unmeasured |
| "Private mode works with every FailEcho integration" | Not the MCP tools, by design (a secret does not belong in a tool argument), and not older package versions: `failecho-autoreport` before 0.1.7, `failecho-mcp` before 0.2.3 and the Claude Code plugin before 0.2.0 ignore `FAILECHO_TEAM` and report **publicly**. Say "from these versions" |
| "Signed reporters are real people" | A key is free to make. Signing rules out impersonation, nothing more |
| "Private mode makes a team's agents do better" | Works and is private -- that is tested. Whether it *helps* a small team is being measured by the team arm (started 23 Sep, a week to read) |
| "FailEcho makes agents more correct" | No measured difference yet: checked answers read 93.1% vs 88.9% on 29 vs 27 runs (p = 0.66). It saves wasted retries and time, and recovers far more often where a fix exists |
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
