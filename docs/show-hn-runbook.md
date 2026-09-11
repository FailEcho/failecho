# Show HN runbook

Everything needed to launch FailEcho on Hacker News, in the order it happens.
Written 2026-09-11. Target slot: **Tuesday 2026-09-15, 08:00-10:00 ET
(12:00-14:00 UTC)**.

The single rule that decides whether this works: you must be at the keyboard
answering comments for the two hours after you submit. A Show HN with no author
in the thread falls off /new without ever reaching the front page.

---

## 1. Pre-flight (do this the day before, not on launch morning)

- [ ] HN account exists and can post. New accounts can post Show HN with zero
      karma, but an account created minutes before the post looks like a
      throwaway. If the account is brand new, make it now and leave it.
- [ ] `https://failecho.com` loads, `https://failecho.com/mcp` answers
      `tools/list`, `https://failecho.com/v1/stats` returns JSON.
- [ ] The GitHub repo is public and the README's first screen answers "what is
      this and how do I connect" without scrolling.
- [ ] Re-run `python scripts/loadtest.py` against the origin and write the
      numbers down. You will be asked "what happens when HN hits it".
- [ ] Read the current stats and be ready to say them out loud, split by
      source: `real_observations_total` (independent agents) and
      `first_party_observations` (your own agents). On 2026-09-11 both were 0.
- [ ] Never seed the database to look busier. Your own agents' real calls,
      labelled first-party with the operator token, are fine: they are
      disclosed on the page and in every answer. Synthetic rows are not.

## 2. Submitting

1. Go to https://news.ycombinator.com/submit
2. **Title** goes in the title field. **Leave the text field empty** and put
   `https://failecho.com` in the url field. A Show HN should link to the thing.
3. Submit. You land on the item page.
4. **Immediately** post the first comment below as a top-level comment. Not
   five minutes later — the first readers arrive within seconds.
5. Do not upvote your own post from another account, do not ask anyone to
   upvote, do not post the link in a group chat asking for support. HN detects
   voting rings and will bury the post silently with no notification.

## 3. Title

Primary:

```
Show HN: FailEcho – agents check what already failed before they retry
```

Backup, if you prefer naming the protocol:

```
Show HN: FailEcho – shared failure intelligence for AI agents over MCP
```

The primary describes the behaviour rather than the category, and avoids
leading with "MCP", which a share of the HN audience is tired of. Either is
fine. No exclamation marks, no "revolutionary", no emoji.

## 4. First comment

Post verbatim, as a top-level comment, immediately after submitting.

```
I kept watching agents burn retries on failures that could never succeed — a
renamed API field, a stale tool schema, a permission change. Every agent
rediscovers these alone, and the right move is often not "retry".

FailEcho is a small shared network for that. An agent reports a tool failure;
the server normalizes the message (identifiers stripped, secrets redacted)
into a fingerprint; agents report what recovery they tried and whether it
worked. The next agent to hit that fingerprint gets the evidence instead of
guessing.

Connect over MCP: https://failecho.com/mcp (streamable HTTP, no auth, no key).
Four tools: check_tool_failure, report_tool_failure, report_tool_success,
report_recovery_outcome. There is a plain REST API too if you don't want MCP.

Honest state of it: no independent agent has reported anything yet. Anything
in it so far comes from my own agents, labelled first-party. The live page
and every answer (`evidence_sources`) say so, and none of it counts as
adoption. You can check the split at https://failecho.com/v1/stats. So this is
a protocol and a nearly empty room, not a product with a graph to show you.

Deliberately boring stack: FastAPI, SQLite in WAL mode, one process, about
100 MB of RAM on a small VPS.

Two design decisions I'd defend:

- Confidence is a Wilson score lower bound over observed attempts, capped at
  0.99, and discounted when fewer than three distinct reporters back it. No
  model produces it, and you can recompute it from the counts returned.
- Successes are collected too, not only failures. 100 failures out of 200
  calls and 100 out of 1,000,000 are very different, and a network that only
  hears about failures cannot tell them apart.

The two things I'd genuinely like input on: how much cross-tenant failure
overlap actually exists outside popular public APIs, and whether recovery
outcome reports ever arrive in practice — an agent has to come back and tell
the network after it recovered, which is the fragile part of the whole idea.

If you run agents against MCP servers and would leave this on for a week, I'm
looking for 5–10 people to do exactly that. I'll publish what the network sees
afterwards, whatever it turns out to be.
```

## 5. Objection playbook

These come up in roughly this order. Answer briefly, concede real limits, and
never argue.

**"This is Sentry / Rollbar / Datadog with extra steps."**
Those are per-tenant: your errors, your dashboard, read by a human afterwards.
FailEcho is cross-tenant, and the consumer is the agent at the decision point,
before the retry. It also stores which recovery worked, which error trackers
do not model at all. Fair follow-up you should concede: for a single team with
one agent, Sentry is genuinely more useful than this.

**"Why would I send my errors to a stranger's server?"**
Nothing is collected that could carry payload: no prompts, API keys,
Authorization headers, cookies, tool arguments, tool results, request or
response bodies. Raw error text is normalized by regex and then discarded —
only the fingerprint survives. Reporter IDs are optional and salted-hashed.
Point at `app/core/normalize.py`; it is short and readable, and the argument
lands better as code than as a promise. Then say the honest part: it is
self-hostable, and a team that does not want to share can run its own.

**"The network is empty, so this does nothing." / "It's just your own data."**
Correct, and it is in the post. What exists is labelled first-party, kept out
of adoption, and every answer says so in `evidence_sources`; point at that
rather than arguing. The bet is that the protocol is worth having before the
data is, and the volunteer ask is how the data stops being yours.

**"My failures are unique to my stack, so overlap is zero."**
This is the real objection and the honest answer is that you don't know yet —
which is why it's one of the two questions in the post. The overlap that
plausibly exists is on shared surfaces: GitHub, Stripe, Slack, OpenAI, AWS
APIs, and popular MCP servers. Internal services obviously won't overlap.

**"I could poison it with fake recoveries."**
Yes, partially. Current floor: one reporter contributes at most 5 attempts per
hour to the same fingerprint+action, confidence is discounted below 3 distinct
reporters, and writes are rate limited to 120/min per IP. Concede clearly that
an attacker with many IPs and patience can still skew a fingerprint, that
there is no auth today, and that reputation is the obvious next thing if the
network gets real traffic worth attacking.

**"SQLite on one box will melt on the front page."**
Give the measured numbers from your pre-flight run. Reads are cached at the
origin for 10s and Cloudflare caches in front of that; the write path is what
would hurt, and writes are the rare case. Say the number, not an adjective.

**"Why MCP?"**
Because it's how an agent already reaches a tool, so integration is a URL
rather than a library. REST exists for everyone else.

## 6. What kills a Show HN

- The author not being in the thread.
- Replying defensively to the first critical comment. The first critical
  comment is usually the most valuable one in the thread.
- Marketing voice. No "excited to share", no "game-changing".
- Asking for upvotes anywhere.
- Any claim that turns out to be false. One person will check.

## 7. If it doesn't take off

Most Show HNs don't. It is not evidence the idea is wrong. Do not repost the
same link the next day — HN treats that as spam. You can resubmit once after a
few weeks with a genuinely different title, or wait until there is real data
and post "Show HN: what 10,000 agent failures look like", which is a much
stronger post than this one because it has a finding in it.

The MCP Discord post can go out the same week regardless.
