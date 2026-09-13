# dev.to warm-up post

**Post this first, two or three days before `devto-post.md`.**

Long-form, in the shape that does well on dev.to right now: a named concept,
a real scientific anchor, an ASCII diagram, a table, and a question at the end.

The one thing deliberately not copied from that format is invented metrics.
Posts in this genre often close with a results table full of impressive
numbers. Ours has a table too, and every figure in it is zero, because that is
what is true. That inversion is the most defensible thing in the piece and
probably the most memorable.

The previous warm-up (the 11pm debugging one) is in git history.

Tags: `ai`, `programming`, `discuss`, `opensource`

---

## dev.to front matter

```
---
title: Your AI Agent Has No Colleagues
published: false
description: Every agent rediscovers the same failures alone, at full price. Ants solved this problem 60 million years ago.
tags: ai, programming, discuss, opensource
---
```

---

## Draft

> "The coordination of the builders is not direct. It is the work already done
> that directs and triggers the work that follows."
>
> — Pierre-Paul Grassé, describing termites, 1959

## 1. The Loop

You have seen this. Every agent framework does it:

```
> create_issue
  x 422 validation_error

> create_issue        (retry)
  x 422 validation_error

> create_issue        (retry, arguments tweaked slightly)
  x 422 validation_error
```

Three attempts. Three identical failures. Then it apologises to you, which
somehow makes it worse.

The instinct is to blame the model. But look at what it had to work with:

```
422 validation_error
```

Tell me, from that string, whether retrying is worth it.

Is it a field renamed in last week's release, or a service having a bad ten
minutes? Same six characters either way. In one case retrying is exactly right
and works in thirty seconds. In the other you can retry until your budget is
gone, and the real answer was "the field is called `content` now, refresh your
tool schema."

The model has to guess. It guesses retry, because that is what nearly all the
code it ever read does.

## 2. Why "don't retry blindly" in your system prompt does nothing

The first instinct, once you notice this, is to write a rule.

```
When a tool call fails, do not retry immediately.
Consider whether the failure is transient before trying again.
```

It sounds reasonable. It does approximately nothing, for a boring reason:
**the instruction does not contain the missing information either.**

You have told the agent to consider whether the failure is transient. It still
has no way to find out. You have asked it to make the same guess, more
thoughtfully. On a hard task, under context pressure, it will guess retry
again — and it will be right often enough that the behaviour never extinguishes.

That last part is the trap. A retry that works occasionally, at unpredictable
intervals, is a **variable-ratio reinforcement schedule** — the same mechanism
that makes slot machines difficult to walk away from. It is the schedule
psychologists reach for when they want a behaviour to be maximally resistant to
extinction. Your agent is on it. So are you, at 1am, hammering the same test.

## 3. What humans actually do instead

Watch how a senior engineer resolves the same 422.

They do not reason about it. They turn around and say "hey, has anyone seen
this?" — and someone across the room says "oh, they renamed that field on
Tuesday."

Three seconds. No analysis. The knowledge existed; it was just in someone
else's head.

That is the entire mechanism, and it is not intelligence. It is **population**.
A senior engineer is not smarter than your agent. They are *networked* — into a
team, a Slack channel, a Stack Overflow thread written by a stranger in 2019
who hit the same wall and left a note.

Your agent has none of that. It is a brilliant engineer working alone in a room
with no colleagues, no chat, no history. Every failure is the first time anyone
has ever seen it.

## 4. Ants solved this 60 million years ago

In 1959 the French zoologist Pierre-Paul Grassé was studying termites building
a nest, and hit a puzzle: the colony produces an elaborate, coherent structure,
but no termite has the plan, and no termite tells another what to do.

His answer was a word he coined for it: **stigmergy**, from the Greek *stigma*
(mark) and *ergon* (work). Coordination by trace.

A termite deposits a pellet of soil. The pellet changes the local environment.
The next termite, encountering the modified environment, is more likely to
deposit its own pellet there. Nobody communicated. Nobody remembered. **The work
already done directed the work that followed.**

Ants do the same with pheromone trails. An ant that finds food leaves a trail
home. Other ants follow it and reinforce it. A trail to nothing evaporates. The
colony converges on good routes without a single ant understanding the map.

Notice what stigmergy is *not*. It is not messaging. It is not shared memory.
The ants never meet. The signal lives **in the environment**, and it is left by
individuals who are gone by the time it is useful to anyone.

That is exactly the shape of the problem with agents. Agent A hits the 422 on
Monday and its context window is destroyed. Agent B hits it on Thursday with no
idea Agent A ever existed. They will never meet, they cannot message each
other, and they do not need to — if there is somewhere to leave the pellet.

## 5. What the trace has to contain

The temptation is to log the error and call it a trail. That does not work,
because an error message is not a signal about what to *do*.

A useful trace needs three parts:

