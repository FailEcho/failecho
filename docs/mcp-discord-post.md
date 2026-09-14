# Posting FailEcho to the MCP Discord

## Getting in

The invite lives on the official site rather than anywhere stable enough to
write down here: **modelcontextprotocol.io** → the Community / Discord link in
the footer or top nav. The GitHub org `modelcontextprotocol` links it too.
Invite URLs get rotated, so take it from the site on the day rather than from
an old link in a blog post.

## Before posting anything

1. **Read `#rules` and any pinned message in the channel you intend to use.**
   Several MCP-adjacent servers ban self-promotion outside one designated
   channel, and a first message that breaks that rule is how you get muted by
   a bot before a human ever sees the project.
2. **Find the right channel.** Look for `#showcase`, `#share-your-project`,
   `#servers`, `#community-servers` or similar. If there is any doubt, ask in
   `#general`: "is there a channel for sharing an MCP server I built?" That
   question is welcome everywhere; the pitch in `#general` is not.
3. **Spend twenty minutes reading first.** Answer someone else's question if
   you can. A first-ever message that is a link to your own thing reads
   differently from a fifth message that happens to be one.

## What to post

Short. Discord is not Hacker News — nobody reads six paragraphs in a chat
channel. One message, no images, no bold headers, no emoji spam.

> Built an MCP server for something that kept annoying me: every agent
> rediscovers the same tool failures from scratch. If `create_issue` on some
> server starts 422ing because a field was renamed, every agent hitting it
> works that out alone, and none of them tell each other.
>
> FailEcho is a shared endpoint for that. An agent reports a failure as
> metadata — service, operation, error class, whether a retry or some other
> action fixed it — and any other agent can ask "is anyone else hitting this
> right now, and what worked?" before it retries.
>
> Streamable HTTP at https://failecho.com/mcp — four tools, no auth, no key.
> There's a Claude Code plugin that reports failures through a hook, and
> `uvx failecho-mcp` / `npx -y failecho-mcp` if your host only starts
> processes.
>
> Being upfront: it's new and the network is basically empty. It's the kind of
> thing that's worth nothing with one reporter and worth a lot with fifty, so
> right now it's mostly a bet. Metadata only — no prompts, no tool arguments,
> no results, no keys. The contract is at https://failecho.com/about
>
> Happy to be told the naming model is wrong. It matches on
> service + operation + error class, and getting agents to agree on what to
> call a service is the part I'm least sure about.

### Why it is written that way

- **The problem first, in one concrete example.** Nobody in that server needs
  "failure intelligence network" explained; they need to recognise the
  annoyance.
- **Says the network is empty.** They will check, and find zero. Saying it
  first is the difference between honest and caught.
- **Ends on a real open question.** It invites a reply about naming rather
  than a silent scroll past, and the naming problem is genuinely unsolved.
- **No adoption claims, no "first", no "revolutionary".** There is nothing to
  claim yet.

## After posting

- **Answer every reply properly, including the dismissive ones.** "How is this
  different from a status page?" is a fair question with a real answer
  (a status page tells you a service is down; this tells you which specific
  call is failing for other people and what fixed it).
- **Do not bump it.** One post. If it sinks, it sinks.
- **Do not cross-post the same text** into three channels or three servers.
  Same-day duplicates are how projects get a reputation before they get users.

## Other servers worth the same treatment

Same rules, same post, spaced out over days rather than an afternoon:

- The Cursor community Discord — the audience runs agents against tools daily.
- LangChain / LlamaIndex Discords, if the framework integration lands.
- r/mcp and r/AI_Agents on Reddit, which is a different format again: there,
  the Hacker News draft in `docs/show-hn-runbook.md` is closer to right.

## The thing that actually matters

A Discord post is worth a handful of curious clicks. Ten *independent*
reporters is what makes the fingerprints mean anything, and those come from
asking specific people who already run agents against GitHub, Stripe, Slack or
search APIs to add one line — not from broadcast. Treat the Discord post as
the cheap half and the direct asks as the real work.
