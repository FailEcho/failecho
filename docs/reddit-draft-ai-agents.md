# Reddit draft — r/AI_Agents

Written 2026-09-15 for **Wednesday 16 Sep, 13:00–15:00 UTC**. Set an alarm;
the r/mcp post went up at 1am Eastern and that decided its fate before anyone
read it.

**This is a structure in roughly your voice. Rework it the way you did the
r/mcp draft** — the version that reads like you beats the version that reads
like a post. Keep the admission in the middle; it is the reason to believe the
rest.

## Why this post is different from the r/mcp one

r/mcp got the protocol story. This sub is framework people — LangChain,
LlamaIndex, CrewAI, cron jobs, local models running unattended. Their problem
is not "which MCP server"; it is "my pipeline hits the same wall every night
and the model never reports it because it is a 7B and it does not decide
things." Different problem, different artifact, same open question at the
end.

It also carries something the r/mcp post could not: a real negative result
from your own machine. That is the most credible sentence available and it
should stay.

## Flair

Whatever the sub's Discussion-type flair is. Not Showcase, not Project. Same
reasoning as before: the post is a question with a tool attached, and the
flair should match the post.

## The install line

Published to PyPI 2026-09-16 and verified file-by-file against the local
build, so the post can say:

    pip install failecho-autoreport

If the post already went out with the one-file curl line, edit it to this;
the file still works, pip is just shorter.

---

## Title

```
I built a tool to find agent failures that repeat across runs. My own logs had zero. Do yours?
```

Alternative:

```
Does your agent pipeline keep hitting the same failure? I couldn't find it in my own logs.
```

## Body

---

I want to check something before I build any more of it.

The theory: an agent that runs the same job repeatedly — nightly scrapes,
scheduled reports, a queue worker — hits the same external failure over and
over. A 429 from the same API. A 422 because someone renamed a field. And
because each run starts from nothing, it re-discovers the fix every time. On
a small local model it's worse, because the model never *reports* the
failure — it just retries, fails, and moves on.

So I built two small things and then tested the theory on myself.

**The test came back empty.** I wrote a scanner that reads Claude Code's own
session logs and looks for external tool failures that show up in more than
one session. On my machine: 28 sessions, 21 using MCP servers, 3 failures
total, **none repeated**. Retry tax: zero.

That's fine, it's honest. But my use is interactive — I don't run the same
task twice. If the repeat pattern exists, it lives in *your* workloads, not
mine, and I can't see those.

**So, the ask.** Two ways to check, depending on what you run:

**If you use Claude Code:** one file, standard library only, reads your local
logs, opens no network connection — a test asserts the imports. Paste the
table it prints if you're willing; it contains tool names and counts, never
error text or paths.

```
curl -O https://raw.githubusercontent.com/FailEcho/failecho/main/failecho_scan/__init__.py
python3 __init__.py
```

**If you run LangChain / LlamaIndex / your own loop:** there are no logs for
me to read, so this is a wrapper instead. It reports the *shape* of each tool
failure — service, operation, error class, duration — never arguments,
results or the error text. Never raises, never blocks; a dead endpoint costs
the caller about 3 ms. Wrap your tools for a week, then query and see if the
same failure came back.

```python
from failecho_autoreport import FailEcho
fe = FailEcho()
tools = fe.wrap(tools, service="github-mcp")   # or @fe.watch(...) on one call
```

Both are MIT, both are one file you can read in ten minutes, and the second
one talks to a network I run at failecho.com — which is currently empty, and
says so on the front page. No account. Self-host it if you'd rather send
nothing to me.

**What I actually want to know:**

What failure does your pipeline keep hitting? The one you've patched three
times. And is it something anyone else calling that same service would hit
too, or is it specific to your setup?

If the answer across enough people is "it's always specific to my setup,"
then the shared part of this is worthless and I'd rather find out now. I'll
post whatever the tables show either way, including "nothing repeats."

---

## Why it's built this way

- **The negative result is the credibility.** "I tested it on myself and
  found nothing" is a sentence no marketer writes, and it is the reason the
  reader believes the ask is genuine rather than a growth hack.
- **Two artifacts, matched to two audiences**, because the scanner only works
  for Claude Code users and this sub is mostly not that. Sending a LangChain
  user to a Claude Code log scanner would be the "advertising a path that
  doesn't work" mistake in a new costume.
- **Privacy claims are checkable, not asserted.** "A test asserts the
  imports" and "one file you can read in ten minutes" are the whole trust
  argument. No policy page could do better.
- **The counter is stated as zero**, before anyone checks. Someone will.
- **No adoption words, no "first," no "revolutionary."** Same rules as always.

## In the comments

- **"So it found nothing on your own machine and you're still building it?"**
  — Yes: my machine is the wrong workload for it, and I'd rather know than
  guess. If yours also finds nothing, that answers it. Say it once, don't
  argue.
- **"Why not just use LangSmith / a tracing tool?"** — Tracing records that
  *you* failed. This asks what fixed it *for other people*, which is the only
  part someone else's run can contribute. Different question.
- **"I'm not sending my failures to a stranger."** — Don't. The scanner sends
  nothing and can't. The wrapper is opt-in and self-hostable, MIT. The honest
  version of this network works on your own history alone from five
  recoveries.
- **"What's the model behind it?"** — There isn't one. It's counts and a
  Wilson score. Say that plainly; it disappoints some people and reassures the
  rest.
- **Do not argue with the first critical comment.** Same as always.
- **Anyone who pastes a table gets a real reply within minutes**, including
  "that's noise, here's why." That is the actual product of this post.
