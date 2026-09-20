# FailEcho lab: four days, with and without

*Window: 2026-09-16 09:17 to 2026-09-20 10:46 UTC. Everything below comes from
the lab (lab.failecho.com), a separate instance our own test agents report to.
**None of it is adoption**: production has 0 independent reporters. The live
page is [lab.failecho.com/fleet](https://lab.failecho.com/fleet).*

*An outside review on 20 September (docs/review-2026-09-20.md) checked the
arithmetic and found it sound, and found the wording around it too wide. This
report uses the narrower wording. Nothing here shows a generalizable uplift for
an ordinary installation; it shows what one mechanism did against one weak
control, in one lab.*

## How a run is graded, exactly

- **A model run counts as completed when it returned an answer** rather than a
  failure marker. An answer that is wrong, vague or unhelpful counts. The
  metric is therefore called **"runs marked completed"**, not "tasks
  completed".
- **A builder or OpenCode run counts** when the file the task named exists and
  matches the task's pattern. A partly correct file can match.
- **Per-call numbers are the harder evidence**: failures met, second attempts
  made, second attempts that succeeded. Those come from the call ledger, not
  from judging an answer.
- **The control is one retry after 3 seconds.** It is not competent recovery
  engineering (no `Retry-After` handling, no fallback table, no circuit
  breaker), so a win here is a win over a weak policy, not over a careful
  engineer.
- **Runs the guest kernel killed for memory before they produced a result are
  excluded** from every rate and counted on their own row. They ran 7 to 0
  against the FailEcho side, whose twins run one extra process in the same
  768 MB guest.

## What was run

45 personas, 49,230 observations over 23 services, 6,587 recovery outcomes.
Personas come in twins: same workload, same model, alternating order. The
*ask* twin consults FailEcho before retrying; the *blind* twin retries by fixed
rules. Six explorers try actions the network has no evidence for, which is how
the evidence askers inherit gets made.

Services: PyPI, npm, crates.io, the GitHub API, Stack Exchange, httpbingo (a
test service that returns what it is asked for), and the model providers
themselves. Models: gpt-oss-20b/120b (groq), gemini-flash, three free models on
OpenRouter, gpt-oss:20b / nemotron-3-nano / gemma4 (Ollama), nemotron-3-super
(NVIDIA), ministral (Mistral), qwen3.6-27b (xKiro). All free tiers, all under
their documented caps.

## The strongest result: a failure with a known fix

Model providers under their own quotas, since the control changed on 18 Sep
05:30. 516 ask runs, 511 blind.

| | With FailEcho | Without |
|---|---|---|
| Runs marked completed | 92.8 % | 77.7 % |
| Provider failures met | 142 | 142 |
| **Provider failures recovered** | **78.2 %** | **20.4 %** |
| Tokens per completed run | 1,835 | 1,984 |
| Seconds per run | 9.6 | 9.9 |

Both sides met the same number of failures. The difference is what they did
next: the network's evidence says `switch_model` works, and it does. This is
the one place where the completion number and the per-call number agree, and
it is the result to lead with.

## The rest, honestly

| Group | Runs | Marked completed | What it means |
|---|---|---|---|
| Flaky test endpoints | 158 vs 158 | n/a | **18.6 vs 25.4 s per run**; 15.2 vs 21.9 s lost inside failures; 1.51 vs 2.0 attempts per failure; 332 retries the network said to skip |
| Real APIs | 1,095 vs 1,090 | 72.7 vs 66.2 % | Mostly fewer wasted retries; tokens even (1,202 vs 1,206) |
| Coding agents | 434 vs 434 | 84.8 vs 83.6 % | Their failures are their own bugs; the one gain is waiting on rate limits, 3.6 vs 4.1 s per run |
| OpenCode, FailEcho's MCP tools | 113 vs 121 | 77.9 vs 82.6 % | **Behind.** The model must choose to ask and does so 0.12 times per run, while paying for four tool definitions every step: 59,078 vs 54,229 tokens |
| Python wrapper (shipped) | 84 vs 84 | 100 vs 100 % | Advice attached to 38 failures; all were GitHub 403s, which nothing fixes without a token |
| OpenCode behind the proxy | 65 vs 65 | 86.2 vs 84.6 % | **Not a FailEcho win**: no advice line has appeared in a scheduled run yet. The difference is provider luck |
| Advice read from production | 55 vs 54 | 90.9 vs 98.1 % | What a new user gets today: production has almost no evidence, so asking costs time and returns nothing |

## What the advice is worth, by failure shape

| Shape | Skipped | Advised retries that worked | Blind retries that worked |
|---|---|---|---|
| httpbingo 503 | 163 | 103 / 158 (65 %) | 123 / 331 (37 %) |
| GitHub 403 rate limit | 51 | n/a (advice was skip) | 57 / 1,297 (4.4 %) |
| Stack Exchange 429 | 182 | n/a (advice was skip) | 0 / 186 |

The skip verdict is where the review's caution matters: in the hours and shapes
where askers were told to skip, blind agents retried 63 times on GitHub and
recovered **5** times. So "skip" is right far more often than not, but it is
not free, and the count of skips is **not** a count of avoided failures.

## Onboarding

127 runs of a cheap model reading llms.txt in a clean VM: 81 graded, 54 passed.
The rest wrote config in the wrong place, skipped the verification step, or
claimed success without checking.

## What shipped during the window

- **failecho-autoreport 0.1.5** (PyPI): advice attached to the exception when a
  wrapped call fails.
- **failecho-mcp 0.2.1** (PyPI and npm): the proxy. Put it in front of any MCP
  server and a failed tool call comes back with one line of evidence; it can
  also draw on the evidence filed under the API host the server wraps.
- Both were installed from the registries in clean environments and checked
  against real servers.

## Known limits

- **Cold start.** Production has 0 independent reporters and only our
  first-party agent's evidence. A new user gets little until others report.
- **The bare MCP endpoint barely helps**: models rarely call a tool they have
  to choose. Use the plugin, the wrapper or the proxy.
- **The proxy** does not support MCP servers that log in with OAuth, and does
  not carry a server's own event stream. Remote servers are re-serialised, not
  forwarded byte for byte; that guarantee holds for local stdio servers.
- **Inferred recovery is correlation.** A repeat of a failed call that succeeds
  is recorded as a retry that worked, without knowing what else changed.
- **Reporter ids are not independence.** They are self-chosen, and a restart
  mints a new one, so "unique reporters" counts processes.
- **Nothing here sees an agent's own `curl`** or shell commands.
- **Grading is shallow** (see the top). Deliverable-level grading is the next
  measurement to build.

## What would make the next report stronger

1. Grade the artefact, not its shape.
2. Add a third arm: competent local recovery, with and without FailEcho.
3. Report inferred outcomes separately from reported ones.
4. Real users, so production's evidence is not ours alone.
