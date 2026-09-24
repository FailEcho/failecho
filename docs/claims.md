# Claims we can defend, and the exact evidence

Updated 24 September 2026, 11:12 UTC, from one snapshot of the lab report,
**with a significance test on every comparison** (Fisher or two-proportion z
for rates, Welch for means). Every number here is from the lab
(`lab.failecho.com`), produced by **our own agents** (55 of them, on seven
model providers' free tiers and real public APIs), over the lab's ledger
window of 22 Sep 00:31 to 24 Sep 11:12 UTC; production has 0 independent
reporters. If a line below is used anywhere public, the "our own agents" part
goes with it.

Three rules. A claim not in the first table does not get made. A number that
moves gets re-checked here before it is repeated. A difference without a
p-value under 0.05 is described as no difference.

**Read this first.** Every gain below comes from an agent that **acts on the
advice**: the lab's asking agents switch model when told `switch_model` and
stop when told `skip`. FailEcho delivers the advice -- on the exception, in
the tool's error, in the plugin's output -- and acting on it is the caller's
code or model. Where the lab left that decision to the model with no harness
help (the "wrapped" arm), there is **no measured difference yet**: 100% vs
100% finished, 90.5% vs 91.1% of checked answers correct, 122 runs a side.

## Safe to say

| Claim | Exact evidence | Where to check |
|---|---|---|
| When switching model fixes a rate limit, agents that follow the advice recover far more often | Model-provider failures: **74.5% recovered with FailEcho, 31.2% without**, 94 vs 93 failures met, p < 0.0001. The win is one move on per-model rate limits (Groq and Gemini free tiers): the network says `switch_model`, and on Groq's 429 it had worked 310 of 341 times | lab scoreboard, provider group |
| That turns into finished work | **90.6% vs 80.2%** of runs finished, 416 vs 415 runs, p < 0.0001 -- all of it in the runs that met a provider failure (65.9% vs 21.7%, 88 vs 83 runs, p < 0.0001); the other 79% of runs finish alike (97.3% vs 94.9%, p = 0.16) | same |
| It costs no more tokens | 1,495 vs 1,536 tokens per finished run in that group, p = 0.66: no difference | same |
| It cuts wasted retries | Flaky endpoints: **12.2 s vs 19.4 s** lost inside failures per run (125 runs a side, p < 0.0001), 1.5 vs 2.0 attempts per failure, **249** retries the network said to skip | test group |
| It does the same with the live network's own advice | Advice read from production: **1.43 vs 1.99** attempts per failure (p < 0.0001), **1.08 s vs 2.06 s** lost per run (144 runs a side, p = 0.016), 69 skips. Most of these runs predate 24 Sep 10:48, when production began to hold the lab's evidence too | prod group |
| The skip verdict is right | Where askers were told to skip, blind agents retried anyway: Stack Exchange **0 of 404** recovered (95% upper bound 0.9%), flaky endpoints **0 of 125** (503) and **0 of 125** (timeouts), GitHub 403 **28 of 340** (8.2%, upper bound 11.6%) | advice table |
| Against careful recovery there is no measured gain | Both sides recover like a good engineer: **100% vs 99.0%** finished (192 runs a side, p = 0.16), **9.8 s vs 10.1 s** lost in failures per run (p = 0.91: none), 1.88 vs 2.0 attempts per failure (p = 0.002, small) | local group |
| Advice arrives without the model choosing to ask | The Claude Code plugin (a hook), `failecho-autoreport` (advice on the exception), `failecho-mcp proxy` (a line in the failed tool's error), the OpenCode plugin (a line in every failing tool's output). Delivery is tested end to end; none of these four has a lab arm with a result to quote | source, registries |
| Metadata only | No field exists for prompts, arguments, results or bodies; error text off by default; the proxy and the OpenCode plugin never send it; reads write no observation | source, `/about` |
| Private mode keeps a team's evidence to the team | Separate tables, never pooled or counted; tested end to end through the hook and the wrapper; the team can delete everything or rotate a leaked token | `tests/test_private.py`, `/setup` |
| Signing proves a reporter holds its key | Ed25519, checked against RFC 8032; a bad signature is refused, not downgraded. **Not** proof of a person | `tests/test_identity.py` |
| Nobody outside uses it yet | Production: **0 independent reporters**, 0 sparse rows, all traffic labelled as ours -- including, since 24 Sep, the lab evidence mirrored into production (every row first_party) | `/v1/stats`, front page |
| Production answers what the lab has learned | Since 24 Sep 10:48 the lab's own agents' reports on real public services are copied into production, first-party, every ten minutes, so a new user's agent gets the same kind of answer the lab's agents get. The evidence is still all ours | `failecho_mirror`, `/v1/stats` first_party count |
| It works before anyone joins | Five recoveries of your own and it starts recommending what worked, marked as your own history | `/llms.txt`, "Working alone" |

## Say only with the qualifier attached

| Claim | Required qualifier |
|---|---|
| The provider-group numbers | The asking agent **acts on the advice** (see above). The control retries **once after 3 seconds**. An agent that already falls back to another model on a 429 would get most of this without FailEcho -- that comparison is **not measured** (the careful twins run on Mistral and met no provider failure). Outages and timeouts show no difference. OpenAI and Anthropic are **not in the lab** (no paid providers), so neither network has evidence for them yet |
| "Runs finished" | A model run counts when it **returned a non-empty answer**. A grader checks the answers against the APIs: on real APIs **99.3% vs 99.2%** of checked values correct (147 vs 126 runs, p = 1.0), careful recovery 96.0% vs 96.9% (101 vs 96), production advice 96.8% vs 96.7% (31 vs 30). No measured difference in correctness |
| Real public APIs (70.1% vs 66.5% finished, p = 0.13) | No measured difference in finishing, and none in time lost either: 5.27 s vs 4.93 s per run (789 runs a side, p = 0.59). The commonest failure there, GitHub's 403, has no fix for anyone to find; asking costs one round trip (137 ms mean on the lab since its database was pruned on 23 Sep) |
| Coding agents in a VM (73.7% vs 73.7%, p = 0.98) | A tie: they fail on their own bugs, which no network of other agents' failures can help with |
| The MCP endpoint on its own | Models call a tool they have to choose **0.19 times per run**; the OpenCode pair with it is a tie (83.9% vs 77.4%, 31 runs a side). Lead with the in-path integrations |
| A team's own history tells it when to stop | One failure shape (Stack Exchange's exhausted quota), our own two-agent team: its private history said skip 7.4 h after the arm started (23 Sep 13:46); over the arm, **15.3 s vs 76.6 s** lost inside failures per run (104 vs 103 runs, p < 0.0001) and 238 retries skipped, with no difference in finishing (30.8% vs 27.2%, p = 0.57) -- time saved on a wall nobody could pass. No fix of its own yet |
| Lab scale | All ours |

