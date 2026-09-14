# Reddit draft — r/mcp

Written to the rules in `docs/engagement-guide.md`. Target: **r/mcp first**,
flair Discussion if the sub has one.

**Two things to fix before posting:**

1. `[YOUR NUMBER]` in the first line. Use a real figure from a real session or
   cut the number entirely — "watching this happen" works fine. A made-up
   anecdote is the one thing in here that cannot be defended.
2. The recording. Record `/demo` playing, 30–60 seconds, and attach it. Reddit
   weights posts with media, and this post's whole argument is two lines of
   terminal output that are far better seen than described. Post it without
   the video only if you have decided not to make one.

---

## Title

```
Your agent has already solved this error. It just doesn't remember.
```

Backups, if that reads too much like a headline:

```
Does anyone else's agent keep rediscovering the same tool failure?
The retry is the default and it's usually wrong
```

## Body

---

I lost [YOUR NUMBER] minutes last week watching this:

```
> create_issue
  x 422 validation_error

> create_issue        (retry)
  x 422 validation_error

> create_issue        (retry, arguments tweaked slightly)
  x 422 validation_error
```

Three attempts, three identical failures, then it apologised to me, which
somehow made it worse.

The thing is it wasn't being stupid. Look at what it had:

```
422 validation_error
```

Tell me from that string whether retrying is worth it. Renamed field, or a
service having a bad ten minutes? Same six characters either way. One of those
fixes itself in thirty seconds; the other burns your budget while the real
answer was "the field is called `content` now, refresh your schema."

So it guesses. And it guesses retry, because that's what nearly all the code
it ever read does.

**Why "don't retry blindly" in the system prompt does nothing**

I tried that first. It doesn't work, for a boring reason: the instruction
doesn't contain the missing information either. You've told it to consider
whether the failure is transient. It still has no way to find out. You've asked
for the same guess, more thoughtfully.

And a retry that works *occasionally* is the worst case — that's a
variable-ratio schedule, which is the thing psychologists reach for when they
want a behaviour to resist extinction. The agent is on it. So are you, at 1am,
hammering the same test.

**What I built**

A shared endpoint the agent can ask before it retries. It reports the failure
as metadata and the outcome of whatever it tried next, and asks what worked for
anyone else who hit the same thing.

The part I got wrong for months is that I thought the value was other people.
It isn't, or not only. **Your own agent forgets between sessions.** Monday's
session works out that the field is `content` now and Monday's session is gone.
Thursday starts from `422` again.

So the first thing it fixes is your own amnesia, and that part needs nobody. An
action is recommended once **five recovery attempts** back it — attempts, not
failures, because a failure can't prove a fix — and those five can all be
yours. Every answer says which kind of evidence it is: `from_other_agents:
false` means it's your own history coming back to you.

**Specifics, since they're the only thing worth trusting**

- Recommendation threshold: 5 observed attempts. Full confidence weight: 3
  distinct reporters. Not 5,000.
- Confidence is a Wilson score lower bound, not a model's opinion — 5/5 scores
  0.57, 117/124 scores 0.89. Ten floating-point operations and you can
  recompute it by hand when you don't believe it.
- Below the evidence threshold it returns `INSUFFICIENT_DATA` rather than a
  guess with a low number bolted on.
- Metadata only: service, operation, error class and code, latency, and what
  fixed it. No prompts, no tool arguments, no results, no keys. Error text is
  off unless you turn it on.
- Runs after the call with a 2-second timeout, so if my server falls over your
  agent waits 2 seconds and carries on. `FAILECHO_DISABLED=1` kills it.

**The limit, before anyone asks**

The shared layer is new and the counter is on the front page, so check it
before you install anything rather than taking my word. The single-agent part
above works today. The cross-agent part needs other people and doesn't yet.

I'm not going to dress that up. What I'll say is that the bar is lower than it
sounds — five and three — and that the failures on popular MCP servers aren't
exotic. They're the same handful of renamed fields and rate limits hitting
everyone, which is why they'd cross those numbers quickly if more than one
person were looking.

**The question I actually can't answer**

Do different people's agent failures actually overlap?

The theory says yes. We're all calling the same twenty MCP servers, and when
GitHub renames a field it renames it for everyone at once. But "obviously true"
is where most wrong ideas live. It's equally plausible that the interesting
failures are all local — your auth setup, my rate limit, their internal
service — and the shared surface is too thin for any of this to matter.

I don't know which world we're in, and I don't think anyone has measured it.

So, genuinely: **what failure does your agent keep rediscovering?** The
specific one you've explained to it four times in four sessions. And would
anyone else calling that service have hit the same one, or is it yours?

I'll publish whatever the answer turns out to be, including "different people's
failures barely overlap", which is the result I most want to know and the one
that would make this whole thing pointless.

MIT, no account, no API key. `https://failecho.com/mcp` is the endpoint,
repo is at github.com/FailEcho/failecho.

---

## Why it's built this way

- **The 422 comes before the product.** Everyone in that sub has had that
  afternoon; nobody has been waiting for a failure network.
- **It gives something up early** — "the part I got wrong for months" — which
  is the Kiln move, and it's true.
- **The numbers are checkable.** 5, 3, 0.57, 0.89, 2 seconds. Real thresholds
  read differently from round ones.
- **The limit is stated before it's challenged.** Said first it's integrity;
  said after being caught it's damage control, and the same words score
  completely differently.
- **The ask is free and reversible.** No signup, and an off switch named in the
  post.
- **It ends on a question they can answer from experience**, which is the only
  reliable way to get comments instead of upvotes — and it worked on dev.to,
  where the top comment was a stranger explaining where the overlap actually
  breaks down.
- **It names the result that would kill the idea.** That is the strongest
  credibility move available to someone with no users.

## In the comments

- Expect **"so it does nothing yet?"** as the top comment. The answer is not
  yes: "It works on your own agent's history from the fifth recovery — that
  part needs nobody. The cross-agent part needs other people and is new." One
  line, no arguing.
- Expect **"how is this different from a status page / Sentry?"** A status page
  says a service is down. Sentry records that you failed. Neither records what
  fixed it, which is the only part another agent can use.
- Expect **"why would I send you my data?"** Answer with the contract, not
  reassurance: metadata only, error text off by default, reporter IDs hashed,
  MIT and self-hostable if the answer is still no.
- **Don't argue with the first critical comment.** It's usually the best one in
  the thread and everyone is watching how you take it.
