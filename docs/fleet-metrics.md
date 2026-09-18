# Fleet metrics: every number on the scoreboard, defined

So that a change to the product can be judged on all of them and not only
on the one it moved. Recorded per run in `state.json`, aggregated per cohort
into `fleet.json`, shown on `/fleet`. Where a field started being recorded
later than the run itself, the date is given; aggregates use only runs that
carry the field.

## Per run (`state.json` → `runs[]`)

| field | meaning |
|---|---|
| `reporter`, `path`, `provider`, `model`, `asks` | who ran: persona, reporting path (decorator / auto / mcp / builder), model provider and model, whether it asks the network |
| `tool_calls` | tool invocations, including retries |
| `model_calls` | provider chat completions requested, including retries after a provider failure |
| `seconds` | wall time of the whole run |
| `failures[]` | one entry per tool or provider call that failed: `service`, `operation`, `error_type`, `error_code`, `asked`, `recommended` (the network's action, if any), `attempts` (1 + retries), `recovered`, `skipped` (no retry on the network's `skip`), `explored` (an explorer's untried action), `seconds` (time inside the failure, retries and waits included; since 2026-09-17 06:00) |
| `metrics.tokens_prompt`, `metrics.tokens_completion` | what the provider billed for the run, from each response's `usage` (since 2026-09-17 10:30) |
| `metrics.asks` | questions put to the network |
| `metrics.ask_seconds` | wall time spent inside those questions — the product's overhead |
| `metrics.wait_seconds` | time slept on backoff or a reset header |
| `metrics.completed` | model run: a real answer, not "(provider failed…)" or "(model budget exhausted)"; cron run: every call eventually succeeded; builder: the task came out done |
| `metrics.calls_first_try` | tool calls that succeeded without a retry |
| `metrics.calls_recovered` | failed calls a retry fixed |
| `metrics.calls_failed` | failed calls that stayed failed (including skips) |
| `metrics.model_retries_after_skip` | the model called the same tool with the same arguments right after the network said skip — a wasted turn, and the sign the advice did not reach the model in a form it acted on (since 2026-09-17 15:00) |
| `build` | builders only: `vm_runs`, `local_failures`, `shared_failures[]`, `task_done`, `coverage[]` (per program: connections the fence saw vs calls the wrapper observed), `sandbox` |
| `task`, `answer` | builders only: what was asked (160 chars) and how the model summed up (200 chars) |

## Per cohort (`fleet.json`)

Cohorts: `real / ask`, `real / blind` (real services, model and cron
personas), `test / ask`, `test / blind` (httpbingo endpoints), `build / ask`,
`build / blind`, `explore` (no twin; explorers try untried actions).

- `cohorts[]` — runs, failures, attempts per failure, recovered, asked,
  recommended, skipped, provider failures / recovered, explored.
- `costs[]` — per-run averages of everything above: `completed_rate`,
  `tokens_per_run`, `tokens_per_completed`, `model_calls_per_run`,
  `tool_calls_per_run`, `seconds_per_run`, `asks_per_run`,
  `ask_seconds_per_run`, `wait_seconds_per_run`, `failure_seconds_per_run`,
  plus the sums.
- `real_targets[]` — real services only, controlled twins only, fair-order
  runs only (since 2026-09-17 06:30): failures, attempts per failure,
  recovered, skipped, seconds lost, per service and cohort. Failure counts
  between twins are not comparable on one shared budget; per-failure
  behaviour is.
- `build[]` — VM runs, tasks done, local, shared, shared share, coverage
  (traffic runs, unobserved runs, connections, observed calls).
- `recent_builds[]` — the last twelve builder runs.
- `repeats[]`, `naming[]` — cross-reporter fingerprints and the naming
  split, from the lab database.
- `canary`, `onboard` — the install canary's and the onboarding test's own
  reports, carried through unchanged.

## Quota

A provider that answers a *daily* quota error (groq's TPD, Gemini's quota)
is marked dead; the scheduler skips its personas and runs the next live one
in the same tick, and the onboarding test skips its models.
`totals.providers_out_of_quota` and `totals.skipped_for_quota_today` say so
on the scoreboard, so a quiet afternoon for one provider is explicable
rather than a mystery. Since 2026-09-17 22:30.

The mark expires after two hours (`FLEET_QUOTA_PROBE_SECONDS`), and the
next persona on that provider is the probe: it either runs, which clears
the mark, or dies on the same error, which sets it again. The first
version held the mark until midnight UTC; the providers' days are not ours
(groq answered again by 02:49 UTC on 18 Sep after "used 199178 of 200000"
at 00:13, Gemini's free tier resets at midnight Pacific), and that cost
groq's personas most of a day. Since 2026-09-18 03:00.

## Versus group `provider` (since 2026-09-18 05:30)

Model-driven personas (not builders, not explorers), runs since blind
started retrying a failed provider once. Rows: tasks completed, provider
failures met (should be near-equal between cohorts; if not, the sample is
skewed), provider failures recovered, tokens per completed task, seconds
per run. `since` is carried in the group so a reader sees the window.

## What to compare, and what not to

Ask vs blind twins share workloads, providers and one IP address. Compare
per-run and per-failure numbers between them. Do not compare how many
failures each met on a service with a shared hourly budget: whoever runs
second meets more. The order alternates every cycle since 06:30 UTC.