## Do not say

| Not this | Why |
|---|---|
| "Install it and your agent recovers 74% vs 31%" | Only an agent that **acts on the advice** does. Show the three lines that switch model on `switch_model` and stop on `skip`; left to the model alone, no difference measured yet |
| "The proxy improves results" | Its group is **behind or tied** (29 runs a side, 89.7% vs 93.1% finished) and has carried **one** advice line in a scheduled run (05:37 UTC, 23 Sep, a 503). Say it is built and verified end to end, never that it helps |
| "The Claude Code plugin is proven" | No lab arm runs Claude Code. The plugin is built, published and tested end to end; it has no controlled result |
| "Just add the MCP endpoint and your agent gets smarter" | That path is the **weakest**: agents given the tools used them 0.19 times per run. Every public page ranks it last and says so |
| "The OpenCode plugin improves results" | Its lab arm started 23 Sep: **9 vs 10 runs**, no advice line yet. Built, tested end to end, unmeasured |
| "Private mode works with every FailEcho integration" | Not the MCP tools, by design (a secret does not belong in a tool argument), and not older package versions: `failecho-autoreport` before 0.1.7, `failecho-mcp` before 0.2.3 and the Claude Code plugin before 0.2.0 ignore `FAILECHO_TEAM` and report **publicly**. Say "from these versions" |
| "Signed reporters are real people" | A key is free to make. Signing rules out impersonation, nothing more |
| "Private mode makes a team's agents do better" | Works and is private -- that is tested. Its one result is a skip on one failure shape: it saves time there and changes no outcome. Say that, not "better" |
| "FailEcho makes agents more correct" | No measured difference: 99.3% vs 99.2% of checked answers on real APIs (147 vs 126 runs, p = 1.0) |
| "Proven to raise task completion" | Only in the provider group, only against a naive retry, only for an agent that acts on the advice, and only on runs that met a per-model rate limit |
| "It saves time even for a well-built agent" | Not measured to: against careful recovery the time lost is 9.8 s vs 10.1 s per run, p = 0.91 |
| "It uses fewer tokens" | 1,495 vs 1,536 per finished run, p = 0.66: no difference |
| "It works for OpenAI / Anthropic" | Neither is in the lab and no user has reported them: no evidence either way |
| "It detects errors everywhere: browser, payments, ..." | It records failures an agent already hit and says what fixed them; it does not detect anything. A browser adapter exists in the repository, unshipped and unmeasured; nothing for payments is built |
| "Independent agents confirm this" | Independent reporters: 0 |
| "Your secrets can never leak" | Defaults are narrow, but a URL path or an error string can carry an identifier. Say what is sent, not what cannot happen |
| "Three-second guarantee" | The advice budget is a socket timeout, not an end-to-end deadline |
| "Byte-for-byte for every MCP server" | True for a locally started server; a remote server's messages are parsed and re-serialised |

## Numbers to refresh before reuse

`lab.failecho.com/fleet` is live, and every table there now says the window it
covers. Production counters: `https://failecho.com/v1/stats`. The lab's
correctness numbers restarted at 05:17 UTC on 23 Sep, when grades began keeping
the truth they used; from then on every grader fix applies to all of them.
