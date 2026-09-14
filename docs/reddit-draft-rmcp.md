# Reddit draft — r/mcp

**This is the owner's own draft, lightly corrected.** It is kept in his voice
on purpose: the dev.to comments proved he writes more like a person than any
draft I produce, and Reddit is unusually good at spotting text written to be
posted rather than written to be read. Do not let anyone (me included) smooth
this into marketing prose.

Target **r/mcp** first.

## Flair: `discussion`

The sub offers: no flair, resource, question, server, article, discussion,
events, job, showcase, connector.

Take **discussion**. The flair is read as a declaration of intent before a
word of the post is, and this post's intent is a question you cannot answer
alone. Discussion is also the only flair on that list that does not promise
the reader something to consume, which is right, because what you want back
is their failure, not their attention.

The two that will tempt you are the wrong ones:

- **`showcase`** frames it as "look at my thing". The post then has to earn
  attention it has already asked for, and every reader arrives primed to
  judge rather than answer.
- **`server`** is literally accurate -- it is an MCP server -- and that is the
  trap. It files the post next to every other server announcement, where the
  question at the end reads as a device rather than the point.

`question` is the honourable fallback if a mod moves you or discussion is
somehow unavailable. It is slightly worse only because it undersells that
there is a working thing behind the question.

Do not post with **no flair** in a sub that offers them. In most subs it
reads as someone who did not read the sidebar, and some filter it outright.

## Tags

Reddit has no tags beyond flair, so there is nothing else to fill in here.
For the cross-posts later: dev.to takes four, and `#mcp #ai #devtools
#opensource` is the set that matches this post. Hacker News takes none --
the title is the whole surface.

**Before posting:** attach the recording if you make one
(`docs/record-the-demo.md`). Everything else below is ready.

---

## Title

```
Your agent has already solved this error. It just doesn't remember.
```

## Body

---

Something that has been bugging me for a while.

Sometimes my agent hits a `422` on an MCP server, retries three times with
slightly different arguments, fails all three, and then apologises to me. The
actual answer was that a field got renamed and it needed to refresh the tool
schema.

That's fine, it happens. The annoying part is that I run several agents and
one of them worked this out on Monday — and on Thursday, in a new session, it
started from `422 validation_error` again with nothing. The session that knew
is gone.

So the idea is a thing that gives it a memory: report the failure and what
fixed it, and next time ask before retrying. That part works on your own
history — five recovery attempts and it starts telling you, and those five can
all be yours. No one else needed.

It costs basically nothing to leave on. It runs after the tool call with a
2-second timeout, so if my server falls over your agent waits 2 seconds and
carries on, and `FAILECHO_DISABLED=1` turns it off completely. It sends
metadata only: the service, the operation, the error class, and what fixed it.
No prompts, no tool arguments, no results, no keys.

If you want to try it, the laziest way is to paste this at your agent and let
it do the work:

```
Read https://failecho.com/llms.txt and set yourself up to use FailEcho.
```

It reads that and configures itself, whatever client you're on. If you'd
rather do it by hand, it's the usual MCP config block:

```json
{"mcpServers": {"failecho": {"type": "http", "url": "https://failecho.com/mcp"}}}
```

And if you don't want MCP at all, it's one POST:

```
curl -sX POST https://failecho.com/v1/query -H 'Content-Type: application/json' \
  -d '{"service":"api.github.com","operation":"create_issue",
       "error_type":"rate_limit","error_code":"429"}'
```

**The part I can't answer alone** is whether it's worth sharing between
people.

The theory says yes — we're all calling the same twenty MCP servers, and when
GitHub or Stripe renames a field or tightens a payload limit, it does it for
everyone at once. Your 422 on Tuesday and mine on Thursday are plausibly the
same 422.

But "obviously true" is where most wrong ideas live. It's equally plausible
that everyone's interesting failures are local — your auth setup, my rate
limit, someone's internal service — and the shared surface is too thin for any
of this to matter. If that's the world we're in, this is worthless and I'd
rather know.

So: **what failure does your agent keep rediscovering?** The specific one
you've explained to it four times in four sessions. And do you reckon anyone
else calling that service would hit the same one, or is it yours?

I'll publish whatever the answers show, including "they barely overlap".

It's at failecho.com if it's relevant — no account, no API key. Code is at
github.com/FailEcho/failecho, MIT, self-hostable if you'd rather not send
anything to me.

---

## What I changed, and why

Kept: the rhythm, the "bugging me", the shrug of "that's fine, it happens",
the direct question at the end. Those are why it reads as a person.

**Corrected for accuracy:**

- *"apologises with my AI guy"* → *"apologises to me"*. The original reads as a
  typo rather than as voice.
- *"with my AI army it only worked that out on Monday"* → names the actual
  point, which is session amnesia: it knew on Monday, the session died, Thursday
  starts from nothing. That is the whole argument for the product and it was the
  blurriest sentence in the draft.
- *"when they do something with GitHub it's coming again"* → *"when GitHub or
  Stripe renames a field or tightens a payload limit, it does it for everyone at
  once"*. Same idea, but a reader who has not already had the thought can follow
  it. Stripe added because one example reads as a one-off and two read as a
  pattern.
- *"your auth setup and the shared surface is too thin"* → separated into the
  two things it was compressing: the examples of local failures, and then the
  conclusion.

**Added, because they were gaps you marked:**

- The install line. It was `or tell your ai to set it up on...` — now the
  actual prompt, and it is the cheapest thing in the post.
- The cost paragraph. "What if your server breaks my agent" is the first real
  objection anyone has, and answering it before it is asked is worth more than
  answering it after. 2-second timeout, off switch, metadata only.
- `github...` filled in, with MIT and self-hostable, which is the answer to
  "why would I send you anything".

**Not added, on purpose:**

- No adoption claims. The counter is public and someone will check.
- No "be the first". Nobody wants an empty room, and the memory works alone.
- No feature list. The post is a question with a product attached, not the
  other way round.

## In the comments

- **"So it does nothing yet?"** — "It works on your own agent's history from
  the fifth recovery. That part needs nobody. The cross-agent part is new."
  One line, no arguing.
- **"How is this different from Sentry / a status page?"** — A status page says
  a service is down. Sentry records that you failed. Neither records what fixed
  it, which is the only part another agent can use.
- **"Why would I send you anything?"** — The contract, not reassurance:
  metadata only, error text off unless you turn it on, reporter IDs hashed
  before storage, MIT and self-hostable if the answer is still no.
- **Do not argue with the first critical comment.** It is usually the best one
  in the thread, and everyone is watching how you take it.
