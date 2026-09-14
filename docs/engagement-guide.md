# Why those posts got engagement, and what to take from them

Written 2026-09-14, from two posts that worked: Kiln (text-to-3D MCP) and
Cogram Studio (CAD MCP).

## What the Kiln post actually does

Strip the subject out and the moves are visible. Almost none of them are about
3D.

**It opens by giving something up.**

> "Originally, this was deployed as a hosted service. But with frontier models
> leapfrogging each other so quickly... I realized the more useful thing was to
> open-source the engine."

That is a retreat, described plainly. The reader's guard drops, because
somebody trying to sell them something does not open by saying their first
plan was wrong.

**It names the problem before the product**, and names the competition's
weakness fairly rather than dismissively: diffusion meshes are hard to edit,
loose Blender scripting gets chaotic. A reader who has hit either of those is
now nodding before the product is mentioned.

**It is specific to the point of being checkable.** 105 primitives. 12
categories. 28 core shapes. Every tool named: `kiln_validate`, `kiln_render`,
`kiln_inspect`. Specificity is the cheapest credibility there is, because
invented numbers are round and real ones are not.

**It has a caveat paragraph, and that paragraph is the most persuasive part of
the post.**

> "If you need AAA production assets, highly organic characters, or
> photorealistic final art, you still need a real 3D artist."

Nobody is made to trust by a claim. They are made to trust by a *limit*,
because a limit is the one thing marketing will not tell you. The same move
again: "I haven't battle-tested Mac nearly as much."

**The ask is free.** "Would love to see what people build with it." No
sign-up, no waitlist, no "let me know if you want access".

Cogram does the same smaller: two models you can open and inspect, and an
honest warning that asking for a finished complex design "may produce
something that looks plausible but falls apart under closer examination".

## The asymmetry, said honestly

Both of those products **make a thing you can look at**. A model appears.
There is a before and an after in one frame.

FailEcho's product is **an absence**. A retry that does not happen. A failure
that does not repeat. Nothing appears; something fails to occur. That is a
genuinely harder thing to sell, and no amount of writing technique changes it.

Prevention always sells worse than creation. The only way to sell prevention
is to **make the cost visible first**, so the absence has something to be
measured against. Kiln shows you a ship. You have to show somebody the forty
minutes.

## But you do have a showcase, and it is already built

This is the thing worth acting on.

`/demo` holds a real transcript of a real run, and it now replays line by line
with a Play button. **That is a screen recording waiting to happen.** Thirty to
sixty seconds, no narration needed:

```
> create_issue
  x 422 validation_error
> create_issue        (retry)
  x 422 validation_error
> create_issue        (retry, arguments tweaked)
  x 422 validation_error
```

...then the same failure, with the network answering:

```
  Recovery actions others reported:
    refresh_schema        5/5 (100.0%)
    retry                 0/5 (0.0%)
```

**That two-line block is the entire product**, and it is visual in the way a
terminal is visual. `0/5` next to `5/5` is an argument that needs no sentence
around it. Put those two panes side by side — the blind retry loop, and the
same moment with evidence — and you have Kiln's before-and-after, in the only
medium your product has.

A GIF or an asciinema recording of that, embedded in the Reddit post and in
the README, would do more than another paragraph. It is the single highest
-value thing left on the list.

## The psychology, in the order it applies

**1. Specificity is credibility.** Not "it learns from other agents" but "an
action needs five observed attempts before it is recommended, and three
separate reporters before it carries full weight". Real thresholds. Checkable.
You already have these numbers; use them as numbers.

**2. A named limit outweighs three claims.** Your equivalent of Kiln's AAA
paragraph is already true and already written:

> The shared layer is new. It works on your own agent's history from the fifth
> recovery — that part needs nobody. The cross-agent part needs other people.

Say it *before* someone asks. Said first, it is integrity. Said after being
challenged, it is damage control, and the same words score completely
differently.

**3. Effort is reciprocal.** The Kiln post clearly took an hour to write. That
is why people gave it ten minutes. A four-line post asking for attention gets
the attention it paid for.

**4. Make the ask cheap and reversible.** Your best line is not "please try
it", it is the fact that leaving it on costs nothing and `FAILECHO_DISABLED=1`
turns it off. People commit to things they can walk away from.

**5. Let them correct you.** The reason your dev.to post got seven comments is
that it ended on a question you genuinely could not answer. A reader who
corrects you is invested; a reader who is impressed is not. Kiln's "Mac bug
reports welcome" is the same move.

**6. Sell the identity, not the feature.** Nobody adopts a failure network.
They adopt being the kind of engineer whose agents do not burn budget on
retries that cannot work. Kiln sells being someone who ships assets in an
afternoon.

## What not to do, specifically

- **No "be the first" framing.** Nobody wants an empty room. You already have
  the answer to this: the memory works alone, and the network is upside.
- **No invented adoption.** The counter is public. Someone will check inside
  thirty seconds, and being caught once costs more than every post gains.
- **Do not post the same text to three subs.** Kiln posted once, properly.
- **Do not argue with the first critical comment.** It is usually the best
  comment in the thread, and the thread is watching how you take it.
- **Do not lead with the protocol.** Lead with the 422 that wasted your
  afternoon. Everyone in that room has had the afternoon.

## The order to do this in

1. **Record the demo.** 30–60 seconds, the retry loop and then the same moment
   with evidence. This is the missing asset and everything else improves with
   it.
2. **Put it in the README**, above the fold, and on the Reddit post.
3. **Then post**, question-first, per `docs/reddit-post.md`.

One honest caveat on all of the above: Kiln had a year of work and a finished
gallery behind it. Some of that engagement was earned before the post existed.
A good post does not manufacture that — it just stops it being wasted.
