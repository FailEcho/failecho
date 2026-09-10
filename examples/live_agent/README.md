# Live agent demo

Proves the one thing the network exists to do:

```
agent hits a tool failure -> reports it -> asks the network
        -> gets a recovery action other agents actually verified
        -> recovers -> reports the outcome -> the next agent gets a better answer
```

Every network call goes over **MCP** (Streamable HTTP, official SDK), from this
external process. Nothing here imports the server.

## Run

```bash
# terminal 1 -- the network
.venv/bin/python -m uvicorn app.main:app --reload

# terminal 2 -- the demo (starts its own tool server in a thread)
.venv/bin/python examples/live_agent/run_demo.py
```

Two-process variant, if you would rather see the tool server's own logs:

```bash
.venv/bin/python examples/live_agent/tool_server.py
.venv/bin/python examples/live_agent/run_demo.py --no-tool-server
```

Flags: `--network-url` (default `http://127.0.0.1:8000`), `--tool-url`
(default `http://127.0.0.1:8765`), `--no-tool-server`.

## What is in here

| File | What it is |
|---|---|
| `tool_server.py` | A local tool that just shipped a breaking change: `body` was renamed to `content`, so a stale client gets `422 validation_error`. Deterministic, no LLM, no credentials. |
| `tool_client.py` | The agent's side: an HTTP client holding a **cached** tool schema. The cache is the bug. `refresh_schema()` is the fix. |
| `network.py` | MCP client wrapping the four tools: `check_tool_failure`, `report_tool_failure`, `report_tool_success`, `report_recovery_outcome`. |
| `agents.py` | The autonomous loop. Explorers work their own playbook (retry, then refresh); the beneficiary asks the network first. |
| `run_demo.py` | One command: five explorers, then the beneficiary, then a summary of what the network learned. |

## Why five explorers

A recommendation needs **5 effective attempts**, so five independent reporters
is exactly enough to watch the evidence cross the threshold instead of starting
past it. Agents A, C, D, E and F each get `recommendation: null` and fall back
to their own playbook. Agent B, the sixth, gets `refresh_schema`.

## Why the retry is in the playbook

Because that is what agents really do, and it is exactly the thing the network
can fix. A retry sends the same stale field and fails again — but no single
agent can tell that from one 422. Five agents' recovery outcomes can:
`retry 0/5`, `refresh_schema 5/5`.

## Demo traffic is labelled

The client sends `X-Reporter-Kind: demo`, so every row it writes is stored with
`source = demo_agent`. It is real evidence from real tool calls, and it is
never counted as real adoption. See the Demo data section of the main README.
