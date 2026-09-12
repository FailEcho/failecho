# dev.to post — the advice-shaped one

Voice: first person, casual, a few `:D` / `:3`. Emoticons stay sparse; four
across the whole post reads as a person, twenty reads as noise.

Tags: `#ai` `#llm` `#python` `#opensource`

Title:

```
Your agent retries the same failure forever, and the error message is why
```

Backups:

```
Four things I learned building a shared failure log for AI agents
The retry is the default and it is usually wrong
```

---

## Draft

Every agent framework I have used has the same reflex. A tool call fails, the
model reads the error, and it tries again. Sometimes with a small change,
sometimes with the exact same arguments, sometimes four times in a row, and
then it apologises to me about it :D

I spent a while trying to fix this properly and ended up learning four things
that I think generalise past whatever you are building. Full disclosure up
front: I did end up building something around this, and it is at the bottom.
The four things are the useful part and they stand on their own.

### 1. The error tells you what failed, not whether retrying helps

This is the whole problem in one line.

```
422 validation_error
```

Is that a field that got renamed permanently, or a service having a bad ten
minutes? The string is identical in both cases. In one, retrying is correct
and will work in thirty seconds. In the other, you can retry until you run out
of budget.

A model reading that error has to guess, and its prior is "retry" because that
is what most of its training data does. It is not being stupid, it just does
not have the one piece of information that separates the two cases: what
happened to everyone else who hit this.

### 2. If you only log failures, your failure rate is a lie

I built the first version to record failures. Obviously. It is a failure log.

Then I looked at a service with 100 failures and realised I had no idea
whether that was catastrophic or completely fine. 100 failures out of 200
calls is an outage. 100 out of a million is Tuesday.

You need the denominator. Log the successes too, boring as they are. It is
the difference between "this operation is broken" and "this operation is
fine and you were unlucky twice".

### 3. Normalise before you compare, or nothing will ever match

Two agents hit the same bug and produce these:

```
Repository 8823 rejected field body at 2026-09-11T14:02:11Z
Repository 41902 rejected field body at 2026-09-12T09:41:55Z
```

Same bug. Different strings. Compare them raw and you have two unrelated
incidents forever.

So before comparing anything, replace the parts that vary and keep the parts
that mean something:

```python
text = URL_RE.sub("<URL>", text)
text = UUID_RE.sub("<UUID>", text)
text = TIMESTAMP_RE.sub("<TS>", text)
text = LONG_NUMBER_RE.sub("<N>", text)
```

Both lines collapse to the same shape, and now they are one thing you can
count. This is also a nice place to strip anything credential-shaped —
`Bearer` tokens, JWTs, `AKIA...` keys — because you are already walking the
string with regexes and you really do not want that stuff in a log :3

Hash the normalised form together with the service and operation and you have
a stable id for "this exact failure", which is the thing everything else hangs
off.

### 4. Do not let a model score the confidence. Count it.

The tempting move is to ask an LLM "how likely is it that refreshing the
schema fixes this". Do not. You will get a number that sounds calibrated and
is not, and you will not be able to explain it to anyone including yourself.

Count instead. If an action was tried 5 times and worked 5 times, that is
5/5 — but so is 117/124, and those are not equally trustworthy. A Wilson
score lower bound folds sample size into the number for you:

```python
def wilson_lower_bound(successes, attempts, z=1.96):
    if attempts <= 0:
        return 0.0
    p = successes / attempts
    z2 = z * z
    centre = p + z2 / (2 * attempts)
    margin = z * math.sqrt((p * (1 - p) + z2 / (4 * attempts)) / attempts)
    return max(0.0, (centre - margin) / (1 + z2 / attempts))
```

5/5 scores about 0.57. 117/124 scores about 0.89. Same ratio, honest ordering,
ten floating point operations, no dependencies, and you can recompute it by
hand when someone asks where the number came from.

And when there is not enough data, return that. Not a guess with a low
number attached — an actual "I don't know". `INSUFFICIENT_DATA` is a real
answer and agents handle it fine.

### The one that still bites me

Here is the unsolved one, in case you want to avoid my mistake.

I let `service` be a free text field. So one agent reports `github`, another
`github-mcp`, another `api.github.com`. Three names, three ids, zero overlap,
and the whole thing quietly does nothing.

I casefold and trim, which is not nearly enough. Aliasing is the real fix and
I have not built it yet. If you are designing anything that compares
observations across users, pin the naming convention *before* you have users,
because afterwards you are migrating data instead of writing a paragraph.

### The thing I built

It is called FailEcho. Agents report failures and recovery outcomes as
metadata, and other agents can ask what already worked before they retry. MCP
endpoint, REST API, and a Claude Code plugin that does the reporting through a
hook so nobody has to remember to.

Being straight with you about the state of it: no independent agent has
reported anything yet. The counter is at zero and the site says so on the
front page, because a shared network with one participant is just a log :3

So this is not a "check out my product" post. It is four things I learned that
I think are true regardless, plus an open invitation: if you run agents
against MCP servers and would leave a reporter on for a week, I would like
about five to ten of you. I will publish whatever the network sees afterwards,
including if the answer is "the overlap between different people's failures is
basically nil", which is genuinely the thing I most want to find out.

https://failecho.com — MIT, no account, no key.

---

## Before publishing

- Re-read section 4 against `app/core/intelligence.py`; if the constants moved,
  the snippet is wrong and someone will run it.
- The two example numbers (0.57 and 0.89) are computed from the function
  above with z=1.96. Recheck if z changes.
- Do not add a number of users. There are none.
- Post in the morning UTC on a weekday. Reply to every comment for the first
  few hours, same rule as HN.
