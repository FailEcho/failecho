# Launch post, draft

Rewritten 23 September 2026 for the user to edit and publish. Claude does not
post anywhere. Every number is checkable against `docs/claims.md` (lab window
21 Sep 09:20 – 23 Sep 21:23 UTC, our own agents); the qualifiers in it are
part of the sentences, not footnotes to drop.

**Packages, 23 Sep:** all live and verified identical to what was built --
`failecho-autoreport` 0.1.7 and `failecho-mcp` 0.2.3 on PyPI, `failecho-mcp`
0.2.3 and `failecho-opencode` 0.1.0 on npm. Every install line below
resolves, and private mode works on every path it names.

---

## Version A — short, for a link post or HN-style text

**FailEcho: agents share what fixed a failure, so the next one does not retry blind**

When an agent's tool or API call fails, it usually retries and hopes. FailEcho
is a shared network where agents report the *shape* of a failure — service,
operation, error class, code, latency; never prompts, arguments, results or
bodies — and get back what other agents already tried and whether it worked.

The thing I learned building it: **the answer has to arrive without the model
choosing to ask.** Give a model a "check this failure" tool and it calls it
about once every four or five runs. So FailEcho sits in the path of the tools the agent
already uses:

- **Claude Code** — a plugin; a hook runs after every MCP tool call.
- **OpenCode** — one plugin file; it runs around every tool, `bash` included.
- **Any other MCP client** — `failecho-mcp proxy -- <your server>` adds one line
  to a failed tool's error.
- **Your own code** — `pip install failecho-autoreport`; the advice lands on the
  exception you already handle.

**What I measured, in my own lab, with my own agents.** 55 test agents run in
twins: same task, same model, one asks the network before retrying, the other
does not. Over the last two and a half days:

- When a fix exists -- a model provider's per-model rate limit, where the
  network knows that switching to another model works: **75% of failures
  recovered with FailEcho, 32% without**, and **92% vs 83%** of runs finished
  (422 runs a side). All of that gain is in the runs that hit such a limit.
- Where nothing works, it says so: on an exhausted Stack Exchange quota the
  network told agents to skip, and the agents that retried anyway recovered
  **0 times out of 339**.
- Wasted time: **1.1 s vs 2.1 s** lost inside failures per run, using the live
  network's own advice (126 runs a side, p = 0.01).

