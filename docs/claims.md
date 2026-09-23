# Claims we can defend, and the exact evidence

Updated 22 September 2026. Every number here is from the lab
(`lab.failecho.com`), produced by **our own agents**; production has 0
independent reporters. If a line below is used anywhere public, the "our own
agents" part goes with it.

Two rules. A claim not in the first table does not get made. A number that
moves gets re-checked here before it is repeated.

## Safe to say

| Claim | Exact evidence | Where to check |
|---|---|---|
| When a fix exists, agents recover far more often | Model-provider failures: **77.3% recovered with FailEcho, 17.2% without**; 97 vs 93 failures met, so both sides met the same wall | lab scoreboard, provider group |
| That turns into finished work | **94.0% vs 83.5%** of runs marked completed, ~530 runs a side, four days | same |
| It costs less, not more, where it helps | **1,835 vs 1,984 tokens** per completed run in that group | same |
| It cuts wasted retries | Flaky endpoints: **21.3 s vs 28.3 s** per run, 1.51 vs 2.0 attempts per failure, **316** retries the network said to skip. Real APIs: **249** skips | test and real groups |
| The skip verdict is usually right | Where askers were told to skip, blind agents retried 63 times on GitHub and recovered **5** | advice table |
| Advice arrives without the model choosing to ask | Three automatic paths shipped: Claude Code plugin, `failecho-autoreport` 0.1.6, `failecho-mcp` 0.2.2 (PyPI + npm), all verified from the registries in clean installs | PyPI, npm |
| Metadata only | No field exists for prompts, arguments, results or bodies; error text off by default; the proxy ignores the error-text switch entirely; reads write no observation | source, `/about` |
| Nobody outside uses it yet | Production: **0 independent reporters**, 0 sparse rows, lab traffic labelled as ours | `/v1/stats`, front page |
| It works before anyone joins | Five recoveries of your own and it starts recommending what worked, marked as your own evidence | `/llms.txt`, "Working alone" |

## Say only with the qualifier attached

| Claim | Required qualifier |
|---|---|
| "Runs completed" numbers | A run counts when it **returned an answer**, not a correct one; builder/OpenCode runs by file pattern. Deliverable grading is not built yet |
| Any comparison at all | The control is **one retry after 3 seconds**, not competent recovery engineering |
| Real-API gain (68.3% vs 64.0%) | Small, and mostly fewer wasted retries rather than more correct work |
| Lab scale (89,822 observations, 13,035 recovery outcomes) | All ours |

## Do not say

| Not this | Why |
|---|---|
| "The proxy improves results" | Its group is **behind or tied** (85.0% vs 90.0% on 20 runs a side, 23 Sep) and **no advice line has appeared in a scheduled run**. Say it is built and verified end to end, never that it helps |
| "Just add the MCP endpoint and your agent gets smarter" | That path is the **weakest**: agents given the tools used them 0.19-0.24 times per run. Every public page now ranks it last and says so. Lead with the plugin (Claude Code, OpenCode), the proxy, or the wrapper |
| "The OpenCode plugin improves results" | Its lab arm started 23 Sep and has **no runs** yet. Built, tested end to end, unmeasured |
| "Install from npm: `failecho-opencode`" | Not published. The single-file install from GitHub is the only real path until the user publishes it |
| "Private mode works with every FailEcho integration" | The published `failecho-autoreport` (0.1.6) and `failecho-mcp` packages do not read `FAILECHO_TEAM`; a team using them reports **publicly**. Say which integrations work (REST, Claude Code plugin 0.2.0+, OpenCode plugin file) until the next release |
| "Proven to raise task completion" | Grading is shallow and the control is weak; see the qualifiers |
| "Independent agents confirm this" | Independent reporters: 0 |
| "Your secrets can never leak" | Defaults are narrow, but a URL path or an error string can carry an identifier. Say what is sent, not what cannot happen |
| "Three-second guarantee" | The advice budget is a socket timeout, not an end-to-end deadline |
| "Byte-for-byte for every MCP server" | True for a locally started server; a remote server's messages are parsed and re-serialised |

## Numbers to refresh before reuse

`lab.failecho.com/fleet` is live. `docs/report-48h.md` is the written version,
refreshed 20 Sep. Production counters: `https://failecho.com/v1/stats`.
