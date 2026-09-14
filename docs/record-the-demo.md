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

## Tools

- **asciinema** — `asciinema rec demo.cast`, then `agg demo.cast demo.gif`.
  Best quality-to-size for a terminal, and the text stays selectable if you
  embed the player.
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
