# Posting to Reddit

Written 2026-09-14. The dev.to warm-up got seven comments in a day by asking a
question rather than announcing a thing, and Reddit rewards that shape harder
than dev.to does.

## Before anything: the account problem

Most relevant subreddits filter new accounts automatically, and a removed post
usually says nothing — it just never appears. Check before writing:

- **Account age and karma.** Many subs need 30+ days and some comment karma.
  If yours is new, spend a few days commenting on other people's posts first.
  Not as a tactic; the filters exist because the alternative is spam.
- **Read the sidebar rules of each sub**, individually. They differ a lot, and
  "I built a thing" is fine in one and an instant removal in the next.
- **Post from your real account**, the one with the dev.to profile linked if
  possible. A one-week-old account posting a link to its own site is the exact
  shape of what those filters are for.

## Where, in order

### 1. r/mcp — go here first

Smallest, most relevant, most forgiving of a new project. The readers already
know what an MCP server is, so the post can be about the *idea* rather than
spending four paragraphs explaining the protocol.

Flair: whatever the sub uses for a project or a discussion. If there is a
"Show" or "Project" flair and also a "Discussion" one, **take Discussion** —
the post is a question, and the flair should match the post rather than the
intention.

### 2. r/AI_Agents — second, a day or two later

Bigger, still relevant. Technical framing, no marketing voice at all. This sub
is tired of launch posts; it responds to specific problems with specific
numbers.

### 3. r/LocalLLaMA — only with a data angle, and only later

Large and openly hostile to hosted services, for good reasons. Do not post
"I built a hosted network" here. The version that works is a *finding*: "here
is what 10,000 agent failures look like in aggregate", once there is data.
Until then, skip it.

### Also possible, lower priority

- **r/ClaudeAI** — there is a Claude Code plugin, so it is on-topic. Rules on
  self-promotion are strict; read them.
- **r/ChatGPTCoding** — broad audience, mixed quality of discussion.

### Do not

**r/programming, r/webdev, r/coding.** Wrong audience, and they punish
anything that reads as promotion. A removal there costs nothing except the
post; the downvote pattern that comes first is what costs.

## The shape that works

Not "I built X, check it out". Lead with the question you actually cannot
answer, mention the thing as context for why you are asking, end by asking
for their failure. That is what earned seven comments on dev.to and none of
them were about the product.

The engagement topic is already sitting there and it is genuinely open:

> **Do different people's agent failures actually overlap, or is everyone's
> interesting failure local to their own setup?**

That question is worth asking whether or not FailEcho exists, which is exactly
why it does not read as marketing. It also has a real answer that nobody has
measured, and Reddit likes being the one to settle something.

## A draft to rewrite in your own words

Do not paste this. Your dev.to comments sound like you and this does not —
rewrite it, cut it, put your own annoyance in it. It is here as a structure,
not as copy.

---

**Title:** Does your agent keep rediscovering the same tool failure? Trying to
find out if they overlap between people

Body:

> Something that has been bugging me for a while.
>
> My agent hits a `422` on an MCP server, retries it three times with slightly
> different arguments, fails all three, apologises. The actual answer was that
> a field got renamed and it needed to refresh the tool schema. Fine — but the
> annoying part is that it worked that out on Monday, and on Thursday, in a new
> session, it started from zero again.
>
> So I built a thing that gives it a memory: report the failure and what fixed
> it, and next time ask before retrying. That part works on your own history —
> five recovery attempts and it starts telling you, and they can all be yours.
>
> The part I can't answer alone is whether it's worth sharing between people.
> The theory says yes: we're all calling the same twenty MCP servers, and when
> GitHub renames a field it renames it for everyone. But "obviously true" is
> where most wrong ideas live. It's equally plausible that everyone's
> interesting failures are local — your auth setup, my rate limit — and the
> shared surface is too thin to matter.
>
> So: **what failure does your agent keep rediscovering?** And do you reckon
> anyone else calling that service would hit the same one, or is it yours?
>
> (The thing is at failecho.com if it's relevant, it's MIT and there's no
> account. Mostly I want the answer to the question though.)

---

## Why the draft is built that way

- **The product appears once, near the bottom, in parentheses.** Reddit can
  smell the reverse instantly.
- **It says what works alone**, so nobody has to be the first person in an
  empty room — and it does not say "be the first", which is a deterrent.
- **It does not claim adoption**, because there is none, and someone will check.
- **It ends on a question the reader can answer from experience**, which is the
  only reliable way to get comments rather than upvotes.

## Timing and follow-up

- **Tuesday to Thursday, 13:00–15:00 UTC.** Never Friday evening, never a
  weekend.
- **One subreddit per day at most.** The same text in three subs on one
  afternoon is the fastest way to get flagged as spam by both the filters and
  the readers.
- **Answer every comment**, including the dismissive ones, for the first few
  hours. "How is this different from a status page?" is a fair question with a
  real answer: a status page tells you a service is down; this tells you which
  specific call is failing for other people and what fixed it.
- **Do not argue with the first critical comment.** It is usually the most
  useful one in the thread, and arguing is what people remember.
