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
- [x] Verified 2026-09-14: `https://failecho.com` 200, `/mcp` answers
      `tools/list` 200, `/v1/stats` returns JSON. Re-check on the morning.
- [x] Repo is public (verified 2026-09-14). Re-read the README's first screen
      before posting and check it still answers "what is this and how do I
      connect" without scrolling.
- [x] The install commands in the post were run end to end on 2026-09-12:
      `claude plugin marketplace add FailEcho/failecho` then
      `claude plugin install failecho@failecho` installs and loads both the
      MCP server and the hook. Re-check if either manifest changes.
- [x] Load tested again 2026-09-14, against production for reads and a
      throwaway instance for writes. Reads, through Cloudflare: 10 concurrent
      539 req/s p99 50 ms; 50 concurrent 277 req/s p99 905 ms; 200 concurrent
      167 req/s p99 7.0 s. Zero failures at every level, memory flat at ~80 MB
      of a 400 MB ceiling, no restarts. Writes serialise at ~100/s (SQLite has
      one writer) and memory stayed at 123 MB under 120 concurrent writers
      with the limiter off. With the production limiter on, a mixed flood
      returned 1380 × 200 and 132 × 429 with reads still answering in 1.6 ms:
      excess writes are shed and readers are not starved. It degrades, it does
      not fall over. The bottleneck is CPU (2 cores), not memory. Re-run
      `.venv/bin/python scripts/loadtest.py` if the code changes again.
- [x] Penetration tested 2026-09-14. Four findings, all fixed: no request body
      limit (a 32 MB body was parsed, taking the worker to 100 MB), no
      security headers at all, `/docs` executing Swagger UI from a CDN, and
      control characters accepted into stored metadata. Reads only against
      production; every write test ran on a throwaway instance so the adoption
      number stayed honest.
- [ ] Read the current stats and be ready to say them out loud, split by
      source. As of 2026-09-14: `real_observations_total` 0,
      `real_reporters_24h` 0, `first_party_observations` 20, and 9 of 44
      queries in the last 24 hours were answerable from evidence. The last
      number is the interesting one — there is demand and no corpus — and it
      is a better answer to "is anyone using this" than the zero alone.
- [ ] Never seed the database to look busier. Your own agents' real calls,
      labelled first-party with the operator token, are fine: they are
      disclosed on the page and in every answer. Synthetic rows are not.

## 1b. Blocking, as of 2026-09-14

Three things gate the post, and none of them are code:

- [ ] **The three blanks in §5 are still blank.** They get asked every time.
- [ ] **The warm-up post is unpublished** (`published: false` in
      `docs/devto-post-warmup.md`). It exists so the author is not a stranger.
- [ ] **`real_observations_total` is 0.** See `docs/posting-calendar.md` for
      the argument that this should move the date rather than be explained
      away in the thread.

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

In Claude Code, two commands:

    /plugin marketplace add FailEcho/failecho
    /plugin install failecho@failecho

That connects the MCP server and installs a hook, so failures are looked up
and reported without the model having to remember to. Any other MCP client:
point it at https://failecho.com/mcp (streamable HTTP, no auth, no key) for
the four tools -- check_tool_failure, report_tool_failure,
report_tool_success, report_recovery_outcome. If your host can only start a
local process, `uvx failecho-mcp` or `npx -y failecho-mcp` runs a relay that
forwards to the same network and stores nothing itself. There is a plain REST
API too if you don't want MCP.

It is useful before anybody else joins, which is the part I got wrong for a
while. A recovery action is recommended once five attempts back it, and those
five can all be yours -- hit the same failure five times, let it record what
fixed it, and it answers on the sixth. Every recommendation carries
`from_other_agents`, so you can tell your own history from somebody else's.

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

