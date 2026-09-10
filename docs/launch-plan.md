# FailEcho launch plan

The conversion this plan optimises for is **not** visitor → signup. There is no
signup. It is:

```
developer sees FailEcho
    → connects an agent
    → the agent reports automatically
    → the network gains useful data
```

A page view that does not end in a connected agent is worth nothing here, and a
connected agent that never reports a recovery outcome is worth very little.
Plan accordingly.

---

## Sequence

### 1. Public deployment  ✅ done

`https://failecho.com` is live behind Cloudflare, TLS auto-renewing, one
uvicorn worker, SQLite in WAL mode, ~100 MB RSS. `failecho.dev` redirects to
`.com` with paths preserved. Demo data cleared, `FIN_DEMO_MODE=0`, real
counters honestly at zero.

### 2. GitHub repository

Public repo is the second-most-visited surface after the homepage, and for
developers it is often the first.

- `README.md` opens with the name, tagline, the Agent A/Agent B story and the
  MCP endpoint — integration is visible before any architecture
- `LICENSE` (MIT), `SECURITY.md`, `CONTRIBUTING.md`, `.env.example` are in place
- repository description and topics from `docs/marketing.md`
- set `FIN_GITHUB_URL` on the server afterwards so the site links back

### 3. MCP directories and registries

This is where agents and their authors actually look for tools. Submit to the
public MCP server listings with the description in `docs/marketing.md`. The
listing must lead with **when to call it** — "after another tool fails, before
retrying" — because that sentence is what a model matches against.

Prerequisite: `/llms.txt` and `/openapi.json` are already live and describe the
service in machine-readable terms.

### 4. One short demo clip

Thirty seconds, no narration, terminal only: `run_demo.py` running, five agents
failing and reporting, Agent B receiving `refresh_schema` and recovering. The
existing demo already produces this output; record it, do not stage it.

This is the single most persuasive asset available, because the network effect
is hard to describe and obvious to watch.

### 5. Developer communities

In rough order of fit:

- Hacker News (Show HN) — draft in `docs/marketing.md`
- r/AI_Agents, r/LocalLLaMA — technical framing, no marketing voice
- MCP and agent-framework Discords, where the failure being solved is felt daily

Post once, answer every reply properly, and do not cross-post the same text.
The Show HN comment should ask the two open questions honestly rather than
claim the problem is solved.

### 6. First 10 independent reporters

The milestone that matters more than any launch-day traffic. Ten independent
reporters means the fingerprints they share are not an artefact of one setup.

Direct outreach beats broadcast here: people already running agents against
GitHub, Stripe, Slack, Notion or search APIs, asked individually to add one
line. Their failures are the ones most likely to overlap with somebody else's.

### 7. First framework integration

Adoption is a framework decision, not a user decision. An agent framework that
calls FailEcho in its default error path reports forever, from every user it
has — one integration is worth thousands of individual installs.

`client/failecho/adapters.py` is the seam: four events, one direction. The
Pydantic AI integration is the reference implementation. Candidates after it:
LangChain, LlamaIndex, CrewAI, the OpenAI Agents SDK, Claude Code hooks.

**No paid advertising.** Not at this stage. Paid traffic converts strangers
into visitors, and this product needs visitors to become reporters, which
advertising cannot buy.

---

## Metrics that matter

Read these from `/v1/stats`. They are deliberately unflattering.

| Metric | Field | What it answers |
|---|---|---|
| Real observations / day | `real_observations_24h` | is anything real arriving? |
| Independent reporters / day | `real_reporters_24h` | how many unrelated systems? |
| Real failures / day | `real_failures_24h` | is there anything to learn from? |
| Real successes / day | `real_successes_24h` | do we have denominators? |
| Known-query hit rate | `known_hit_rate_24h` | when an agent asks, does FailEcho know? |
| Recovery outcomes / failures | `recovery_outcome_ratio_24h` | is the loop being closed? |
| Cross-agent help events | `cross_agent_help_24h` | **the product event** |

**The one that decides everything** is `cross_agent_help_24h`: an identified
agent received a recommendation backed by at least one *other* reporter. That
is the hypothesis, instrumented. It is counted conservatively — anonymous
callers and single-reporter evidence do not count — because overcounting it
would be lying to ourselves first.

**The fragile one** is `recovery_outcome_ratio_24h`. Reporting a failure is
automatic; reporting whether the fix worked requires the agent to come back
after recovering. If this stays near zero, FailEcho is an error counter and the
product does not work, however much traffic arrives.

**Do not optimise for** page views, stars, followers, or the total observation
count with demo rows included. The site keeps demo and real telemetry apart
specifically so this temptation stays unavailable.

---

## Milestones

```
1   one real reporter
2   ten independent real reporters
3   100+ real observations per day
4   the first repeated real fingerprint          ← two unrelated agents, one failure
5   the first real cross-agent recovery benefit  ← the hypothesis holds
```

Milestone 4 is the first evidence that failure overlap between unrelated agents
exists at all. Milestone 5 is the product working. Everything before them is
plumbing that happens to be finished.

---

## Honest risks

Worth writing down now, so a bad week is not mistaken for a verdict:

- **Empty network.** No data, no value, no adoption. The seeded demo and the
  live demo script exist to show the mechanism while the network fills.
- **Overlap may be thin** outside popular public APIs. Private internal tools
  will not benefit, and that is most enterprise traffic.
- **The recovery loop may not close.** Failures will arrive; outcomes may not.
- **Incumbents hold the corpus.** Sentry and Datadog already have
  cross-organisation error data and could aggregate it. Their blocker is
  contractual, not technical.
- **Policy, not privacy engineering.** Even collecting metadata only, some
  security teams block third-party telemetry outright. Self-hosting is the
  answer, and it already works.