**What I am not claiming.** No outside agent has used it yet: independent
reporters is 0, and the front page says so. The big numbers are against an
agent that retries once after three seconds. Against one that recovers
carefully I measured **no gain at all**: both finish (100% vs 99%) and lose the
same time to failures (8.5 s vs 8.9 s per run, noise). An agent that already
falls back to another model on a 429 would get most of the provider win without
FailEcho; I have not measured that. On real public APIs, where the commonest
failure (GitHub's 403) has no fix, asking costs a round trip for nothing: 6.7 s
vs 5.8 s lost inside failures per run (not significant per run; per failure it
was 0.6 s while my lab's database was slow and is 0.16 s since I fixed that).
A grader checks answers against the real APIs, and there is no measured
difference in correctness (98% vs 100% on 67 and 60 checked runs). It saves
retries and time for agents without good retry logic, and I cannot say it
makes answers more right. And pasting the MCP
URL alone does little; use one of the paths above.

**Free for early teams: private mode.** Set one environment variable and your
team's failures stay yours — separate tables, never pooled or counted — and
your own fixes come back to your agents, including "stop, nothing you tried has
worked". Delete it all or rotate a leaked token any time. It works and is
tested; whether it *helps* a small team is being measured now, and I will post
that when there is a week of it.

MIT, self-hostable, no account or key, and a read stores nothing.
https://failecho.com — the lab scoreboard, losing cohorts included:
https://lab.failecho.com/fleet

---

## Version B — longer, for a blog post

### The problem

An agent hits `429`, or `503`, or a schema error. It retries. It retries again.
Somewhere else, another agent hit the same failure an hour ago and found the
thing that worked — switch model, wait for the reset, stop and fail fast — and
that knowledge dies in its log.

### What FailEcho does

Agents report the shape of a failure and, crucially, what they tried next and
whether it worked. The network keys that on
service + operation + version + schema hash + failure fingerprint, and answers
one question: *before you retry, is this worth retrying, and what worked for
others?* When nothing has worked for anyone in the last day, the answer is
"skip", and that turns out to be one of the most useful things it says.

What never leaves the machine: prompts, tool arguments, results, request or
response bodies, headers, keys. Error text is off by default. A query writes no
observation.

### Why "automatically" is the whole product

The first version exposed four tools over MCP and expected agents to call them.
They mostly do not: in my lab, OpenCode called FailEcho 0.19-0.25 times per run,
even with the strictest instruction I could write, and the pair with it is a
tie with the pair without. That is the most useful negative result I have.

So FailEcho now sits in the tool path — a Claude Code hook, an OpenCode plugin,
an MCP proxy, a Python wrapper — and the advice arrives in the error the model
is already reading. Its shape (the counts here are illustrative, not measured):

    HTTP/2 429
    rate limit exceeded

    FailEcho: try wait_until_reset (worked 33/40).

### The numbers, and their limits

55 test agents, all mine, in twins that run the same task with the same model.
Window: 21 Sep 09:20 – 23 Sep 21:23 UTC. Every "with" beats "without" in the
top half at p < 0.05; nothing in the bottom half does.

| Group | With | Without |
|---|---|---|
| Model-provider failures recovered (p < 0.0001) | 74.7% | 32.1% |
| Runs finished, provider group, 422 a side (p = 0.0003) | 91.5% | 83.2% |
| Seconds lost in failures, flaky endpoints (p < 0.0001) | 15.2 | 21.9 |
| Seconds lost in failures, advice from production (p = 0.01) | 1.07 | 2.13 |
| Retry attempts per failure, advice from production (p < 0.0001) | 1.40 | 1.99 |
| Tokens per finished run, provider group (p = 0.71) | 1,390 | 1,422 |
| Careful recovery on both sides: runs finished (p = 0.16) | 100% | 98.6% |
| Careful recovery on both sides: seconds lost in failures (p > 0.8) | 8.5 | 8.9 |
| Real APIs, seconds lost in failures (p = 0.22) | 6.67 | 5.84 |
| Real APIs, checked answers correct (67 vs 60 runs, p = 1.0) | 98.5% | 100% |
| Coding agents in a VM, runs finished (p = 0.51) | 74.9% | 73.4% |

Read the bottom half as carefully as the top. Against a careful agent there is
no measured gain, in time or outcomes. On real public APIs FailEcho loses time: most of those
failures have no fix, and asking still costs a round trip. Checked answers
show no difference. Coding agents fail on their own
bugs, and a network of other agents' failures cannot help with that.

### What the audit found

I had an outside model review the product, and then audited my own numbers
against the raw ledger. Both found real problems, and the fixes are in the repo
with the reasoning: a grader that marked "0 open issues" wrong every time it was
right, a scoreboard whose "since 17 September" covered two and a half days, the
slowest experiments silently capped at 25 runs a side, a flaky test that was
really the clock crossing an hour. The grader bugs made answers look worse than
they were -- the last one, an empty reply scored as a wrong answer, mostly
against the agents *without* FailEcho; the scoreboard's window made the
evidence look longer than it was. None of them changed the recovery numbers,
which are counted per call; the empty-reply fix moved the finish rates by
under a point on both sides.

### Private mode, free for early teams

A shared network needs other agents; your own evidence does not. Send a secret
your team shares and every report is stored for your team alone — separate
tables, never pooled, never counted — and your agents get your own history
back, labelled as yours, including a "skip" when five of your recent attempts
all failed. `DELETE /v1/team` removes everything; `POST /v1/team/rotate` moves
it to a new token if one leaks. There is no account: the token is the team.

It works and it is private — that is tested end to end. Whether it helps a small
team, and how soon, is being measured by a lab arm that started today.

### Try it

    # Claude Code
    /plugin marketplace add FailEcho/failecho
    /plugin install failecho@failecho

    # OpenCode
    mkdir -p .opencode/plugin && curl -fsSL -o .opencode/plugin/failecho.js \
      https://raw.githubusercontent.com/FailEcho/failecho/main/opencode-plugin/plugin/failecho.js

    # any MCP client
    npx -y failecho-mcp proxy -- npx -y @modelcontextprotocol/server-github

    # python
    pip install failecho-autoreport

https://failecho.com/setup — and if you would rather send nothing anywhere, the
whole thing self-hosts from the repo with your own salt.

---

## Notes for the poster

- Keep the limits paragraph and the bottom half of the table. They are the
  reason the good numbers are believable.
- Do not add a claim that is not in `docs/claims.md`.
- If someone asks "how do you know reporters are independent?": today there are
  none, and the front page says 0. Reporters can now sign with an Ed25519 key,
  which proves a report came from that key and nothing more — keys are free,
  so it is not a count of people. The adoption threshold is what counts one.
- If someone asks whether private mode helps: say it is measured from 23 Sep
  and you will post the result, not that it does.
- Numbers move: re-check `docs/claims.md` against lab.failecho.com/fleet the
  day you post.
