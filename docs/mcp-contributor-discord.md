# The MCP Contributor Discord

Rewritten 2026-09-14, after reading the server rules. The previous version of
this file was a showcase post. **Posting it would have been a bannable
offence**, twice over. It is deleted rather than kept in git history as a
temptation.

## Two rules that decide everything

> **1. No AI-generated messages.** This server is for human-to-human
> communication. Generating message copy with AI is a bannable offense, even
> if you "review and approve" it before posting.

So there is no draft in this file and there will not be one. Not a paragraph,
not a sentence, not "a starting point you can edit". That rule is unusually
explicit about the edit-it-afterwards loophole, and a banned account cannot be
un-banned by explaining that the wording was mostly yours.

Assistive tools are allowed — spell-check, translation, speech-to-text. The
line is between fixing how you wrote something and having something written
for you.

What this document can do is tell you *what is worth talking about* and *where
the line is*. The words have to be yours.

> **2. No self-promotion.** Beyond introducing yourself in #introductions,
> please avoid bringing up your projects, products, blog posts, social media
> posts, or any sort of initiatives that could be construed as brand marketing
> unless they have technical relevance to an already-running discussion about
> the protocol.

This is not a showcase server. There is no #showcase channel to find. The
whole "here is a thing I built, please try it" plan does not apply here.

## Where FailEcho may legitimately appear

**#introductions, once.** That is the one place the rules explicitly allow it.
One message, who you are and what you are working on. Do not link the site
three times.

**In a running protocol discussion, if it is genuinely relevant.** The test in
the rule is "technical relevance to an already-running discussion about the
protocol" — so you are joining a conversation, not starting one about
yourself.

## The protocol problems you actually have opinions about

This is the useful part, and it is why that server is worth your time even
though you cannot advertise there. You have hit real specification questions
while building. Those are contributions.

**Server identity.** Cross-agent evidence only works if two agents calling the
same server agree on what to call it. You match on the server's own
`serverInfo.name`, and in practice that value is neither unique nor stable —
local aliases differ, forks reuse names, and nothing in the spec makes it an
identifier. "How should a server be identified across clients?" is a real
protocol question and you have hit it in a way most people have not.

**Error shape.** MCP has no standard error taxonomy beyond JSON-RPC codes, so
"the same failure" has to be reconstructed from free text that every server
formats differently. Whether the protocol should define an error class
vocabulary — or deliberately should not — is a genuine design discussion.

**Tool schema versioning.** You built around the case where a tool's schema
changes under an agent and the failure looks like a validation error. Whether
`tools/list_changed` is sufficient signal for that is a spec question.

**Stateless Streamable HTTP.** You run stateless with JSON responses and no SSE
stream, and you found that `GET` still holds a connection open. That is a
concrete observation about the transport that other implementers would find
useful.

Any of those is a legitimate reason to be in that server. None of them
requires mentioning your product at all — and if your product comes up because
someone asks how you know, that is the rule being satisfied rather than
dodged.

## The mechanics

- **Display name:** `Name (Company)` or `username (Company)`. The rules ask for
  it. `Fuyuki0 (FailEcho)` is honest and does the introduction quietly, in the
  one way the server has asked for.
- **Use threads**, not messages in the main channels. Rule 6.
- **Do not ask for help getting started with MCP.** Rule 4 — the server assumes
  a working knowledge of the spec.
- **Do not answer product support questions there**, including about your own.
  Rule 3.

## So where does the showcase post go instead?

Not here. Servers that welcome "I built an MCP server" exist and are a
different audience — smaller, less rigorous, and much more likely to actually
install something. Worth finding one or two, reading their rules the same way,
and posting there.

The Contributor Discord is a long game: be useful on the protocol for a while
and the product becomes a thing people find out about because they looked you
up, which is the only form of promotion the rules leave open and probably the
most effective one available in a room full of implementers.
