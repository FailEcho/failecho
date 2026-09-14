# Reddit draft — r/mcp

Short version. The long one was three screens; nobody reads three screens from
a stranger. Target **r/mcp** first, Discussion flair if the sub has one.

**Before posting:** replace `[N]` with a real number from a real session, or
cut the number. And attach the recording — see `docs/record-the-demo.md`. The
whole argument is two lines of terminal output, and they are far better seen
than described.

---

## Title

```
Your agent has already solved this error. It just doesn't remember.
```

## Body

---

I lost [N] minutes last week watching my agent retry a `422` three times with
slightly different arguments, fail all three, and apologise.

It wasn't being stupid. This is everything it had:

```
422 validation_error
```

Renamed field, or a service having a bad ten minutes? Same six characters. One
fixes itself in thirty seconds, the other eats your budget while the real
answer was "the field is called `content` now."

So it guesses. It guesses retry, because that's what nearly all the code it
ever read does. And a retry that works *occasionally* is a variable-ratio
schedule, which is the thing you use when you want a behaviour to survive
everything you throw at it. Your agent is on one.

**The annoying part isn't even that.** It's that my agent worked this out on
Monday and started from zero again on Thursday, because the session that knew
was gone.

So I built it a memory. It reports the failure and whatever fixed it, and asks
before retrying next time.

**Install is one line — paste it at your agent:**

```
Read https://failecho.com/llms.txt and set yourself up to use FailEcho.
```

It reads that and configures itself. No account, no API key, nothing to sign
up for. Or `claude mcp add --transport http failecho https://failecho.com/mcp`
if you'd rather do it yourself.

**What it costs you:** nothing you weren't already paying. It runs after the
call with a 2-second timeout, so if my server dies your agent waits 2 seconds
and carries on. `FAILECHO_DISABLED=1` turns it off. Metadata only — service,
operation, error class, what fixed it. No prompts, no arguments, no results,
no keys.

**What you get back, alone:** five recovery attempts and it starts answering,
and those five can all be yours. Thresholds are 5 attempts and 3 reporters for
full weight — not five thousand. Confidence is a Wilson lower bound, not a
model's opinion: 5/5 scores 0.57, 117/124 scores 0.89, and you can recompute
it by hand when you don't believe it. Below the threshold it returns
`INSUFFICIENT_DATA` instead of a guess.

**The limit, before anyone asks:** the shared layer is new. The counter is on
the front page — check it before you install anything rather than taking my
word. The single-agent part works today; the cross-agent part needs other
people and doesn't yet.

**Here's what I actually want from this thread.**

Do different people's agent failures overlap at all?

The theory says obviously — we're all calling the same twenty MCP servers, and
when GitHub renames a field it renames it for everyone. But "obviously" is
where most wrong ideas live. Maybe every interesting failure is local: your
auth setup, my rate limit, their internal service. Then this is worthless and
I'd like to know.

**So: what failure does your agent keep rediscovering?** The specific one
you've explained to it four times in four sessions. And would anyone else
calling that service have hit the same one — or is it yours?

If enough answers turn out to be the same failure, that settles it. I'll
publish what it shows either way, including "they barely overlap", which is
the result that kills the idea.

MIT, self-hostable. github.com/FailEcho/failecho

---

## The moves in it

- **Effortless install.** One line, near the top, before any explanation of
  how it works. `llms.txt` means they do not even read the docs.
- **Cheap.** The 2-second timeout and the off switch are in the post, because
  "what if your thing breaks my agent" is the real objection and answering it
  before it is asked is worth more than answering it after.
- **Long-term benefit.** Five and three, not five thousand, and the memory pays
  off on your own history — no waiting for a crowd.
- **A challenge, not a pitch.** "Maybe this is worthless and I'd like to know"
  invites a reader to prove something rather than to buy something.
- **Does not burn tokens on errors** is the actual value proposition and it is
  stated as a cost saved, not a feature.
- **The engagement topic is the last thing they read**, and it is answerable
  from experience in one comment.

## In the comments

- **"So it does nothing yet?"** — "It works on your own agent's history from
  the fifth recovery. That part needs nobody. The cross-agent part is new."
  One line, no arguing.
- **"How is this different from Sentry / a status page?"** — A status page says
  a service is down. Sentry records that you failed. Neither records what
  fixed it, which is the only part another agent can use.
- **"Why would I send you anything?"** — Answer with the contract, not
  reassurance: metadata only, error text off by default, reporter IDs hashed
  before storage, MIT and self-hostable if the answer is still no.
- **Do not argue with the first critical comment.** It is usually the best one
  in the thread, and everyone is watching how you take it.
