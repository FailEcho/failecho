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

You have seen this. Every agent framework does it:

```
> create_issue
  x 422 validation_error

> create_issue        (retry)
  x 422 validation_error

> create_issue        (retry, slightly different arguments)
  x 422 validation_error
```

Three attempts, three identical failures, and then it apologises to me about
it :D

The reason it does this is not that the model is stupid. It is that the error
message does not contain the information needed to make the decision.

```
422 validation_error
```

Is that a field renamed permanently in the last release, or a service having
a bad ten minutes? The string is byte-for-byte the same in both cases. In one,
retrying is exactly right and works in thirty seconds. In the other, you can
retry until your budget runs out, and the answer was "refresh the tool schema,
the field is called `content` now".

The model has to guess, and its prior is retry, because that is what most code
in its training data does.

Here is the part that bothers me. **Somebody else already hit this.** Probably
this week, probably on the same MCP server, and they already found out whether
retrying works. That knowledge exists and there is nowhere for it to go, so
every agent rediscovers it alone, at full price, forever.

## What would actually fix it

Not a better prompt, and not a bigger model. The missing thing is not
reasoning, it is an observation nobody has: *what happened to everyone else
who hit this exact failure.*

That needs three pieces:

- a stable id for "this exact failure", so two agents can tell they hit the
  same thing
- the outcome of what each of them tried next — not what they intended, what
  actually worked
- successes too, or the failure rate is meaningless. 100 failures out of 200
  calls is an outage. 100 out of a million is Tuesday.

## So I built it

**FailEcho** is a shared failure log for agents. One agent reports a tool
failure and what it tried; the next agent to hit the same failure gets that
instead of guessing.

In Claude Code it is two lines:

```
/plugin marketplace add FailEcho/failecho
/plugin install failecho@failecho
```

That installs an MCP server and a hook, so failures get reported and looked up
after every tool call without the model having to remember to do it. Any other
MCP client points at `https://failecho.com/mcp`. There is a plain REST API if
you do not want MCP at all.

What comes back looks like this:

```
Fingerprint:            6ed9ef705ff4037af2c977306b8b9f92
Known failure:          YES
Observed failures:      11
Independent reporters:  6

Recovery actions others reported:
  refresh_schema        5/5 (100.0%) confidence 0.57 reporters 5
  retry                 0/5 (0.0%)   confidence 0.00 reporters 5

Best observed recovery: refresh_schema
  Skipping retry: other agents already proved it does not work here.
```

Two things I would defend about that output. The confidence is a Wilson score
lower bound over observed attempts — no model produces it, and you can
recompute it from the counts shown. And when the evidence is thin it returns
`INSUFFICIENT_DATA` and no recommendation, which is a real answer rather than a
guess with a small number attached.

It sends metadata only: the service, the operation, an error class and code,
how long the call took. Never prompts, tool arguments, tool results, headers,
keys or anything from you. Raw error text is normalised server-side and the
original thrown away. MIT, no account, no API key.

## The honest part

That example output is from the runnable demo, not from live traffic.

Right now the network is empty. Zero independent agents have reported
anything; the counter is on the front page and it says zero, because a shared
log with one participant is just a log :3

So I am not going to tell you it will help you today. It will not. It needs
about five to ten people running it for a week before any fingerprint has
enough behind it to be worth reading.

That is the actual ask. If you run agents against MCP servers, leave it on for
a week and see what it catches. It runs after the tool call with a two second
timeout, so the worst case when my server is down is that your agent waits two
seconds, and `FAILECHO_DISABLED=1` turns it off entirely.

I will publish whatever the network sees afterwards, including if the answer
turns out to be "different people's failures barely overlap at all", which is
genuinely the thing I most want to find out.

https://failecho.com

---

## Before publishing

- The sample output must match what the demo actually prints. Run
  `python examples/live_agent/run_demo.py` and copy from it rather than from
  this file.
- Do not add a user count, a star count, or a "trusted by". There are none.
- Weekday morning UTC. Answer every comment for the first few hours.
- Expect "so it does nothing yet?" as the top comment. The answer is yes, said
  plainly, in one line, without arguing.
