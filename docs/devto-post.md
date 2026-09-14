# dev.to post

Shape: the problem, then the thing that fixes it. The advice-shaped version of
this post is in git history if the pitch version does not land.

Voice: first person, casual, a few `:D` / `:3`. Sparse. Four in the whole post
reads as a person; twenty reads as noise.

Tags: `#ai` `#llm` `#opensource` `#python`

Title:

```
Your agent has already solved this error. It just doesn't remember.
```

Backups:

```
AI agents retry failures that can never succeed. I gave mine a memory.
The retry is the default and it is usually wrong
```

The title deliberately says *your agent*, not *other people's agents*. The
first version led with the shared network, which asks the reader to bet on
strangers turning up. Nobody wants to be the first one in an empty room, and
the honest thing is that they do not have to be: the memory works on one
person's own history. The network is the upside, not the entry fee.

---

## Draft

## The problem

I lost about forty minutes last week watching this happen:

```
> create_issue
  x 422 validation_error

> create_issue        (retry)
  x 422 validation_error

> create_issue        (retry, slightly different arguments)
  x 422 validation_error
```

Three attempts. Three identical failures. Then it apologised to me, which
somehow made it worse :D

And the thing is, it wasn't being stupid. Look at what it had to work with:

```
422 validation_error
```

Tell me from that whether retrying is worth it. Renamed field, or a service
having a bad ten minutes? Same string either way. One of those fixes itself in
thirty seconds; the other eats your whole budget while the real answer was
"the field is called `content` now".

So it guesses. And it guesses retry, because that's what nearly all the code
it ever read does.

Here's the bit that bugs me. **Somebody already hit this**, probably this
week, on the same MCP server, and they already found out whether retrying
works. That knowledge exists. It's in someone else's scrollback where nothing
can reach it, so every agent pays full price to learn it again.

## What would actually fix it

Not a better prompt. Not a bigger model either — this isn't a reasoning
problem. What's missing is a fact nobody has: *what happened to everyone else
who hit this exact thing.*

You need three pieces to get there:

- a stable id for "this exact failure", so two agents can tell they hit the
  same thing
- the outcome of what each of them tried next — not what they intended, what
  actually worked
- successes too, or your failure rate means nothing. 100 failures out of 200
  calls is an outage. 100 out of a million is a Tuesday.

## So I built it

So I built **FailEcho**. It's a shared failure log for agents: one agent
reports a failure and what it tried, and the next agent to hit the same thing
gets told, instead of guessing.

### Claude Code

**1.** Paste these two lines at the Claude Code prompt — not in a terminal:

```
/plugin marketplace add FailEcho/failecho
/plugin install failecho@failecho
```

**2.** Start a new session. Claude Code reads hooks at session start, so
installing mid-session leaves you with a plugin that looks installed and does
nothing. I've done this and spent ten minutes sure my own thing was broken :D

**3.** Check it took: type `/plugin`. FailEcho should be listed as *enabled*.

That's it. From then on every MCP failure gets looked up and recorded
automatically. You don't remember anything and the model doesn't decide
anything — it's a hook, it just runs after the call.

### Any other MCP client

Cursor, Claude Desktop, your own framework — paste the endpoint into the
client's MCP settings as type `http`:

```json
"failecho": {
  "type": "http",
  "url": "https://failecho.com/mcp"
}
```

That gives you four tools: `check_tool_failure` before a retry, plus
`report_tool_failure`, `report_tool_success` and `report_recovery_outcome` for
contributing. Without the hook your agent has to call these itself, so put a
line about it in your system prompt or it won't bother.

### Host can only start a process

Some hosts cannot speak HTTP at all. Same relay, either runtime, pick whichever
you already have:

```json
{ "mcpServers": { "failecho": { "command": "uvx", "args": ["failecho-mcp"] } } }
```

```json
{ "mcpServers": { "failecho": { "command": "npx", "args": ["-y", "failecho-mcp"] } } }
```

Both are ours, both forward to the same network, both store nothing locally.
The Node one has no dependencies and starts in about a second.

### No MCP at all

One HTTP call. This one stores nothing and is never rate limited:

```bash
curl -X POST https://failecho.com/v1/query \
  -H "Content-Type: application/json" \
  -d '{"service": "api.github.com", "operation": "create_issue",
       "error_type": "rate_limit", "error_code": "429"}'
```

### Or make the agent do it

None of the above? Can't be bothered reading a setup page? Fair. Hand it to
the agent — there's an `llms.txt` sitting there for exactly this. Paste this
at whatever you're running:

```
Read https://failecho.com/llms.txt and set yourself up to use FailEcho.
```

It has the endpoint, the config block, all four tools with when to call each,
and the rules about what must never be sent. I tried it and the agent got it
first try, which honestly surprised me :3

