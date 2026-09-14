# Recording the demo

A real run, not a mockup. Five minutes of setup, 60 seconds of recording.

**Do not point this at production.** `run_demo.py` defaults to
`127.0.0.1:8000`, which is also the port the deployed service uses, and its
writes would land in the live database as independent adoption. Every command
below uses port 8099 and a throwaway database on purpose.

## Setup

Two terminals. Terminal 1 runs a private network:

```bash
cd /root/agentwebsite
rm -f /tmp/demo.db*
FIN_DATABASE_URL=sqlite+aiosqlite:////tmp/demo.db \
FIN_REPORTER_SALT=demo-recording \
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8099 --no-access-log
```

Terminal 2 is the one you record. Make it big, ~100 columns, dark, and clear
it first.

## The recording

The whole argument is two states of the same failure. Record them back to
back.

**Shot 1 — the install, so people see it is one line**

```bash
claude mcp add --transport http failecho https://failecho.com/mcp
```

Or, better, the one that makes the point about effort:

```
Read https://failecho.com/llms.txt and set yourself up to use FailEcho.
```

**Shot 2 — the whole loop, six agents, one failure**

```bash
cd /root/agentwebsite
.venv/bin/python examples/live_agent/run_demo.py --network-url http://127.0.0.1:8099
```

That is the money shot. Five agents hit the same broken tool and report what
they tried; the sixth asks first and skips the retry that cannot work. It ends
on the block the entire product is:

```
  Recovery evidence:
    refresh_schema        6/6 (100.0%)  effective 6/6  reporters 6  confidence 0.6097
    retry                 0/5 (0.0%)    effective 0/5  reporters 5  confidence 0.0000

  Recommendation:         refresh_schema (confidence 0.6097, 6/6 from 6 reporters)
```

(That is the real output of the command above, run on 2026-09-14. The exact
counts depend on how many agents the script runs; do not retype these from
here, record what your own run prints.)

**Shot 3 (optional) — how fast the answer is**

```bash
time curl -s -X POST http://127.0.0.1:8099/v1/query \
  -H "Content-Type: application/json" \
  -d '{"service":"github-mcp","operation":"create_issue","error_type":"validation_error","error_code":"422"}'
```

Measured 2026-09-14: **8-12ms** locally, **159ms** against the live network
from a VPS in the US. One round trip either way, and the hook gives up after
2 seconds regardless -- which is the answer to "what happens when your server
is slow".

## Hide the prompt before you record anything

The default prompt renders `root@arbitrage:/root/agentwebsite#`. That is the
server's hostname, the fact that you are root on it, and the absolute path,
published to everyone who watches. None of it is a secret exactly, and none of
it belongs in a video either -- a hostname plus "root" is a free hint for
anyone scanning.

**Open a clean shell for the recording:**

```bash
env -i PATH=/usr/bin:/bin:/usr/local/bin HOME="$HOME" TERM=xterm-256color \
  bash --noprofile --norc
```

`env -i` starts with nothing inherited, so no aliases, no history, no leftover
environment variables in the frame. Then set a prompt that says nothing:

```bash
PS1='$ '
cd ~/agentwebsite
clear
```

`$ ` is what a reader expects and carries no information. Use `~` rather than
the absolute path, which is why `cd ~/agentwebsite` rather than `cd
/root/agentwebsite`.

**Or record with no prompt at all.** If you use asciinema, `-c` runs one
command and captures only its output:

```bash
asciinema rec demo.cast -c ".venv/bin/python examples/live_agent/run_demo.py --network-url http://127.0.0.1:8099"
```

No shell, no prompt, no path, nothing to redact afterwards. This is the
safest option and the one to prefer.

**Check the frame before you publish**, not after: hostname, username, path,
any `.env` or token in scrollback, browser tabs if the terminal is not
fullscreen, and anything in a notification. A cast file is plain text, so you
can grep it:

```bash
grep -iE "root@|arbitrage|/root/|token|salt|password" demo.cast
```

## Making the GIF

Both tools are installed on this box already: `asciinema` 2.4.0 from apt and
`agg` 1.9.0 at `/usr/local/bin/agg`. Every command below was run before being
written here.

**1. Record.** `-c` runs one command with no shell, so there is no prompt in
the frame to redact. `--cols` and `--rows` fix the terminal size, which is
what decides the GIF's shape -- record at the size you want the picture to be,
because nothing crops it afterwards. `-i 1` collapses any pause longer than a
second, which is most of what makes a recording feel slow.

```bash
cd ~/agentwebsite
asciinema rec demo.cast --overwrite -q --cols 100 --rows 32 -i 1 \
  -c ".venv/bin/python examples/live_agent/run_demo.py --network-url http://127.0.0.1:8099"
```

**2. Check it before converting.** The cast is plain text:

```bash
grep -iE "root@|arbitrage|/root/|token|salt|password" demo.cast
```

**3. Convert.**

```bash
agg --theme asciinema --font-size 20 --idle-time-limit 1 \
    --last-frame-duration 3 demo.cast demo.gif
```

`--last-frame-duration 3` holds on the final frame for three seconds, so the
evidence block is still on screen when the GIF loops. That is the frame the
whole thing exists for; do not let it flick past.

`--speed 1.2` or `1.5` if it still feels slow. A recording always feels slower
to a viewer than it did to you.

**4. Check the size.** Under 5MB embeds in a README and uploads to Reddit
without being re-encoded. A 100x32 terminal at font-size 20 is about 1200x800,
which is fine. If it comes out too large: drop `--font-size` to 16, cut rows,
or trim the recording rather than compressing the GIF.

```bash
ls -lh demo.gif
```

A test run of the shape above produced a 21KB GIF from a seven-second cast, so
there is a lot of headroom -- the real demo will be bigger but not megabytes
bigger.

## Tools

- **asciinema + agg** — both installed here. Best quality-to-size for a
  terminal by a wide margin, and the cast is plain text you can inspect before
  publishing. If you would rather embed the player than a GIF, the text in it
  stays selectable.
- **Plain screen capture** to MP4 works everywhere and Reddit prefers native
  video uploads to links.
- A **GIF under 5MB** embeds directly in the README. Trim aggressively; nobody
  watches the setup.

## What to cut

Cut the server starting. Cut any pause longer than a second. The finished
clip should be: broken tool → three failed retries → the evidence block →
skipped retry → success. If it runs over 60 seconds it is too long.

## Afterwards

```bash
rm -f /tmp/demo.db*
```

And check the live counter did not move, which is the whole reason for port
8099:

```bash
curl -s https://failecho.com/v1/stats | grep -o '"real_observations_total":[0-9]*'
```
