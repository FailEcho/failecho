# dev.to post

Shape: the problem, then the thing that fixes it. The advice-shaped version of
this post is in git history if the pitch version does not land.

Voice: first person, casual, a few `:D` / `:3`. Sparse. Four in the whole post
reads as a person; twenty reads as noise.

Tags: `#ai` `#llm` `#opensource` `#python`

Title:

```
AI agents retry failures that can never succeed. I built a shared log so they stop.
```

Backups:

```
Your agent has hit this error before. It just doesn't know that.
The retry is the default and it is usually wrong
```

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

## The honest part

Before you install anything: that example output above is from the runnable
demo. It is not live traffic.

The network is empty. Zero independent agents have reported anything to it.
That counter is on the front page and it says zero, because a shared log with
one person in it is just a log :3

So I won't pretend it helps you today. It doesn't. It needs five to ten people
running it for a week before any fingerprint has enough behind it to read.

That's the ask. If you run agents against MCP servers, leave this on for a week
and see what it catches. It runs after the call with a two second timeout, so
the worst case when my server falls over is your agent waits two seconds.
`FAILECHO_DISABLED=1` kills it entirely.

I'll publish whatever it sees afterwards — including if the answer is "different
people's failures barely overlap", which is honestly what I most want to know.

## Why it is worth being early

Here's the part I like. Contributing costs you nothing you weren't already
paying. Those failures are happening to your agents this week regardless. The
only question is whether they evaporate or turn into something the next person
can read — and since the hook does the reporting, "contributing" means leaving
a switch on and forgetting about it.

And the bar is way lower than it sounds. An action needs five observed
attempts before it gets recommended, and three separate reporters before it
carries full weight. Five and three. Not five thousand. If ten of us point
this at the popular MCP servers for a week, the failures we all share cross
those numbers, and after that none of us is paying for the same mistake
alone anymore.

That's the whole bet. Either it works at ten people or the overlap isn't
there, and either way we find out in a week :3

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
- Expect "so it does nothing yet?" as the top comment. The answer is yes, said
  plainly, in one line, without arguing.
