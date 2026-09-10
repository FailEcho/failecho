# Contributing to FailEcho

The most valuable contribution right now is not code. It is **one real agent
reporting one real failure**. The network is only interesting when the
evidence in it came from systems that never met each other.

## Reporting telemetry

Point an agent at `https://failecho.com/mcp` or the REST API and let it report.
No account, no key. See the README's "Connect an agent" section.

## Code

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest          # the whole suite must stay green
.venv/bin/python -m uvicorn app.main:app --reload
```

Some conventions that are load-bearing rather than stylistic:

- **Privacy tests are not negotiable.** `tests/test_privacy.py` reads rows
  straight out of SQLite. If a change makes it possible to store a prompt, an
  argument or a raw identifier, the change is wrong, not the test.
- **Determinism over cleverness.** Normalization, fingerprinting and confidence
  are plain arithmetic that anyone can recompute. No model calls, no
  embeddings, no learned parameters in the intelligence path.
- **The wire format is a contract.** Endpoint paths, MCP tool names and
  response field names do not change for branding, taste or tidiness.
- **Never fabricate confidence.** Thin evidence returns `INSUFFICIENT_DATA` and
  `null`. An honest "I don't know" is a feature.
- **Keep it small.** One process, one SQLite file, no broker, no cache, no
  queue. It has to run on a small VPS in well under 150 MB.

## Framework integrations

Build against `failecho.adapters.ToolTelemetrySink` -- four events, one
direction -- and put the adapter in `client/failecho/integrations/`. Import the
framework lazily so the core client keeps its zero-dependency promise. See the
Pydantic AI integration for the shape.

## Reporting a bug

Open an issue with the smallest reproduction you can manage. For anything
security-related, see [SECURITY.md](SECURITY.md) instead.
