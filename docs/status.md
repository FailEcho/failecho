# Where everything stands — 2026-09-16, 11:40 UTC

One page. Every line says built or not, tested how, and whether reality has
confirmed it yet. "Live" means production. "Lab" means lab.failecho.com.

Legend for the tested column: **unit** = pytest against the real database;
**run** = executed end to end against a throwaway or the lab; **prod** =
observed happening on production; **docs** = taken from vendor documentation,
not run by us.

## The network itself (production)

| Feature | Built | Tested | Seen for real | Notes |
|---|---|---|---|---|
| REST: observe / outcome / query / stats / services | yes | unit, run, prod | yes | 0 independent reporters; 87 first-party observations |
| MCP endpoint, four tools, Streamable HTTP | yes | unit, run, prod | yes | listed on the MCP registry, Glama, awesome-mcp-servers |
| Wilson-score recommendation, per-reporter cap, diversity discount | yes | unit | in lab only | production has zero recovery outcomes so far |
| Privacy: normalizer, hashed reporter ids, column contract | yes | unit | yes | `mutates` added 2026-09-16, deliberately, with the reason in the test |
| Retention to hourly aggregates | yes | unit | yes | pre-existing |
| Write rate limit, body caps, security headers, Cloudflare firewall | yes | unit, run | yes | |
| `decaying` on actions and recommendation | yes | unit (rows a month apart) | **no** | needs a fix that stopped working; never happened yet |
| `success_evidence.verified` (unverified success) | yes | unit | **no** | fires at 20 write successes, zero failures; the lab's `echo_write` will reach it today |
| `mutates` declared by reporter, name heuristic as fallback | yes | unit, prod | yes | first startup migration; ran on production with 21 rows intact |
| `related_failures` (one cause, several masks) | yes | unit | **no** | needs recovery outcomes on 2+ shapes of one operation |
| `GET /mcp` hangs instead of 405 | **not done** | — | confirmed live | decision pending; closes a connection-hold vector |
| "never stores user content or secrets" wording | **not done** | — | — | stronger than the normalizer can guarantee; decision pending |
| `report_tool_recovery_outcome` alias | **not done** | — | — | agents guess the wrong name; documented instead |
| A2A agent card at `/.well-known/agent.json` | **not done, on purpose** | — | 38 probes 404ing | we do not speak A2A; a card would advertise a path that does not work |
| OTLP receiver (gateways as sources) | **planned only** | — | — | `docs/otlp-receiver-plan.md` |
| Canonicaliser: route names vs tool names | **not done** | — | lab shows the split | the fleet's first finding; fixed after the test ends, from its data |

## Ways in

| Path | Built | Tested | Seen for real | Notes |
|---|---|---|---|---|
| llms.txt one-line install | yes | 9 real agent sessions | yes | default is `.mcp.json`, project scope, no hook |
| `.mcp.json` / `claude mcp add --scope project` | yes | run | yes | |
| Cursor / VS Code config shapes on /setup | documented | **docs only** | no | never installed by us |
| Claude Code plugin + hook (automatic reporting) | yes | run (installed, uninstalled) | first-party only | zero of nine test agents chose it, correctly |
| `failecho-mcp` stdio relay (PyPI + npm) | yes | run | yes | reproducible build verified byte for byte |
| `failecho-autoreport` 0.1.1 (PyPI) | yes | 28 unit + clean-room + LangChain + LlamaIndex real tools | first-party only | never raises, never blocks, `mutates`, operator token, `recovered()` |
| `auto` zero-code mode (urllib/requests/httpx) | yes | 14 unit + run | first-party only | inert on import since the incident; test suite cannot reach production |
| CLI: `run`, `check` | yes | run from PyPI install | yes | |
| LangChain / LlamaIndex MCP client snippets | yes | run against production | yes | `langchain.mcp` beta, needs `fastmcp` |
| Scanner (`failecho_scan`) | yes | 17 unit + your laptop | your laptop: 28 sessions, no repeats | **not on PyPI, not named, no `--share`** — all deliberate until you decide |
| OTel / gateways | no | — | — | see plan |

## Our own agents

| | Status |
|---|---|
| First-party agent, production, every 30 min | running since 05:30 UTC; all `first_party`; label self-check on every run; 4 real provider failures seen (Gemini timeouts, a 503); **zero recovery outcomes yet** — nothing retryable has failed |
| Fleet, lab, 16 personas, 4 providers, every 3 min | running since ~09:00 UTC; code frozen at `9f91a5d` since 11:30; 53 runs, 28 failures, **6 fingerprints across 2+ independent reporters**, 14 recovery outcomes, **0 recommendations delivered yet** |

What the fleet has shown in three hours: the naming split (same Groq 400 on
two fingerprints via two paths); real provider failures from all four
providers; the first cross-reporter repeats. What it has not shown yet: an
asker getting a recommendation, so askers and blind still tie at 1.0
attempts per failure on real targets. The test personas will produce the
first recommendation today.

## Site

Home, setup, about, demo, network: current. Lab section on the home page
(sixth section, the last). Lab instance with strip, tag, home link, `/fleet`
scoreboard, `/` -> `/fleet`. `deploy/deploy.sh` for production; the lab is a
separate pinned checkout it never touches.

## Distribution

| Channel | Status |
|---|---|
| MCP registry, Glama, PyPI, npm, awesome-mcp-servers (95k stars) | listed |
| r/mcp | posted at 1am Eastern; ~180 views, no comments; **your laptop table not yet posted as a comment** |
| r/AI_Agents | posted; three substantive comments; three features shipped from one of them; replies sent |
| dev.to warm-ups | two posted; one real comment thread (Raknaos) |
| dev.to main post | **not published** |
| MCP Contributor Discord | waiting on server-author flair; intro is yours to write |
| Show HN | 29 Sep at the earliest; three blanks unfilled (who you are, monetisation, dataset) |

## Incidents this week, all closed

- 91 rows of test traffic reached production as adoption (import-time
  patching + TestClient). Purged from a backup; module inert on import;
  socket-level guard in the test suite; in CLAUDE.md as the second time.
- A PyPI token was pasted into the conversation. Rotated; the new one is
  project-scoped.
- Two commits went out with a failing test because pytest's exit code was
  read through a pipe. Exit code checked directly now.

## What is not yet known, in order of how much it matters

1. **Does asking help?** The fleet's cohort table, tomorrow.
2. **Do failures repeat across *strangers*?** Only our own agents so far.
   Production has 0 independent reporters. Nothing we run can answer this.
3. **How bad is the naming split?** The fleet's naming table, in 48 hours.
4. **Does decay ever fire on real data?** Never observed.
5. **Will anyone use it?** Unchanged.