```
┌──────────────────────────────────────────────────────────────────┐
│  THE ANATOMY OF A USEFUL FAILURE TRACE                           │
├──────────────────────────────────────────────────────────────────┤
│  1. AN IDENTITY                                                  │
│     A stable id for "this exact failure", so two agents can      │
│     tell they hit the same thing. Not the raw string: that one   │
│     contains a request id and a timestamp and will never match   │
│     anything again.                                              │
├──────────────────────────────────────────────────────────────────┤
│  2. AN OUTCOME, NOT AN INTENTION                                 │
│     What the next agent tried, and whether it worked. "I         │
│     refreshed the schema" is worthless. "I refreshed the schema  │
│     and the call then succeeded" is the whole point.             │
├──────────────────────────────────────────────────────────────────┤
│  3. A DENOMINATOR                                                │
│     Successes too, or the failure rate is meaningless. 100       │
│     failures out of 200 calls is an outage. 100 out of a         │
│     million is a Tuesday.                                        │
└──────────────────────────────────────────────────────────────────┘
```

Part 1 is fiddly and worth spelling out. These two are the same bug:

```
Repository 8823 rejected field body at 2026-09-11T14:02:11Z
Repository 41902 rejected field body at 2026-09-12T09:41:55Z
```

Compare them raw and you have two unrelated incidents forever. So you normalise
first — replace the parts that vary, keep the parts that mean something — and
hash what is left together with the service and operation:

```python
text = URL_RE.sub("<URL>", text)
text = UUID_RE.sub("<UUID>", text)
text = TIMESTAMP_RE.sub("<TS>", text)
text = LONG_NUMBER_RE.sub("<N>", text)
```

Both lines collapse to one shape. Now they are one thing you can count. It is
also a good place to strip anything credential-shaped, since you are already
walking the string with regexes and you very much do not want tokens in a
shared log.

And once you are counting, resist the urge to have a model score the result.
Count it. If an action was tried 5 times and worked 5 times, that is 5/5 — but
so is 117/124, and those are not equally trustworthy. A Wilson score lower
bound folds sample size in for you: 5/5 scores about **0.57**, 117/124 scores
about **0.89**. Ten floating point operations, no dependencies, and you can
recompute it by hand when somebody asks where the number came from.

When there is not enough evidence, return that. Not a guess with a low
confidence bolted on — an actual "I don't know". Agents handle it fine.

## 6. So I built the pheromone trail

It is called **FailEcho**. Agents report tool failures and recovery outcomes as
metadata; the next agent to hit the same fingerprint gets told what worked
instead of guessing.

In Claude Code it is two lines. Any other MCP client points at an endpoint.
There is a REST API if you do not want MCP, and you can hand the whole job to
the agent — there is an `llms.txt` written for exactly that.

Now the part where this post stops resembling the genre it is written in.

Articles like this usually end with a results table. Here is mine, live at the
time of writing:

| Metric | Value |
|---|---:|
| Independent agents reporting | **0** |
| Distinct reporters, last 24h | **0** |
| Cross-agent recoveries recorded | **0** |
| Known failure fingerprints | 3 |

That is not modesty. **The pheromone trail is empty.** Every number above is on
the front page of the site, unrounded, and the three fingerprints are mine.

A stigmergic system with one participant is not a colony. It is one ant walking
in a circle.

## 7. The actual question

Here is what I genuinely do not know, and cannot find out alone:

**Do different people's agent failures overlap at all?**

The theory says they should. Everyone is calling the same twenty MCP servers
and the same dozen public APIs, and when GitHub renames a field it renames it
for all of us at once. But I have no evidence, and "obviously true" is where
most wrong ideas live.

There is one number that decides it. A recovery action needs five observed
attempts before it is recommended, and three distinct reporters before it
carries full weight. **Five and three.** Not five thousand. If ten people point
this at the popular MCP servers for a week, the failures we share cross those
thresholds and we all stop paying separately for the same mistake. If they
never cross, the overlap is not there and the idea is wrong — which is also
worth knowing, and I will publish that too.

It runs after the tool call with a two second timeout, so the worst case when
my server falls over is your agent waits two seconds. One environment variable
turns it off. MIT, no account, no API key.

https://failecho.com

## Your turn

Two things I would genuinely like to know, whether or not you ever install
anything:

- **What failure does your agent keep rediscovering?** The one you have
  explained to it four times.
- **Would you leave a reporter on for a week?** And if not — what is the thing
  that stops you? I would rather hear that now than guess at it.

---

## Before publishing

- Re-check the four numbers in the table against
  `https://failecho.com/v1/stats` on the morning you post. They are the most
  scrutinised thing in the piece precisely because they are zeros.
- The Wilson figures (0.57 and 0.89) are computed from the standard formula at
  z=1.96. Recheck if you quote different counts.
- Grassé coined *stigmergie* in 1959 studying termites; the etymology is
  *stigma* + *ergon*. Both are correct as written. Do not embellish the history.
- Do not add a results table, a benchmark, or a "forthcoming preprint". There
  is no study here, and this genre is full of posts that invent one.
- Answer every comment for the first day. The post exists for the comments.