## What comes back

Here's the real thing, right now, from the curl above:

```json
{
  "known": false,
  "status": "INSUFFICIENT_DATA",
  "observations": { "total": 0, "unique_reporters": 0 },
  "recovery_actions": [],
  "recommendation": null,
  "evidence_sources": []
}
```

Yeah. Nothing. Nobody has reported that signature, so it says so instead of
making something up. That's today's honest output and I'd rather show you it
than a screenshot from my demo folder :3

Once a fingerprint has evidence behind it, the empty arrays fill in:
`recovery_actions` gets one entry per action anyone tried, with attempts,
successes and a confidence score, and `recommendation` names the best one.

Two things about that I'd argue with anyone about. The confidence is a Wilson
score lower bound over the actual attempts — no model produced it, and you can
recompute it from the counts printed next to it. And it only appears once five
attempts and three separate reporters exist. Below that you get the empty
answer above, because a recommendation from one person's single lucky retry is
worse than nothing.

On what leaves your machine: the service, the operation, an error class and
code, how long the call took. That's it. No prompts, no tool arguments, no
results, no headers, no keys. Error text is normalised server-side and the
original thrown away. MIT, no account, no key.

## The part that works with nobody else involved

Here is the thing I got wrong when I started building this. I thought the
whole value was other people. It isn't, and it took me a while to notice.

Your agent hits the same failure across different sessions. Not other
people's agents — yours. Monday's session works out that the field is
`content` now, and Monday's session is gone. Thursday's session starts from
`422 validation_error` again, with nothing.

So the first thing this fixes is your own amnesia.

A recovery action gets recommended once **five recovery attempts** back it.
Note attempts, not failures — reporting the same failure five times gets you
nothing, correctly, because a failure cannot prove a fix. What counts is what
happened *after*. And those five can all be yours. Hit the thing five times,
let the hook record what fixed it each time, and the sixth time it tells you,
before another agent has ever connected.

Every answer says which kind of evidence it is:

```
from_other_agents: false   -> your own history, coming back to you
from_other_agents: true    -> somebody else already paid for this one
from_other_agents: null    -> you sent no reporter_id, so it cannot be known
```

Confidence is discounted below three distinct reporters, so your own evidence
counts for less than a crowd's. It just doesn't count for nothing.

## And then the part that needs other people

The shared layer is new. I'm not going to put a fake number on it — the live
counter is on the front page and you can read it yourself before you install
anything.

What I'll say instead is that the bar is lower than it sounds. Five attempts
for a recommendation, three reporters for full weight. Five and three, not
five thousand. The failures on popular MCP servers are not exotic; they are
the same handful of renamed fields and rate limits hitting everyone, which is
exactly why they cross those numbers quickly once more than one person is
looking.

And contributing costs nothing you weren't already paying. Those failures are
happening to your agents this week regardless. The hook does the reporting, so
"contributing" is leaving a switch on and forgetting about it. It runs after
the call with a two second timeout — worst case, my server falls over and your
agent waits two seconds. `FAILECHO_DISABLED=1` kills it entirely.

I'll publish what it sees, including if the answer turns out to be "different
people's failures barely overlap". That is genuinely the thing I most want to
know, and it is the kind of result that only exists if somebody runs the
experiment :3

https://failecho.com

---

## Before publishing

**Two lines are written as things you personally did. Make them true or change
them — a made-up anecdote is the one thing in here that cannot be defended:**

- "I lost about forty minutes last week watching this happen" — the opening.
  Use a real number from a real session, or cut the number and say "watching
  this happen".
- "I tried it and the agent got it first try" — about pasting the llms.txt
  line. Paste it at your own agent once, then it is true.

Then the rest:

- The sample output is a real production response. Re-run the curl before
  publishing and paste what comes back, so it is true on the day.
- Never show demo numbers as if they were the network. An "11 observations,
  6 reporters" block next to "the network is empty" reads as either a lie or
  an author who does not use his own product.
- Do not add a user count, a star count, or a "trusted by". There are none.
- Weekday morning UTC. Answer every comment for the first few hours.
- Expect "so it does nothing yet?" as the top comment. The answer is **not**
  yes any more, and it was wrong when the earlier draft said so. The answer is:
  "It works on your own agent's history from the fifth recovery — that part
  needs nobody. The cross-agent part needs other people and is new." One line,
  no arguing, no defensiveness.
- Do not write "be the first" or "help me get started" anywhere. It is a
  deterrent: nobody wants to be the only person in the room, and the product
  does not actually require it. Say what it does for one person, let the
  network be the upside.
- If asked directly how many people use it, say the number. It is on the front
  page and a reader who checks and finds you dodged is worse than any number.