**"The agent already has the error message. Why does it need a network?"**
The premise-level objection, and the one to answer best. The error text says
what failed. It does not say whether retrying works: `422 validation_error`
reads the same whether a field was renamed permanently or the service is
having a bad ten minutes, and the model guesses retry in both. What the
network adds is the outcome distribution — 0/5 retries worked, 5/5 schema
refreshes did — which no single agent can derive from its own view. Concede
the real limit: for a well-documented public API the model often does already
know the fix. The value concentrates in what it cannot know — a breakage from
this week, an undocumented change, and which of several plausible fixes
actually worked.

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
Half right, and the half that is wrong is worth correcting once, calmly: five
attempts recommend an action and all five can come from one reporter, so it
answers you from your own repeats before anyone else exists. Confidence is
discounted below three distinct reporters, so your own evidence counts for
less than a crowd's -- it does not count for nothing.

Then concede the rest. What exists is labelled first-party, kept out
of adoption, and every answer says so in `evidence_sources`; point at that
rather than arguing. The bet is that the protocol is worth having before the
data is, and the volunteer ask is how the data stops being yours.

**"My failures are unique to my stack, so overlap is zero."**
This is the real objection and the honest answer is that you don't know yet —
which is why it's one of the two questions in the post. The overlap that
plausibly exists is on shared surfaces: GitHub, Stripe, Slack, OpenAI, AWS
APIs, and popular MCP servers. Internal services obviously won't overlap.

**"`service` is free text. Three people will name the same server three ways
and never match."**
True, and the weakest joint in the design. `_canon` in
`app/core/fingerprint.py` is strip plus casefold and nothing else, so
`github`, `github-mcp` and `api.github.com` are three different fingerprints.
Two things reduce it in practice: the Claude Code hook derives the name from
the server's own `serverInfo.name`, or its public package name, so hook users
converge without thinking about it, and the docs pin the convention. A REST
caller can still type anything. Aliasing is the obvious fix and it is not
built. Do not argue this one — conceding it earns more credibility than any
other answer in the thread, because it shows you have read your own code.

**"You run a hook on every tool call that phones home. What does that cost me,
and what happens when your box is down?"**
It runs *after* the tool call, never in front of it, so it is not in the path
of the call. Two-second client timeout: if FailEcho is unreachable the agent
loses one 2 s timeout and carries on, and Claude Code caps the hook at 10 s
regardless. Failure path only, apart from a counter POST on success.
`FAILECHO_DISABLED=1` turns it off without uninstalling anything.

**"I could poison it with fake recoveries."**
Yes, partially. Current floor: one reporter contributes at most 5 attempts per
hour to the same fingerprint+action, confidence is discounted below 3 distinct
reporters, and writes are rate limited to 120/min per IP. Concede clearly that
an attacker with many IPs and patience can still skew a fingerprint, that
there is no auth today, and that reputation is the obvious next thing if the
network gets real traffic worth attacking.

**"SQLite on one box will melt on the front page."**
Measured on the production box (one process, 500 MB VPS, 100 MB resident):
the homepage and its live data serve 287 req/s with zero failures at 50
concurrent connections, p50 108 ms, and Cloudflare caches in front of that. The agent query path does
~60 req/s, about 5 million queries a day. At 200 concurrent nothing fails;
latency climbs to a few seconds. Writes are rate limited per client by design.
Say the number, not an adjective.

**"Who are you?" / "What is the business model?" / "Will you publish the
data?"**
Three factual questions about you rather than about the code, and improvising
them reads as evasion. Decide all three before Tuesday and write them here:

- Who is behind it: ______________________________________________
- Whether it is ever monetised: __________________________________
- Whether the aggregate dataset gets published: __________________

Two notes. Anything that sounds like a company that does not exist will be
checked and will cost more than the plain answer would have. And "no plans to
monetise" is an ordinary, well-received answer on HN — what damages you is
inventing a pricing tier live in the thread. On the data question, yes is a
strong answer for a network asking strangers to seed it, and it is also the
answer that makes the volunteer ask in the post credible.

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
