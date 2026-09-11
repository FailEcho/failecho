# Blog post + X runbook

Two low-cost channels. Neither is a launch; both are compounding. Written
2026-09-11.

---

# Part 1 — The engineering post

## Do I need a new account?

**dev.to** — yes, but it's 30 seconds: https://dev.to/enter, "Continue with
GitHub". No karma, no waiting period, no approval. You can publish immediately.

**Hashnode** — yes, separate account, also GitHub sign-in. Hashnode gives you a
subdomain (`fuyuki0.hashnode.dev`) or lets you map your own.

**Do you need both?** No. Post on **dev.to** and stop. It has more traffic for
this topic, its posts rank well in Google, and one post you actually write
beats two you half-write. If you later want Hashnode too, cross-post and set
the canonical URL to the dev.to original (Hashnode has a "canonical URL" field
in post settings) so Google doesn't treat it as duplicate content.

**Do not** publish it on `failecho.com`. You have no blog there, adding one
means touching the frozen backend, and the point of this channel is borrowing
someone else's traffic.

## The title

The one I suggested earlier is wrong and you should not use it. `loadtest.py`
fires 200 concurrent **requests**, not 200 agents. Claiming agents would be the
first thing a reader checks and the first thing you'd lose credibility on.

Use:

```
The metric took down the endpoint it was measuring
```

with subtitle / dev.to tags: `#python #sqlite #fastapi #webdev`

Backup titles:

```
Our API returned 500 under load. The bug was in the analytics, not the query.
A UNIQUE constraint on a counter nobody reads
```

## Why this post works

It is a bug story with a real fix, a reproducible cause, and a lesson that
generalizes past your project. Nobody reads "introducing my product". People
read "here is a mistake you are probably also making". The FailEcho link sits
in paragraph one as context and again at the end — that's enough.

## Draft

Publish roughly as-is. Verify the numbers first (see "Before publishing").

---

**The metric took down the endpoint it was measuring**

I run a small service called FailEcho — a shared network where AI agents
report tool failures so other agents can check what already failed before
they retry. One HTTP endpoint matters more than all the others: `/v1/query`,
the one an agent calls at the moment it is deciding whether to retry.

Before launching I load tested it. At 50 concurrent requests, fine. At 200,
`/v1/query` started returning HTTP 500.

The error:

```
UNIQUE constraint failed: daily_counters.day, daily_counters.name
```

That table is not on the query path. It holds one integer per day per counter
name — how many queries hit a known fingerprint, how many hit nothing. It is
telemetry. Nobody's answer depends on it.

The code was the obvious version:

```python
row = await session.get(DailyCounter, (day, name))
if row is None:
    session.add(DailyCounter(day=day, name=name, value=amount))
else:
    row.value += amount
```

Read, then write. Two concurrent requests both find no row for today, both
INSERT, and the second one hits the unique constraint. The exception
propagates out of the request handler and the caller gets a 500.

So an agent, already in the middle of handling a failure, asked the network
what to do — and got an error, because two requests raced on a **statistic**.

There were two separate bugs there, and I think the second is the more
interesting one.

**Bug one: read-then-write is not an upsert**

The fix is a real atomic upsert. SQLite and PostgreSQL share the syntax, so
this stays portable:

```python
statement = dialect_insert(DailyCounter).values(
    day=bucket, name=name, value=amount
)
await session.execute(
    statement.on_conflict_do_update(
        index_elements=["day", "name"],
        set_={"value": DailyCounter.__table__.c.value + amount},
    )
)
```

Note `DailyCounter.__table__.c.value + amount` rather than a Python integer.
The increment happens in the database, against whatever the row currently
holds. No value is ever read into Python and written back.

**Bug two: telemetry was allowed to fail the request**

Fixing the race removes this specific 500. It does not remove the class of
problem: a counter write was inside the request's success path, so *any*
future failure of it — a lock timeout, a disk error, a schema change — would
again turn a good answer into an error response.

Counters are not allowed to have that power:

```python
try:
    await bump_counter(session, counter_name)
    await session.commit()
except Exception:
    await session.rollback()
    # A counter must never cost an agent its answer.
```

Swallowing exceptions is usually a smell. Here it is the requirement. The
worst outcome is an undercounted statistic. The alternative was a 500 for the
caller.

**Testing it**

A race that needs concurrency to appear needs concurrency in the test, or it
comes back. Two tests now guard this:

- 48 concurrent queries across 12 threads: every response must be 200, and
  the counter must equal 48. Off-by-one from a lost update fails it just as
  loudly as a 500.
- A deliberately broken counter — monkeypatched to raise — must still produce
  a 200 with a correct body.

The second one is the one I'd keep if I could only have one. It tests the
policy, not the bug.

**What I'd take from this**

The endpoint had been reviewed, tested and running for days. What it hadn't
been was *concurrent*. Every read-then-write in a request handler is a race
waiting for enough traffic, and the ones in your instrumentation are the
easiest to overlook, because instrumentation feels like it sits outside the
system it measures. It doesn't. It runs in the same transaction, on the same
connection, holding the same locks.

Ask of every non-essential write in a request path: if this fails, does the
user still get their answer? If the answer is no, that write has more
authority than it earned.

FailEcho is open source and the network is currently empty — it launched this
week. The code is at https://github.com/FailEcho/failecho and the MCP endpoint
is https://failecho.com/mcp if you want to point an agent at it.

---

## Before publishing

- [ ] Re-run `.venv/bin/python scripts/loadtest.py --profile query` and confirm the
      story matches what the code does today.
- [ ] Confirm both tests still exist and pass:
      `.venv/bin/python -m pytest tests/test_experiment_metrics.py -k "atomic or broken_counter"`
- [ ] Read the draft once for anything that overclaims. There is no "200
      agents" in it and there should not be.

## After publishing

Post the dev.to link as a comment in the MCP Discord and on r/mcp — a bug
story is welcome in places where a product link would be spam. That is the
actual reason to write it.

---

# Part 2 — X / Twitter

## Do I need a new account?

**Not a new brand account.** A zero-follower `@failecho` account posting into
the void is the worst reach on the platform, and X heavily suppresses new
accounts with no history.

- **If you already have an X account**, use it. A person with a project gets
  more reach than a logo.
- **If you don't**, this channel is not worth starting from scratch right now.
  It takes months to build enough presence for it to pay. Skip it and spend
  that time on the Show HN and the Discord posts.

## If you do have an account

The strategy is not posting. It's replying.

1. Follow the accounts that announce MCP things: `@modelcontextprotocol`,
   Anthropic's dev accounts, Cursor, Cline, Continue, and the people who build
   popular MCP servers.
2. Turn on notifications for a handful of them.
3. When one posts about agent reliability, retries, tool errors, or MCP
   servers, reply **with something useful on its own** — an observation about
   failure patterns, a number, a real limitation you hit. Mention FailEcho only
   when it is genuinely the answer to what was asked.
4. Ratio: roughly nine replies that mention nothing to one that does.

A reply that only says "check out my thing" gets ignored at best and blocked
at worst. A reply that teaches gets clicked on, and the profile link does the
rest — so make sure your bio has `failecho.com` in it.

## Honest expectation

This is the lowest-yield channel on the list. Occasional hit, no compounding
unless you do it for months. It is on the list because it costs nothing when
you're already reading those accounts — not because it will bring users this
week.
