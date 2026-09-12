# dev.to warm-up post — the discussion one

**Post this first, two or three days before `devto-post.md`.**

Discussion posts outperform tutorials on dev.to by a wide margin, and a new
account needs comments more than it needs claps. This one names FailEcho
nowhere. The connection is thematic: it is about retrying things that cannot
work, which is the same thing the product is about, so the main post lands on
an audience already thinking about it.

Set `discussion` in the tags. End on a question and answer every reply.

The previous advice-shaped draft (four engineering lessons) is in git history
if you would rather lead technical.

---

## dev.to front matter

```
---
title: The bug was fixed at 11pm. I kept debugging until 2am.
published: false
description: On the developer version of the retry loop, and why "one more try" is the hardest thing to stop.
tags: discuss, mentalhealth, career, productivity
---
```

Alternative titles:

```
Why can't we stop debugging when we know we should stop?
The sunk cost fallacy has a keyboard shortcut and it's Ctrl+R
```

---

## Draft

I fixed the bug at 11pm.

I know that now because I checked the git log the next morning. The commit
that made the tests pass is timestamped 23:04. I kept going until 2am.

What was I doing for three hours? Re-running the suite. Reading the same
forty lines. Changing a thing, changing it back. Refreshing a page that had
already worked six times.

I don't think I'm unusual here :D

## The loop

Here's the shape of it, and I bet you recognise it:

1. Something breaks
2. You try the obvious fix
3. It doesn't work
4. **You try it again**

Step four is the interesting one. Not a different fix — the *same* one, maybe
with a small change you couldn't defend if asked. And when that fails, again.

There's a decent amount of psychology behind why. Sunk cost, mostly: three
hours in, stopping means those three hours were wasted, and continuing means
they might not have been. Which is nonsense, because the three hours are gone
either way, but it does not feel like nonsense at 1am.

Some of it is that debugging is a variable-ratio reward schedule, which is the
same mechanism that makes slot machines work. Sometimes the fourth identical
retry *does* work — a cache expired, a deploy finished — so the behaviour gets
reinforced at random intervals. That is the schedule psychologists use when
they want a behaviour to be maximally hard to extinguish.

And some of it is just that we're bad at telling two situations apart:

> **This will work if I keep going.**
>
> **This can never work and I need a different approach.**

They feel identical from the inside. Same frustration, same tunnel, same
certainty that the answer is close.

## The thing that actually helps

Not discipline. I've tried discipline, it works for about a week :3

What actually helps is **someone else having already been there**. One
sentence from a colleague — "oh, that's the schema cache, restart it" — ends
three hours instantly. It isn't that they're smarter. They just already paid
for that information, and you didn't have to.

Which is why I think Stack Overflow, for all the jokes, was one of the most
important pieces of infrastructure we ever built. Not the answers. The
*evidence that someone else hit the same wall.*

## What I've actually changed

Two things, and they're both embarrassingly small:

**A timer.** Twenty-five minutes on one bug. When it goes, I write down what
I've ruled out. Usually the act of writing it ends it, because "I've ruled
out nothing, I've just been rerunning the tests" is very hard to write down
and then keep doing.

**Saying it out loud to someone.** Not asking for help — just describing it.
The rubber duck thing is real, and I don't fully understand why.

Neither is a fix. I still did the 2am thing last month. But the gap between
"I'm stuck" and "I've noticed I'm stuck" is shorter than it was, and that gap
is where all the hours go.

## Your turn

I'm genuinely curious about two things:

- **What's your longest one?** The record for time spent on something that
  turned out to be already fixed, or fixable in one line.
- **What actually gets you out?** Not what you know you should do. What
  works, on a real bad night.

I'll read all of them. I suspect the honest answers are more useful than any
productivity advice either of us has read :D

---

## Before publishing

- The 11pm/2am story is written as yours. It needs to be true — check a real
  git log and use the real times, or write your own version. A fabricated
  anecdote in a mental-health post is the worst possible thing to be caught
  doing.
- Do not mention FailEcho anywhere in this post. Not in the body, not in a
  comment. The bio link is enough and is the whole point of posting it first.
- Reply to every comment for the first day. This post exists for the comments;
  a discussion post with no author in the thread is worse than no post.
- Tags: `discuss` is the one that matters. It puts it in front of people who
  came to argue, which is what you want here.
