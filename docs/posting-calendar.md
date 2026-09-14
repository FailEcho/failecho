# Posting calendar

Written 2026-09-14. Supersedes the target date in `docs/show-hn-runbook.md`.

## The decision this calendar makes

The runbook aimed at Show HN on Tuesday 2026-09-15. This moves it, for one
reason: `real_observations_total` is 0.

Show HN is a one-shot channel. Reposting the same link is treated as spam, and
the honest version of the post today says "this is worth nothing with one
reporter and a lot with fifty, and right now it has one." That is a fine thing
to say in a Discord channel of forty people who build MCP servers. On the
front page of Hacker News it spends the single largest audience this project
will get for months on a demo of an empty database.

The runbook already says the stronger post exists later:

> wait until there is real data and post "Show HN: what 10,000 agent failures
> look like", which is a much stronger post than this one because it has a
> finding in it.

So: seed first, launch second. Everything below is ordered to get independent
reporters *before* spending the one-shot channels.

The counter-argument, which is real: HN traffic could itself be the seeding
mechanism, and a project can die waiting for a perfect launch moment. If two
weeks of seeding produces nothing, take the HN slot anyway in October with the
post as written — an empty network honestly described is still a real project,
and the thread itself may find the first ten reporters.

---

## Week 1 — seed quietly (15–21 Sep)

Nothing here is "advertising the product". It builds the two things a launch
needs and cannot manufacture on the day: an author with a track record, and a
network with something in it.

**Tue 15 Sep — publish the warm-up post**
`docs/devto-post-warmup.md`, "Your AI Agent Has No Colleagues". FailEcho is
mentioned nowhere in it, deliberately. It exists so that when the product post
lands, its author is someone who wrote a good piece about stigmergy rather
than an account whose entire history is one link to their own site.
Best window: **Tue–Thu, 13:00–15:00 UTC** (morning US, afternoon Europe).
Cross-post the same article to Hashnode and Medium — dev.to permits it and
the canonical tag keeps search clean.

**Wed 16 Sep — nothing. The listings are done.**
Every directory that can be placed without waiting on someone else already is:
official registry, Glama, Smithery, mcpservers.org. What is outstanding is a
merge queue (`awesome-mcp-servers` PR #14162) and a review queue (the Claude
community marketplace, submitted 2026-09-14) — neither is worth chasing, and
both propagate on their own. See `docs/mcp-directories.md` for the table.

**Thu 17 Sep — the MCP Discord**
`docs/mcp-discord-post.md`. Small audience, exactly the right one, and the
only channel here where "the network is empty, help me fill it" is a
*reasonable ask* rather than a weak pitch.

**Fri 18 – Sun 21 Sep — direct asks, which is the actual work**
Ten individual messages beat any broadcast. Find people already running agents
against GitHub, Stripe, Slack, Notion or search APIs — in the Discord threads
you just read, in MCP server repos' issue trackers, in r/AI_Agents comment
histories — and ask each one personally to add the hook for a week. Their
failures are the ones most likely to overlap with somebody else's, which is
the entire product.

Target: **10 independent reporters.** Not traffic. Reporters.

---

## Week 2 — the product post, if there is something to show (22–28 Sep)

**Gate:** `real_observations_total > 0` and ideally ≥ 10 unique reporters.
If the gate is not met, spend another week on direct asks and slide everything
right. The gate is the point; a date is not.

**Tue 23 Sep — the main dev.to post**
`docs/devto-post.md`, problem → product. By now the warm-up post has been up a
week and the author is not a stranger.

**Wed 24 Sep — Reddit, one subreddit at a time**
Not the same text in three places on the same day; that is the fastest way to
get flagged.
- **r/mcp** — smallest, most relevant, most forgiving. Go here first.
- **r/AI_Agents** — technical framing, no marketing voice.
- **r/LocalLLaMA** — only if the framing is about the *data*, not the service.
  This crowd is hostile to hosted anything; the honest angle is "here is what
  agent failures actually look like in aggregate".
Best window: **13:00–15:00 UTC weekdays**. Never Friday evening or a weekend.

---

## Week 3+ — Show HN, when it has a finding in it (from 29 Sep)

**Tue, Wed or Thu, 12:00–14:00 UTC** (08:00–10:00 ET). Never Monday, never
Friday, never a weekend.

**The one rule that decides it:** be at the keyboard for the two hours after
submitting. A Show HN with no author in the thread falls off `/new` without
ever reaching the front page. Block the morning out; do not submit and then go
to a meeting.

Two titles, depending on what the data gives you:
- With a finding: *"Show HN: What 10,000 agent tool failures look like"* —
  the data is the story and the product is the footnote. Much stronger.
- Without: the post as written in `docs/show-hn-runbook.md`.

**Before you submit, the three blanks in `show-hn-runbook.md` §5 must be
filled in writing.** Who is behind it, whether it is ever monetised, whether
the dataset gets published. Improvising these in the thread reads as evasion,
and they will be asked. "No plans to monetise" is a perfectly well-received
answer on HN; inventing a pricing tier live is not.

---

## Timing, per platform

| Channel | Best window (UTC) | Notes |
|---|---|---|
| Hacker News | Tue–Thu 12:00–14:00 | One shot. Be present for 2h after. |
| dev.to | Tue–Thu 13:00–15:00 | Cross-post to Hashnode/Medium, canonical tag. |
| Reddit | Tue–Thu 13:00–15:00 | One subreddit per day, never the same text. |
| Discord | Weekday working hours, any TZ | Read the rules, use the right channel. |
| Directories / awesome lists | No timing | Durable. Do these first, they never expire. |

## Places deliberately not on this list

- **Product Hunt** — built for visual consumer products with a launch-day
  crowd. An MCP endpoint with no UI does badly there, and it burns a
  once-per-product slot.
- **Twitter/X and LinkedIn** — only worth it with an existing audience. From a
  standing start the cost per reader is higher than everything above.
- **r/programming, r/webdev** — wrong audience, and they punish anything that
  reads as promotion.

## What actually decides this

Every channel above is worth a handful of clicks. Ten independent reporters is
what makes a fingerprint mean anything, and that number moves through
individual asks, not broadcast. If a week of posting produces traffic and no
reporters, the answer is not another post — it is ten more direct messages.
