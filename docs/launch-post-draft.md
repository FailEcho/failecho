# Launch post, draft

Written 22 September 2026 for the user to edit and publish. Claude does not
post anywhere. Every number is checkable against `docs/claims.md`; the
qualifiers in it are part of the sentences, not footnotes to drop.

---

## Version A — short, for a link post or HN-style text

**FailEcho: agents share what fixed a failure, so the next one does not retry blind**

When an agent's tool or API call fails, it usually retries and hopes. FailEcho
is a shared, anonymous network where agents report the *shape* of a failure
(service, operation, error class, code, latency — never prompts, arguments,
results or bodies) and get back what other agents already tried and whether it
worked.

The part I care about is that nobody has to remember to ask. Three paths
deliver the answer inside the failure itself:

- a Claude Code plugin (a hook, no model decision);
- `pip install failecho-autoreport` — advice attached to the exception;
- `failecho-mcp proxy -- <your MCP server>` — one line added to the failed
  tool's error, for any MCP client.

**What I measured, in my own lab, with my own agents.** 45 test agents run
around the clock in twins: same task, same model, one asks the network before
retrying, the other retries by fixed rules. Over four days, on failures where a
fix exists (model-provider rate limits and outages):

- failures recovered: **77% with, 17% without**
- runs that finished: **94% vs 84%**
- tokens per finished run: **1,835 vs 1,984**

On deliberately flaky endpoints it is mostly about waste: 21.3 s vs 28.3 s per
run, and 316 retries the network told agents to skip.

**What I am not claiming.** No outside agent has used it yet: independent
reporters is 0, and the front page says so. A "finished run" means the agent
returned an answer, not a verified-correct one. The control is a plain retry
after 3 seconds, so this is a win over naive retrying, not over careful
recovery engineering. Pasting the MCP URL alone is *worse* than nothing in my
lab (the model rarely calls a tool it has to choose) — use the plugin, the
wrapper or the proxy. The proxy's own group is currently behind and has not yet
shown a single advice line in a scheduled run; I am fixing that measurement
rather than quoting it.

It is MIT, self-hostable, needs no account or key, and reads store no
observation. Site: https://failecho.com — lab scoreboard, with the losing
cohorts included: https://lab.failecho.com/fleet

---

## Version B — longer, for a blog post

### The problem

An agent hits `429`, or `503`, or a schema error. It retries. It retries again.
Somewhere else, another agent hit the same failure an hour ago and found the
thing that worked — switch model, wait for the reset, refresh the schema — and
that knowledge dies in its log.

### What FailEcho does

Agents report the shape of a failure and, crucially, what they tried next and
whether it worked. The network keys that on
service + operation + version + schema hash + failure fingerprint, and answers
one question: *before you retry, is this worth retrying, and what worked for
others?*

What never leaves the machine: prompts, tool arguments, results, request or
response bodies, headers, keys. Error text is off by default and is normalised
server-side when it is sent at all. A query writes no observation; it adds 1 to
a daily counter of answered/unanswered queries, and that is the whole of it.

### Why "automatically" is the whole product

The first version of this exposed four tools over MCP and expected agents to
call them. They mostly do not. In my lab, OpenCode called FailEcho 0.12 times
per run, and that cohort ended up *worse* than the one without FailEcho,
because the tool definitions cost tokens in every step and bought almost
nothing. That is the most useful negative result I have.

So the product is now the three automatic paths above. The advice arrives in
the error the model is already reading:

    HTTP 403 rate limit exceeded: {"message":"API rate limit exceeded for ..."}
    FailEcho (evidence from api.github.com): no clear fix yet; on this service's
    other operations, agents tried wait_until_reset worked 2/11, backoff worked
    104/2330.

Which in that case means: stop retrying, this is not going to work.

### The numbers, and their limits

45 personas, ~90,000 observations, 13,000 recovery outcomes, four days, all my
own agents. Twins run the same task with the same model, alternating order.

| Group | With | Without |
|---|---|---|
| Model-provider failures recovered | 77.3% | 17.2% |
| Runs finished (provider group) | 94.0% | 83.5% |
| Tokens per finished run | 1,835 | 1,984 |
| Flaky endpoints, seconds per run | 21.3 | 28.3 |
| Real APIs (GitHub, PyPI, npm, crates, Stack Exchange) | 68.3% | 64.0% |
| Coding agents in a VM | 84.0% | 83.7% |

The last row is the honest shape of it: coding agents mostly fail on their own
bugs, and a network of other agents' failures cannot help with that.

Limits, stated once and plainly: zero independent reporters so far; "finished"
means an answer was returned, not verified correct; the control is a naive
retry; the proxy group is unproven; nothing here sees an agent's own `curl`.

### An outside review, and what it broke

I had a strong model review the whole thing — code, published packages, the
scoring, the claims. It found no fabricated numbers and several claims wider
than the measurement, plus real defects: a 5xx containing the word "invalid"
filed as a validation error; the proxy honouring an error-text switch it
promised to ignore; an advice line that hid the server's own "these attempts
are failing lately" warning. All fixed and published. The findings live in the
repo with their status, including the ones I have not done yet.

### Try it

    # any MCP client
    npx -y failecho-mcp proxy -- npx -y @modelcontextprotocol/server-github

    # python
    pip install failecho-autoreport

https://failecho.com/setup — and if you would rather send nothing anywhere, the
whole thing self-hosts from the repo with your own salt.

---

## Notes for the poster

- Keep the limits paragraph. It is the reason the good numbers are believable.
- Do not add a claim that is not in `docs/claims.md`.
- If someone asks "how do you know reporters are independent?": today, you do
  not — ids are self-chosen and a restart mints a new one. Persistent signed
  identity is the next piece of work. Say that.
