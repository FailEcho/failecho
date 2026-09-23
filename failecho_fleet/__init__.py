"""The fleet: eighteen of FailEcho's own agents, one at a time, against the lab.

See docs/fleet-test-plan.md for what this is for. In one line: half the fleet
asks the network before retrying and half retries blind, on identical
workloads, so "does asking help" becomes a number instead of an argument.

Every run is one persona, chosen round-robin from the state file, executed
as one process and then gone. The scheduler is the systemd timer; this module
only knows how to run persona N and how to write the scoreboard afterwards.

Three things this refuses to do, each checked before any request is made:

* run against anything but the lab -- the endpoint's host is asserted at
  startup, and production's hostname is refused by name;
* run with production's operator token in the environment -- it is not
  needed for the lab and its presence means the environment is wrong;
* hold more than one persona in memory -- there is no loop here, one run and
  exit, and the unit caps memory.

Nothing is manufactured. The failures are whatever PyPI, npm, GitHub and four
free-tier model providers actually do when eighteen agents share one IP for two
days.
"""

from __future__ import annotations

import contextlib
import contextvars
import fcntl
import datetime as dt
import json
import os
import signal
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from failecho_autoreport import FailEcho, classify

from .builder import BUILDER_SCHEMAS, BUILDER_TASKS, DOC_HOSTS, SYSTEM_PROMPT as BUILDER_PROMPT, Builder, fetch_doc
from .grading import grade
from .opencode import OC_PROVIDERS, OC_TASKS, OCP_TASKS, run_opencode

__version__ = "0.1.0"
UA = "failecho-fleet/0.1 (+https://failecho.com; lab)"

LAB_ENDPOINT = (os.environ.get("FAILECHO_ENDPOINT") or "").rstrip("/")
# systemd joins several StateDirectory= paths with ":"; the first is ours
STATE_DIR = ((os.environ.get("STATE_DIRECTORY") or "").split(":")[0]
             or os.environ.get("FLEET_STATE_DIR") or "/tmp/failecho-fleet")
REPORT_PATH = os.environ.get("FLEET_REPORT_PATH") or os.path.join(STATE_DIR, "fleet.json")
LAB_DB = os.environ.get("FLEET_LAB_DB") or ""
PRODUCTION_HOSTS = ("failecho.com", "www.failecho.com")
MAX_RUNS_PER_DAY = int(os.environ.get("FLEET_MAX_RUNS_PER_DAY") or 3000)
#: How long a provider marked out of daily quota is left alone before one
#: persona probes it again. Two hours: a wasted call every two hours while
#: it is truly dead, against a day of skipped slots when its window rolls
#: over on a clock that is not ours.
QUOTA_PROBE_SECONDS = int(os.environ.get("FLEET_QUOTA_PROBE_SECONDS") or 7200)
#: Largest cached answer, serialised, an ETag entry may keep (see save_etags).
ETAG_BODY_MAX = 32 * 1024
#: An OpenCode run's wall-clock budget. A six-step task takes ~150 s on
#: NVIDIA; the eighteen-step one hit 280. The fleet slot waits for it.
#: 240 until 2026-09-19: the 220 s inner clock, not the task, decided most
#: failures of both twins, so the versus row measured speed, not outcome.
#: Not above 300: the guest caps any task there (guest_init.MAX_TIMEOUT,
#: baked into the rootfs) and a run it kills loses all of its events.
OPENCODE_TIMEOUT = int(os.environ.get("FLEET_OPENCODE_TIMEOUT") or 300)
#: Least time between two OpenCode runs. Its guest takes 768 MB, three times
#: a builder's, and production lives on the same host. 10 min was not enough:
#: at 08:18 and 08:31 on 20 Sep the kernel ran out of memory and killed
#: firecracker (the test VM, not the server -- but the next one could be the
#: server), with swap at 3.9 of 4 GB. 30 min then, and 2 GB more swap; back to
#: 10 min on 22 Sep, when the host's other projects were stopped and 889 MB
#: came free.
#: A gap costs runs per hour, not the comparison: both twins wait alike.
OPENCODE_MIN_GAP_SECONDS = int(os.environ.get("FLEET_OPENCODE_MIN_GAP") or 1800)
#: What the sandbox guest is told to report to. Unset means the in-guest
#: wrapper stays off; there is deliberately no default.
LAB_PUBLIC_URL = (os.environ.get("FLEET_LAB_PUBLIC_URL") or "").rstrip("/") or None

PROVIDERS = {
    "groq": {"host": "api.groq.com", "url": "https://api.groq.com/openai/v1/chat/completions",
             # groq documents 1,000 requests a day *per model*; three models rotate
             # here, so 3,000 is the tier (its per-model token budget binds first)
             "key": os.environ.get("GROQ_API_KEY"), "models": ["openai/gpt-oss-20b"], "daily_cap": 3000,
             "alt_models": ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]},
    "gemini": {"host": "generativelanguage.googleapis.com",
               "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
               # the free tier's own answer on 18 Sep 2026:
               # GenerateRequestsPerDayPerProjectPerModel-FreeTier, quotaValue 20,
               # for gemini-3.8-flash. Two models, so 40 is the tier, not above it.
               "key": os.environ.get("GEMINI_API_KEY"), "models": ["gemini-flash-latest"], "daily_cap": 40,
               "alt_models": ["gemini-flash-lite-latest"]},
    # Free-tier key, zero spend, no limit set -- checked before this was added.
    # Three free models verified to make real tool calls; rotated per run so
    # no single one carries the persona. Free models are rate-limited hard,
    # and those 429s are real data. The daily cap keeps us a polite tenant.
    "openrouter": {"host": "openrouter.ai", "url": "https://openrouter.ai/api/v1/chat/completions",
                   "key": os.environ.get("OPEN_ROUTER_API_KEY"),
                   "models": ["nex-agi/nex-n2.5-mini:free", "liquid/lfm-2.5-2.6b:free",
                              "inclusionai/ling-3.0-flash-vl:free"],
                   "headers": {"HTTP-Referer": "https://failecho.com", "X-Title": "FailEcho fleet lab"},
                   # "free-models-per-day ... X-RateLimit-Limit: 50" without credits (18 Sep 2026)
                   "daily_cap": 50, "alt_models": ["liquid/lfm-2.5-2.6b:free", "nex-agi/nex-n2.5-mini:free"]},
    # Ollama's cloud, free tier: three models verified for tool calls, two
    # others answered 402 "requires a subscription", which is where the tier
    # ends and where the fleet stops. gpt-oss:20b here is the same model as
    # Groq's gpt-oss-20b -- the one returning "parsing failed" 400s -- so the
    # fleet will show whether that failure belongs to the model or the host.
    # NVIDIA's API catalog (added 2026-09-18 06:30 UTC, user-issued key).
    # Documented free tier: 40 requests a minute. The account page shows
    # only that ("Your API Rate Limit: Up to 40 rpm", checked by the user
    # 18 Sep 20:40) and no credit balance, so there is no daily pool to run
    # out of. Cap 2,400 a day, an hour's worth of that rate (was 400,
    # reached by 16:30 once OpenCode ran). Both models
    # verified to make real tool calls; gpt-oss-20b is the same model groq
    # and Ollama serve, a third host for the same weights.
    "nvidia": {"host": "integrate.api.nvidia.com", "url": "https://integrate.api.nvidia.com/v1/chat/completions",
               "key": os.environ.get("NVIDIA_API_KEY"), "models": ["openai/gpt-oss-20b"], "daily_cap": 2400,
               "alt_models": ["nvidia/nemotron-3-super-120b-a12b"]},
    # Mistral (added 2026-09-18 07:10 UTC, user-issued key). The key's own
    # headers say what this tier allows per model: ministral-8b 188 req/min,
    # ministral-14b 30, codestral 125; mistral-small, -medium and magistral
    # answer 429 with a limit of 0, so they are not on this tier. Tool calls
    # verified on ministral-8b. Cap 2,400 a day, well inside those minutes.
    "mistral": {"host": "api.mistral.ai", "url": "https://api.mistral.ai/v1/chat/completions",
                "key": os.environ.get("MISTRAL_API_KEY"), "models": ["ministral-8b-latest"], "daily_cap": 2400,
                "alt_models": ["ministral-14b-latest", "codestral-latest"]},
    # xKiro (added 2026-09-18 08:00 UTC, user-issued key): a routing gateway
    # whose site states its free tier as 500,000 tokens a day, 1,000,000
    # once the account is verified on Telegram (the user did, 08:15 UTC).
    # Tool calls verified on all three models. 600 calls a day at the
    # fleet's prompt sizes (~1k tokens on the qwen chat template) stays
    # inside the verified tier.
    "xkiro": {"host": "api.xkiro.com", "url": "https://api.xkiro.com/v1/chat/completions",
              # the tier is tokens, not calls: 1,000,000 a day verified. A call here
              # averaged 2,246 tokens on 18 Sep (builders), and 450 calls had
              # already spent 1.01M by 20:30 -- the call cap alone let it run over.
              "key": os.environ.get("XKIRO_API_KEY"), "models": ["qwen/qwen3.6-27b:free"], "daily_cap": 2000,
              "daily_token_cap": 950_000,
              "alt_models": ["minimax/minimax-m2.7-highspeed:free", "qwen/qwen3.5-flash:free"]},
    "ollama": {"host": "ollama.com", "url": "https://ollama.com/v1/chat/completions",
               "key": os.environ.get("LLAMA_API_KEY"),
               "models": ["gpt-oss:20b", "nemotron-3-nano:30b", "gemma4:31b"], "daily_cap": 150,
               "alt_models": ["nemotron-3-nano:30b", "gpt-oss:20b"]},
}

#: What an agent can do about a model provider failing, beyond retrying.
#: Explorers try these, one per failure, and report what happened; askers
#: then inherit whichever the network recommends. Blind personas, and askers
#: the network has nothing for, do what an agent without the network does:
#: one plain retry after a short pause (since PROVIDER_CONTROL_SINCE; on the
#: first day they gave up at once, which flattered the ask side).
#: What a careful engineer does without any network: honour the reset header,
#: back off with a rising delay, keep a fallback model list, stop retrying what
#: never recovers, and break the circuit after two failures on one model. The
#: old control was a single retry after 3 s, so every comparison answered "is
#: FailEcho better than retrying blindly?" -- which is not the question a
#: reader has (review, 20 Sep). Returns the actions to try, in order.
def local_plan(error_type: str, code: str | None, exc: BaseException, consecutive: int) -> list[str]:
    if error_type in ("auth_error", "not_found"):
        return []                       # nothing retryable: stop, as one should
    if error_type == "validation_error":
        return ["retry_without_tool_choice"]
    if error_type == "rate_limit":
        headers = getattr(exc, "headers", None)
        # x-ratelimit-reset is GitHub's, and GitHub is where most of the
        # fleet's rate limits come from: leaving it out meant the careful
        # plan backed off blindly exactly where it should have waited
        has_reset = bool(headers and any(headers.get(h) for h in (
            "retry-after", "x-ratelimit-reset", "x-ratelimit-reset-tokens",
            "x-ratelimit-reset-requests")))
        if _daily_quota(exc):
            return ["switch_model"]     # the day's pool is gone; waiting is pointless
        return (["wait_until_reset", "switch_model"] if has_reset
                else ["backoff", "switch_model"])
    if error_type in ("server_error", "timeout", "connection_error"):
        # two failures on this model already: break the circuit rather than
        # spend a third attempt on it
        return ["switch_model", "backoff"] if consecutive >= 2 else ["backoff", "switch_model"]
    return ["retry"]


PROVIDER_ACTIONS = {
    "rate_limit": ["wait_until_reset", "backoff", "switch_model"],
    "validation_error": ["retry_without_tool_choice", "switch_model", "retry"],
    "server_error": ["retry", "switch_model", "backoff"],
    "timeout": ["retry", "switch_model"],
    "connection_error": ["retry", "backoff"],
}
KNOWN_ACTIONS = ("backoff", "retry", "refresh_schema", "skip", "wait_until_reset", "switch_model",
                 "retry_without_tool_choice", "conditional_request")
#: When blind personas (and askers without advice) started retrying a failed
#: provider once instead of giving up. Provider-failure comparisons start here.
PROVIDER_CONTROL_SINCE = "2026-09-18T05:30:00"
#: An explorer keeps trying an action until the network has enough evidence
#: to rule on it. Matches the server's min_recovery_attempts; below it a
#: single 1/1 is not a recommendation and the askers inherit nothing.
EXPLORE_UNTIL_ATTEMPTS = 5


def explore(candidates: list[str], evidence: list[dict]) -> str | None:
    """The action an explorer tries next: one nobody has tried, else the one
    below the evidence floor that has worked best so far (least tried on a
    tie), else None -- everything has been ruled on. The first version
    tried each action once and stopped, so switch_model sat at 1 of 1 for
    a day and no asker ever inherited it."""
    by = {a.get("action"): a for a in evidence if isinstance(a, dict)}
    untried = [a for a in candidates if a not in by]
    if untried:
        return untried[0]
    open_ = [a for a in candidates if int(by[a].get("attempts") or 0) < EXPLORE_UNTIL_ATTEMPTS]
    if not open_:
        return None
    return max(open_, key=lambda a: (int(by[a].get("successes") or 0) / max(int(by[a].get("attempts") or 0), 1),
                                     -int(by[a].get("attempts") or 0)))

# ---------------------------------------------------------------------------
# personas
# ---------------------------------------------------------------------------

PYPI_NPM = [
    "Latest versions of the PyPI packages requests, httpx and fastapi, one line each.",
    "Which is newer, npm express or PyPI flask? Give both versions.",
    "Latest versions of npm react, vue and svelte.",
    "Does the PyPI package 'definitely-not-a-real-package-xyz-123' exist? And what is the latest 'uv'?",
]
GITHUB = [
    "Star counts for modelcontextprotocol/python-sdk and modelcontextprotocol/typescript-sdk.",
    "Latest release tag of FailEcho/failecho and its open issue count.",
    "Open issues on langchain-ai/langchain and run-llama/llama_index.",
    "Latest release of astral-sh/uv on GitHub, and does 'uv' on PyPI match it?",
    "Latest release tag of FailEcho/failecho-does-not-exist. If it does not exist, say so.",
]
MIXED = PYPI_NPM[:2] + GITHUB[:3]

#: The six calls a nightly job makes, every night, in the same order. No
#: model. The pattern the Reddit post described.
CRON_CALLS = [
    ("pypi", "requests"), ("pypi", "fastapi"), ("npm", "express"),
    ("github", "FailEcho/failecho"), ("github", "modelcontextprotocol/python-sdk"),
    ("github_release", "astral-sh/uv"),
]

#: The test-service job: the same eleven calls every run. Enough 429s, 503s
#: and timeouts to give the recovery loop something to do, and three writes
#: that never fail so the unverified-success flag has something to fire on.
TEST_CALLS = [
    ("tool", "flaky_read"), ("tool", "flaky_read"), ("tool", "flaky_read"),
    ("tool", "throttled_read"), ("tool", "throttled_read"),
    ("tool", "always_broken"), ("tool", "slow_read"),
    ("tool", "echo_write"), ("tool", "echo_write"), ("tool", "echo_write"),
    ("pypi", "requests"),
]

#: Twenty real GitHub calls per run. The unauthenticated limit is 60 an hour
#: per IP, shared by the whole fleet -- which is exactly how a real fleet
#: behind one NAT experiences it. Some hours this crosses the line and the
#: 403s are real.
GH_REPOS = ["FailEcho/failecho", "modelcontextprotocol/python-sdk", "modelcontextprotocol/typescript-sdk",
            "langchain-ai/langchain", "run-llama/llama_index", "astral-sh/uv", "pallets/flask",
            "psf/requests", "encode/httpx", "fastapi/fastapi"]
GH_HEAVY = [("github", r) for r in GH_REPOS] + [("github_release", r) for r in GH_REPOS]

#: Real limits, honestly crossed: six crates.io calls back to back (policy is
#: one a second), four Stack Exchange calls (300 a day per IP, shared by the
#: fleet), three GitHub searches (10 a minute unauthenticated), then two
#: repos. Every run, in this order.
LIMIT_CALLS = [
    ("crate", "serde"), ("crate", "tokio"), ("crate", "reqwest"), ("crate", "clap"), ("crate", "anyhow"), ("crate", "rand"),
    ("so", "python"), ("so", "rust"), ("so", "javascript"), ("so", "go"),
    ("gh_search", "mcp server"), ("gh_search", "failure intelligence agents"), ("gh_search", "retry backoff library"),
    ("github", "FailEcho/failecho"), ("github", "astral-sh/uv"),
]

PERSONAS = [
    # reporter,           path,        provider, asks,  workload
    ("fleet-decor-ask-a",  "decorator", "groq",   True,  PYPI_NPM),
    ("fleet-decor-ask-b",  "decorator", "ollama", True,  GITHUB),
    ("fleet-decor-blind-a","decorator", "groq",   False, PYPI_NPM),
    ("fleet-decor-blind-b","decorator", "ollama", False, GITHUB),
    ("fleet-auto-ask-a",   "auto",      "openrouter", True,  MIXED),
    ("fleet-auto-ask-b",   "auto",      "gemini",     True,  MIXED),
    ("fleet-auto-blind-a", "auto",      "openrouter", False, MIXED),
    ("fleet-auto-blind-b", "auto",      "gemini",     False, MIXED),
    ("fleet-mcp-ask",      "mcp",       "groq",       True,  GITHUB),
    ("fleet-mcp-blind",    "mcp",       "groq",       False, GITHUB),
    ("fleet-cron-a",       "decorator", None,     True,  CRON_CALLS),
    ("fleet-cron-b",       "decorator", None,     False, CRON_CALLS),
    # test targets: a service that returns what it is asked for
    ("fleet-test-ask",     "decorator", None,     True,  TEST_CALLS),
    ("fleet-test-blind",   "decorator", None,     False, TEST_CALLS),
    # real target, real limit: twenty GitHub calls a run from a shared IP
    ("fleet-gh-ask",       "decorator", None,     True,  GH_HEAVY),
    ("fleet-gh-blind",     "decorator", None,     False, GH_HEAVY),
    # builders: write code, run it in a throwaway VM, fix it, run it again.
    # Added 2026-09-16 15:40 UTC, after the first sixteen had run for six
    # hours; the twins share a provider so the only difference is asking.
    ("fleet-build-ask",    "builder",   "groq",   True,  BUILDER_TASKS),
    ("fleet-build-blind",  "builder",   "groq",   False, BUILDER_TASKS),
    # explorers: on a provider failure they try an action nobody has evidence
    # for yet and report the outcome. They pay; askers inherit. Added
    # 2026-09-17 05:00 UTC.
    ("fleet-explore-a",    "decorator", "groq",   True,  PYPI_NPM),
    ("fleet-explore-b",    "decorator", "gemini", True,  GITHUB),
    # real limits, real fixes (2026-09-17 06:00 UTC): twins on services that
    # push back under honest use, and a third explorer with no model that
    # meets GitHub's 403 and the limits every run and tries what nobody has
    ("fleet-limits-ask",   "decorator", None,     True,  LIMIT_CALLS),
    ("fleet-limits-blind", "decorator", None,     False, LIMIT_CALLS),
    ("fleet-explore-c",    "decorator", None,     True,  GH_HEAVY + LIMIT_CALLS),
    # NVIDIA (2026-09-18 06:30 UTC): a second provider with a usable tier,
    # so the provider comparison does not rest on groq alone. Decorator
    # twins, builder twins, and an explorer to make the evidence.
    ("fleet-decor-ask-c",  "decorator", "nvidia", True,  MIXED),
    ("fleet-decor-blind-c","decorator", "nvidia", False, MIXED),
    ("fleet-build-ask-n",  "builder",   "nvidia", True,  BUILDER_TASKS),
    ("fleet-build-blind-n","builder",   "nvidia", False, BUILDER_TASKS),
    ("fleet-explore-d",    "decorator", "nvidia", True,  MIXED),
    # Mistral (2026-09-18 07:10 UTC): decorator twins and an explorer.
    ("fleet-decor-ask-d",  "decorator", "mistral", True,  PYPI_NPM),
    ("fleet-decor-blind-d","decorator", "mistral", False, PYPI_NPM),
    ("fleet-explore-e",    "decorator", "mistral", True,  GITHUB),
    # xKiro (2026-09-18 08:00 UTC): decorator twins, builder twins, explorer.
    ("fleet-decor-ask-e",  "decorator", "xkiro",  True,  GITHUB),
    ("fleet-decor-blind-e","decorator", "xkiro",  False, GITHUB),
    ("fleet-build-ask-x",  "builder",   "xkiro",  True,  BUILDER_TASKS),
    ("fleet-build-blind-x","builder",   "xkiro",  False, BUILDER_TASKS),
    ("fleet-explore-f",    "decorator", "xkiro",  True,  PYPI_NPM),
    # OpenCode (2026-09-18 12:30 UTC, user-directed): a real agent product,
    # headless in the VM, with FailEcho's MCP server in its config (ask) or
    # without (blind). Same task, same model, same project otherwise.
    ("fleet-oc-ask-n",     "opencode",  "nvidia", True,  OC_TASKS),
    ("fleet-oc-blind-n",   "opencode",  "nvidia", False, OC_TASKS),
    # a second explorer on groq (2026-09-18 13:30 UTC): the server rules on an
    # action at five attempts, and one explorer made about one an hour
    ("fleet-explore-a2",   "decorator", "groq",   True,  GITHUB),
    # The shipped path, as a user would run it (2026-09-19): tools wrapped by
    # failecho-autoreport, advice on for one twin. Nothing in the harness
    # asks or retries for either; the ask twin's model sees the wrapper's
    # one-line advice in the tool error and decides for itself.
    ("fleet-wrap-ask",     "wrapped",   "mistral", True,  MIXED),
    ("fleet-wrap-blind",   "wrapped",   "mistral", False, MIXED),
    # The MCP proxy, as a user would run it (2026-09-19): OpenCode with one
    # real-API MCP server (PyPI, npm, crates.io, GitHub). The ask twin
    # reaches it through `failecho-mcp proxy`, the blind twin directly;
    # neither has FailEcho's tools or a FailEcho paragraph in AGENTS.md.
    ("fleet-ocp-ask-n",    "opencode",  "nvidia", True,  OCP_TASKS),
    ("fleet-ocp-blind-n",  "opencode",  "nvidia", False, OCP_TASKS),
    # The fourth arm (2026-09-23): OpenCode with the shipped plugin against
    # OpenCode with nothing. Same tasks as the MCP pair, so the two answer the
    # same question from opposite ends -- ask the model to call a tool, or
    # hook every tool it already calls. The MCP twin chose to call FailEcho
    # 0.19 times a run, and the first run under the strictest instruction we
    # could write used bash thirteen times and the tool zero.
    ("fleet-och-ask-n",    "opencode",  "nvidia", True,  OC_TASKS),
    ("fleet-och-blind-n",  "opencode",  "nvidia", False, OC_TASKS),
    # What a new user of failecho.com gets today (2026-09-19): the same
    # harness as the real-API twins, but the ask twin's advice is read from
    # production, whose evidence is only our one first-party agent's. Reads
    # store nothing; reports still go to the lab.
    ("fleet-prod-ask",     "decorator", "nvidia", True,  MIXED),
    ("fleet-prod-blind",   "decorator", "nvidia", False, MIXED),
    # The third arm (2026-09-22): competent local recovery with no network at
    # all, against the same recovery plus FailEcho. The old control was one
    # retry after 3 s, so "with vs without" only ever answered "better than
    # retrying blindly". These two answer "better than doing it properly".
    ("fleet-local-ask",    "decorator", "mistral", True,  MIXED),
    ("fleet-local-blind",  "decorator", "mistral", False, MIXED),
    # a second pair on the same provider (22 Sep): the arm that answers
    # "better than doing it properly?" was gathering 0.3 runs an hour a side,
    # six days to a readable result. Same provider on purpose -- two pairs
    # differing only in sample size, not in the model's behaviour.
    ("fleet-local-ask-2",  "decorator", "mistral", True,  MIXED),
    ("fleet-local-blind-2", "decorator", "mistral", False, MIXED),
    # The team arm (2026-09-23): a small team in private mode. Two agents share
    # one team token and read only their team's own evidence -- no public
    # network, no rest of the fleet -- against two that read nothing. Both
    # sides recover carefully, like the local arm, so the only difference is
    # the team's own history. Tool calls only, no model: the job a small
    # team's failures come from, and no provider budget.
    ("fleet-team-ask-a",   "decorator", None,     True,  LIMIT_CALLS),
    ("fleet-team-ask-b",   "decorator", None,     True,  LIMIT_CALLS),
    ("fleet-team-blind-a", "decorator", None,     False, LIMIT_CALLS),
    ("fleet-team-blind-b", "decorator", None,     False, LIMIT_CALLS),
]
BUILD_PERSONAS = {"fleet-build-ask", "fleet-build-blind", "fleet-build-ask-n", "fleet-build-blind-n",
                  "fleet-build-ask-x", "fleet-build-blind-x"}
#: Share of each provider's daily budget that only the OpenCode twins may
#: spend. Everything else stops at 85% of the cap. The caps themselves do not
#: move -- they stay at the documented free tiers -- so this costs the light
#: lane a few runs an evening and buys the experiment that is actually being
#: watched its runs at all. Both arms of every pair lose the same runs, so no
#: comparison moves.
OPENCODE_RESERVE = 0.15

def effective_caps(provider: str, path: str) -> tuple[int, int]:
    """The call and token budget this persona may spend today.

    The OpenCode twins get the whole cap; everything else stops at
    ``1 - OPENCODE_RESERVE`` of it.
    """
    limits = PROVIDERS.get(provider, {})
    cap = limits.get("daily_cap", 10 ** 9)
    token_cap = limits.get("daily_token_cap", 10 ** 12)
    if path == "opencode":
        return cap, token_cap
    return int(cap * (1 - OPENCODE_RESERVE)), int(token_cap * (1 - OPENCODE_RESERVE))


OPENCODE_PERSONAS = {"fleet-oc-ask-n", "fleet-oc-blind-n"}
#: When the MCP twin's instruction became a rule rather than a paragraph
#: (see AGENTS_MD_FAILECHO). Everything before this is the polite version,
#: which got 0.12 FailEcho calls a run; the rows below split on it, because
#: a before-and-after in one group is easy to read as one number otherwise.
#: Grades recorded before this are not counted. Grading is computed once, at
#: record time, so a grader bug is frozen into the ledger: between 20:45 and
#: 21:55 on 22 Sep the local arm's right answers were scored wrong because a
#: model writes "LlamaIndex" where the task names run-llama/llama_index. The
#: answers behind those grades were not kept, so they cannot be re-graded --
#: and a rate that silently mixes two graders is worse than a shorter one.
GRADING_SINCE = "2026-09-23T05:17:00"
# Moved twice, each time with its reason. From 21:55 to 03:32 when the zero
# and summary-line fixes went live; from 03:32 to 05:17 when grades began to
# keep the truth they used. The grades between were made by a grader with
# five bugs since fixed ("24 368", a one-subject answer without its name, the
# harness's "(model budget exhausted)", "cannot be found", a value-less line
# under a refusal) and carry no snapshot, so they cannot be redone -- and
# every grade after 05:17 is recomputed by the current grader at report time,
# so this should be the last move.

#: How much of a prose answer the ledger keeps for regrading.
ANSWER_KEEP = 600


def _current_grade(r: dict) -> dict:
    """The grade this run gets from the grader as it is *now*.

    A run that kept its task, its answer and the truth its grade was computed
    against is graded again, so a grader fix applies to every such run instead
    of only to the ones after it. Anything else -- OpenCode results, whose
    files are not kept, and runs from before 23 Sep -- keeps its frozen grade.
    """
    g = r.get("graded") or {}
    answer, task, truths = r.get("answer"), r.get("task"), g.get("truth")
    if (not g or not truths or not isinstance(task, str) or not isinstance(answer, str)
            or r.get("opencode") or r.get("build") or len(answer) >= ANSWER_KEEP):
        return g
    return grade(task, answer, bool(g.get("answered")), truths=truths)

MCP_RULES_SINCE = "2026-09-22T18:00:00"
WRAPPED_PERSONAS = {"fleet-wrap-ask", "fleet-wrap-blind"}
OCPROXY_PERSONAS = {"fleet-ocp-ask-n", "fleet-ocp-blind-n"}

#: OpenCode with the plugin, which reports and advises around every tool the
#: agent runs without the model deciding anything.
OCHOOK_PERSONAS = {"fleet-och-ask-n", "fleet-och-blind-n"}
PROD_ADVICE_PERSONAS = {"fleet-prod-ask", "fleet-prod-blind"}
#: Both sides recover like a careful engineer; only the ask twin also asks the
#: network. The blind twin here is *not* the naive control.
LOCAL_PERSONAS = {"fleet-local-ask", "fleet-local-blind", "fleet-local-ask-2", "fleet-local-blind-2"}

#: A small team in private mode: see the persona rows. The askers share one
#: team token and read only the team's own evidence.
TEAM_PERSONAS = {"fleet-team-ask-a", "fleet-team-ask-b", "fleet-team-blind-a", "fleet-team-blind-b"}
TEAM_ASKERS = {"fleet-team-ask-a", "fleet-team-ask-b"}


def team_token() -> str:
    """The lab team's secret, made once and kept beside the fleet's state.

    Not in the code: the repository is public, and a token anybody can read is
    not a team's. It only ever goes to the lab endpoint.
    """
    path = os.path.join(STATE_DIR, "team-token")
    try:
        with open(path, encoding="utf-8") as fh:
            value = fh.read().strip()
        if len(value) >= 16:
            return value
    except OSError:
        pass
    import secrets
    value = "lab-team-" + secrets.token_urlsafe(24)
    os.makedirs(STATE_DIR, exist_ok=True)
    handle = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        fh.write(value)
    os.replace(path + ".tmp", path)
    return value


def team_view(answer: dict | None) -> dict | None:
    """What a team sees when it has only itself: the team's own evidence in
    place of the network's. The fingerprint stays -- it is the same shape."""
    if not isinstance(answer, dict):
        return answer
    team = answer.get("team_evidence") or {}
    return {"fingerprint": answer.get("fingerprint"), "known": bool(team.get("observations")),
            "recommendation": team.get("recommendation"),
            "recovery_actions": team.get("recovery_actions") or [], "team": True}
#: The one production URL the fleet may call: the read path, which stores
#: nothing. Sent with X-Reporter-Kind: demo, the label the server accepts
#: only as a downgrade, so these reads stay out of its usage counters. The
#: fleet never holds the operator token (assert_lab_only), and never writes
#: to production.
PROD_READ_URL = "https://failecho.com/v1/query"
#: The model OpenCode is pointed at, per provider: the strongest agentic one
#: each tier serves with tool calls.
OPENCODE_MODELS = {"nvidia": "nvidia/nemotron-3-super-120b-a12b", "mistral": "codestral-latest",
                   "xkiro": "qwen/qwen3.6-27b:free", "zen": "nemotron-3-ultra-free"}
EXPLORER_PERSONAS = {"fleet-explore-a", "fleet-explore-b", "fleet-explore-c", "fleet-explore-d", "fleet-explore-e",
                     "fleet-explore-f", "fleet-explore-a2"}

#: Ask/blind twins by reporter. The round-robin ran every ask twin before
#: its blind twin, two minutes apart, and on GitHub's hourly budget the twin
#: that runs second meets the 403s the first one used up the budget for:
#: gh-ask 0 failures, gh-blind 47, identical work. Since 2026-09-17 06:30 UTC
#: twins swap order every cycle, and the proof table counts only runs from
#: then on.
TWINS = [("fleet-decor-ask-a", "fleet-decor-blind-a"), ("fleet-decor-ask-b", "fleet-decor-blind-b"),
         ("fleet-auto-ask-a", "fleet-auto-blind-a"), ("fleet-auto-ask-b", "fleet-auto-blind-b"),
         ("fleet-mcp-ask", "fleet-mcp-blind"), ("fleet-cron-a", "fleet-cron-b"),
         ("fleet-test-ask", "fleet-test-blind"), ("fleet-gh-ask", "fleet-gh-blind"),
         ("fleet-build-ask", "fleet-build-blind"), ("fleet-limits-ask", "fleet-limits-blind"),
         ("fleet-decor-ask-c", "fleet-decor-blind-c"), ("fleet-build-ask-n", "fleet-build-blind-n"),
         ("fleet-decor-ask-d", "fleet-decor-blind-d"), ("fleet-decor-ask-e", "fleet-decor-blind-e"),
         ("fleet-build-ask-x", "fleet-build-blind-x"), ("fleet-oc-ask-n", "fleet-oc-blind-n"),
         ("fleet-wrap-ask", "fleet-wrap-blind"), ("fleet-ocp-ask-n", "fleet-ocp-blind-n"),
         ("fleet-och-ask-n", "fleet-och-blind-n"),
         ("fleet-prod-ask", "fleet-prod-blind"), ("fleet-local-ask", "fleet-local-blind"),
         ("fleet-local-ask-2", "fleet-local-blind-2"),
         ("fleet-team-ask-a", "fleet-team-blind-a"), ("fleet-team-ask-b", "fleet-team-blind-b")]
FAIR_ORDER_SINCE = "2026-09-17T06:30:00"


#: Two lanes, two timers (since 2026-09-18 13:30 UTC). A builder or an
#: OpenCode run holds the slot for 30-240 s; a cron or model persona for
#: 2-10 s. In one queue the 31 light personas waited behind the 8 VM ones
#: and a full cycle was 76 minutes. Each lane round-robins its own list;
#: twins share a path, so they are always in the same lane.
LANES = ("light", "vm")


def lane_of(persona: tuple) -> str:
    return "vm" if persona[1] in ("builder", "opencode") else "light"


def lane_personas(lane: str | None) -> list[int]:
    """Indexes into PERSONAS this lane runs; None is the old single queue."""
    return [i for i, p in enumerate(PERSONAS) if lane is None or lane_of(p) == lane]


def persona_index(position: int, lane: str | None = None) -> int:
    """Which persona runs at this position of the lane's round-robin: on odd
    cycles the twins trade places, so neither side always runs second."""
    members = lane_personas(lane)
    n = len(members)
    idx, cycle = members[position % n], position // n
    if cycle % 2 == 0:
        return idx
    names = [p[0] for p in PERSONAS]
    swap = {}
    for a, b in TWINS:
        if a in names and b in names:
            swap[names.index(a)] = names.index(b)
            swap[names.index(b)] = names.index(a)
    return swap.get(idx, idx)

#: What an agent can do about a tool call failing, beyond the default.
#: Explorers try these in order, skipping what the network has evidence for.
#: conditional_request is out (2026-09-18): GitHub answers a conditional GET
#: with 403, not 304, once the IP is over its limit -- probed by hand, and
#: 0 of 2 in the lab. It prevents a limit; it does not recover from one.
TOOL_ACTIONS = {
    "rate_limit": ["backoff", "wait_until_reset"],
    # GitHub's secondary limit answers a plain 403 Forbidden, which the
    # classifier files as auth_error; from an unauthenticated agent it is a
    # limit all the same
    "auth_error": ["wait_until_reset"],
    "server_error": ["retry", "backoff"],
    "timeout": ["retry"],
    "connection_error": ["retry"],
}
TEST_PERSONAS = {"fleet-test-ask", "fleet-test-blind"}


def log(msg: str) -> None:
    print(f"[fleet] {msg}", flush=True)


def _daily_quota(exc: BaseException) -> bool:
    """Did the provider say the *daily* budget is gone (groq's TPD, Gemini's
    quota)? Then a minute's wait is pointless."""
    body = (getattr(exc, "failecho_body", "") or "").lower()
    return "per day" in body or "tpd" in body or "rpd" in body or "exceeded your current quota" in body


def _seconds(value: str) -> float:
    """A Retry-After or ratelimit-reset header as seconds: '7', '2.5s', '1m3s'."""
    value = str(value).strip().lower()
    try:
        return float(value)
    except ValueError:
        pass
    total, num = 0.0, ""
    for ch in value:
        if ch.isdigit() or ch == ".":
            num += ch
        elif ch in "hms" and num:
            total += float(num) * {"h": 3600, "m": 60, "s": 1}[ch]
            num = ""
    return total


# ---------------------------------------------------------------------------
# safety: the lab and only the lab
# ---------------------------------------------------------------------------


def assert_lab_only() -> None:
    host = (urllib.parse.urlparse(LAB_ENDPOINT).hostname or "").lower()
    if not LAB_ENDPOINT or not host:
        raise SystemExit("fleet: FAILECHO_ENDPOINT is not set; refusing to guess")
    if host in PRODUCTION_HOSTS or host.endswith(".failecho.com") and host not in ("lab.failecho.com",):
        raise SystemExit(f"fleet: endpoint {host} is production; the fleet runs against the lab only")
    if os.environ.get("FIN_FIRST_PARTY_TOKEN") or os.environ.get("FAILECHO_OPERATOR_TOKEN"):
        raise SystemExit("fleet: an operator token is in the environment; the lab does not use one "
                         "and its presence means this is not the lab's environment")


# ---------------------------------------------------------------------------
# the tools (the same real calls the first-party agent makes)
# ---------------------------------------------------------------------------


#: Set by Run.call for one retry when the recovery action is
#: conditional_request: send If-None-Match with the ETag from the last good
#: answer. GitHub serves 304 to a matching ETag and a 304 does not count
#: against the rate limit -- the fix almost no agent knows. The ETag cache
#: is per persona, on disk, so a later run has something to condition on.
CONDITIONAL: contextvars.ContextVar[bool] = contextvars.ContextVar("conditional", default=False)
ETAGS: contextvars.ContextVar[dict] = contextvars.ContextVar("etags", default={})


def _get_json(url: str):
    headers = {"User-Agent": UA, "Accept": "application/json"}
    cache = ETAGS.get()
    cached = cache.get(url)
    if CONDITIONAL.get() and cached:
        headers["If-None-Match"] = cached["etag"]
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.load(r)
            etag = r.headers.get("ETag")
            if etag and isinstance(cache, dict):
                cache[url] = {"etag": etag, "body": body}
            return body
    except urllib.error.HTTPError as e:
        if e.code == 304 and cached:
            return cached["body"]
        raise


def pypi_latest_version(package: str) -> dict:
    d = _get_json(f"https://pypi.org/pypi/{urllib.parse.quote(package)}/json")
    return {"package": package, "version": d["info"]["version"]}


def npm_latest_version(package: str) -> dict:
    d = _get_json(f"https://registry.npmjs.org/{urllib.parse.quote(package, safe='@/')}")
    return {"package": package, "version": d["dist-tags"]["latest"]}


def github_repo(owner: str, repo: str) -> dict:
    d = _get_json(f"https://api.github.com/repos/{owner}/{repo}")
    return {"full_name": d["full_name"], "stars": d["stargazers_count"], "open_issues": d["open_issues_count"]}


def github_latest_release(owner: str, repo: str) -> dict:
    d = _get_json(f"https://api.github.com/repos/{owner}/{repo}/releases/latest")
    return {"tag": d["tag_name"]}


# Real services with real, documented limits that honest use at fleet scale
# crosses: GitHub search (10/min unauthenticated), crates.io (one request a
# second, 429 above), Stack Exchange (300/day per IP, then a 400
# "throttle_violation"). Nothing synthetic: every failure is a policy being
# enforced on us, the way it would be on any agent behind one address.
def github_search(query: str) -> dict:
    d = _get_json(f"https://api.github.com/search/repositories?q={urllib.parse.quote(query)}&per_page=3")
    return {"total": d["total_count"], "top": [i["full_name"] for i in d["items"][:3]]}


def crates_io_latest(crate: str) -> dict:
    d = _get_json(f"https://crates.io/api/v1/crates/{urllib.parse.quote(crate)}")
    return {"crate": crate, "version": d["crate"]["max_stable_version"] or d["crate"]["max_version"]}


def stackexchange_questions(tag: str) -> dict:
    d = _get_json("https://api.stackexchange.com/2.3/questions?order=desc&sort=activity&pagesize=3"
                  f"&site=stackoverflow&tagged={urllib.parse.quote(tag)}")
    return {"tag": tag, "titles": [q["title"] for q in d["items"][:3]], "quota_remaining": d.get("quota_remaining")}


# httpbingo.org exists to return whatever status you ask for. Calling it is its
# intended use. These are the failure classes the real targets rarely produce,
# and one write that "succeeds" forever while persisting nothing -- the
# fake-success case from the r/AI_Agents thread, as a live service.
def _bingo(path: str, method: str = "GET", timeout: int = 20) -> dict:
    req = urllib.request.Request(f"https://httpbingo.org{path}", method=method,
                                 data=b"{}" if method == "POST" else None,
                                 headers={"User-Agent": UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return {"status": r.status}


def flaky_read() -> dict:            # 503 one time in three; retry genuinely helps
    return _bingo("/status/200,200,503")


def throttled_read() -> dict:        # 429 half the time; backoff genuinely helps
    return _bingo("/status/200,429")


def always_broken() -> dict:         # 503 every time; nothing helps, and the network should learn that
    return _bingo("/status/503")


def slow_read() -> dict:             # 10s server, 8s client: a timeout every time
    return _bingo("/delay/10", timeout=8)


def echo_write() -> dict:            # 200 forever, persists nothing: a write that cannot be verified
    return _bingo("/post", method="POST")


TOOLS = {"pypi_latest_version": pypi_latest_version, "npm_latest_version": npm_latest_version,
         "github_repo": github_repo, "github_latest_release": github_latest_release,
         "github_search": github_search, "crates_io_latest": crates_io_latest,
         "stackexchange_questions": stackexchange_questions,
         "flaky_read": flaky_read, "throttled_read": throttled_read, "always_broken": always_broken,
         "slow_read": slow_read, "echo_write": echo_write}
TOOL_SERVICE = {"pypi_latest_version": "pypi.org", "npm_latest_version": "registry.npmjs.org",
                "github_repo": "api.github.com", "github_latest_release": "api.github.com",
                "github_search": "api.github.com", "crates_io_latest": "crates.io",
                "stackexchange_questions": "api.stackexchange.com",
                "flaky_read": "httpbingo.org", "throttled_read": "httpbingo.org", "always_broken": "httpbingo.org",
                "slow_read": "httpbingo.org", "echo_write": "httpbingo.org"}
TOOL_MUTATES = {"echo_write": True}   # everything else is a read
TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "pypi_latest_version", "description": "Latest version of a PyPI package.",
     "parameters": {"type": "object", "properties": {"package": {"type": "string"}}, "required": ["package"]}}},
    {"type": "function", "function": {"name": "npm_latest_version", "description": "Latest version of an npm package.",
     "parameters": {"type": "object", "properties": {"package": {"type": "string"}}, "required": ["package"]}}},
    {"type": "function", "function": {"name": "github_repo", "description": "Stars and open issues of a GitHub repository.",
     "parameters": {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}}, "required": ["owner", "repo"]}}},
    {"type": "function", "function": {"name": "github_latest_release", "description": "Latest release tag of a GitHub repository.",
     "parameters": {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}}, "required": ["owner", "repo"]}}},
]


# ---------------------------------------------------------------------------
# one persona, one run
# ---------------------------------------------------------------------------


def completion(resp):
    """A chat completion, or an error a recovery path can handle. Providers
    sometimes answer 200 with an error object and no ``choices``
    (2026-09-18 21:53, a light-lane run); indexing into that killed the run."""
    if isinstance(resp, dict) and resp.get("choices"):
        return resp
    err = resp.get("error") if isinstance(resp, dict) else None
    msg = (err.get("message") if isinstance(err, dict) else err) or "no choices in response"
    raise ValueError(f"provider returned no completion: {str(msg)[:120]}")


class Run:
    """Everything one run needs to know about itself, and the scoreboard row it
    leaves behind."""

    def __init__(self, reporter: str, path: str, provider: str | None, asks: bool):
        self.reporter, self.path, self.provider, self.asks = reporter, path, provider, asks
        self.fe = FailEcho(endpoint=LAB_ENDPOINT, reporter_id=reporter,
                           team_token=team_token() if reporter in TEAM_ASKERS else None)
        self.tool_calls = 0
        self.model_calls = 0
        self.model: str | None = None
        self.failures: list[dict] = []   # one per failure: shape, asked, recommended, attempts, recovered
        self.tools = {}
        for name, fn in TOOLS.items():
            if path in ("decorator", "wrapped"):
                self.tools[name] = self.fe.watch(service=TOOL_SERVICE[name], operation=name,
                                                 mutates=TOOL_MUTATES.get(name, False))(fn)
            else:
                self.tools[name] = fn  # auto and mcp report by other means
        if path == "wrapped":
            self.fe.advise = asks
        if path == "auto":
            from failecho_autoreport import auto
            auto._fe = self.fe
            auto.enable()
        #: consecutive provider failures per model, for the local arm's circuit
        #: breaker. Per run: a fresh agent does not inherit a broken circuit.
        self._consecutive: dict[str, int] = {}
        self.build: dict | None = None   # the builder's ledger, when this is one
        self.opencode: dict | None = None   # the OpenCode run's ledger, when this is one
        # Every cost a run pays, so ask and blind can be compared on more than
        # attempts: tokens the provider billed, time spent asking the network,
        # time spent waiting on advice, and whether the task got done.
        self.tokens_prompt = 0
        self.tokens_completion = 0
        self.asks_made = 0
        self.ask_seconds = 0.0
        self.wait_seconds = 0.0
        self.completed: bool | None = None
        #: The model calling the very same tool with the very same arguments
        #: right after the network said skip: a turn, and its tokens, wasted
        #: because the advice did not reach the model in a form it acted on.
        self.last_skip: tuple[str, str] | None = None
        self.model_retries_after_skip = 0
        #: Set when the provider says its *daily* budget is gone. The scheduler
        #: then skips this provider's personas for QUOTA_PROBE_SECONDS instead of
        #: spending two-minute slots on 429s (a third of an afternoon's slots,
        #: on the first day).
        self.provider_dead_today = False
        # this persona's ETag cache (URL -> etag, body), for conditional requests
        self._etag_path = os.path.join(STATE_DIR, f"etags-{reporter}.json")
        try:
            with open(self._etag_path, encoding="utf-8") as fh:
                self.etags = json.load(fh)
        except (OSError, ValueError):
            self.etags = {}
        ETAGS.set(self.etags)

    def save_etags(self) -> None:
        """Keep the last 50 ETags whose cached answer is small. The cache held
        whole response bodies, and an npm package document is megabytes: by
        18 Sep the files were 21 MB each, read and parsed on every run in a
        lane capped at 400 MB. conditional_request left the explorers' list
        that morning; the small cache is kept for anyone recommending it."""
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            keep = [(u, e) for u, e in self.etags.items() if len(json.dumps(e.get("body"), default=str)) <= ETAG_BODY_MAX]
            with open(self._etag_path, "w", encoding="utf-8") as fh:
                json.dump(dict(keep[-50:]), fh)
        except (OSError, TypeError, ValueError, AttributeError):
            pass

    # -- asking and reporting through the three paths ------------------------

    def ask(self, service: str, operation: str, error_type: str, code: str | None) -> dict | None:
        """One question to the network, timed: asking is the product's cost."""
        started = time.monotonic()
        self.asks_made += 1
        try:
            return self._ask(service, operation, error_type, code)
        finally:
            self.ask_seconds += time.monotonic() - started

    def _ask(self, service: str, operation: str, error_type: str, code: str | None) -> dict | None:
        if self.path == "mcp":
            return self._mcp("check_tool_failure", {"service": service, "operation": operation,
                                                    "error_type": error_type, "error_code": code,
                                                    "reporter_id": self.reporter})
        body = {"service": service, "operation": operation, "error_type": error_type}
        if code:
            body["error_code"] = code
        headers = {"Content-Type": "application/json", "User-Agent": UA, "X-Reporter-ID": self.reporter}
        url = f"{LAB_ENDPOINT}/v1/query"
        if self.reporter in PROD_ADVICE_PERSONAS:
            url = PROD_READ_URL
            headers["X-Reporter-Kind"] = "demo"
        team = self.reporter in TEAM_ASKERS
        if team:
            headers["X-FailEcho-Team"] = team_token()
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                answer = json.load(r)
        except Exception:
            return None
        return team_view(answer) if team else answer

    def _count_tokens(self, resp: dict) -> dict:
        usage = resp.get("usage") if isinstance(resp, dict) else None
        if isinstance(usage, dict):
            self.tokens_prompt += int(usage.get("prompt_tokens") or 0)
            self.tokens_completion += int(usage.get("completion_tokens") or 0)
        return resp

    def _sleep(self, seconds: float) -> None:
        self.wait_seconds += seconds
        time.sleep(seconds)

    def _mcp(self, tool: str, args: dict) -> dict | None:
        """One tools/call against the lab's MCP endpoint. Stateless HTTP, so
        no session dance: initialize is not required for a single call."""
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": tool, "arguments": {k: v for k, v in args.items() if v is not None}}}
        req = urllib.request.Request(f"{LAB_ENDPOINT}/mcp", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Accept": "application/json, text/event-stream", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.load(r)
            text = d["result"]["content"][0]["text"]
            return json.loads(text)
        except Exception:
            return None

    def report_failure(self, service, operation, exc):
        if self.path == "mcp":
            et, code = classify(exc)
            self._mcp("report_tool_failure", {"service": service, "operation": operation,
                                              "error_type": et, "error_code": code, "reporter_id": self.reporter})
        # decorator and auto paths report through the wrapper automatically

    def report_success(self, service, operation):
        if self.path == "mcp":
            self._mcp("report_tool_success", {"service": service, "operation": operation, "reporter_id": self.reporter})

    def report_recovery(self, service, operation, action, ok, fingerprint=None):
        if self.path == "mcp" and fingerprint:
            self._mcp("report_recovery_outcome", {"fingerprint": fingerprint, "action": action,
                                                  "successful": ok, "reporter_id": self.reporter})
        else:
            self.fe.recovered(service, operation, action, ok)

    # -- the ask-then-recover discipline --------------------------------------

    def call(self, name: str, args: dict, fn=None, service: str | None = None) -> tuple[str, bool]:
        fn = fn or self.tools[name]
        service = service or TOOL_SERVICE[name]
        self.tool_calls += 1
        started = time.monotonic()
        try:
            out = fn(**args)
            self.report_success(service, name)
            return json.dumps(out), True
        except Exception as exc:  # noqa: BLE001 - every failure is data
            et, code = classify(exc)
            self.report_failure(service, name, exc)
            rec = {"service": service, "operation": name, "error_type": et, "error_code": code,
                   "asked": False, "recommended": None, "attempts": 1, "recovered": False, "seconds": 0.0}
            self.failures.append(rec)
            if self.path == "wrapped":
                return self._wrapped_error(exc, et, code, rec, started)
            fingerprint = None
            action = {"rate_limit": "backoff", "server_error": "retry", "timeout": "retry",
                      "connection_error": "retry"}.get(et)
            if self.reporter in LOCAL_PERSONAS or self.reporter in TEAM_PERSONAS:
                # The local arm recovers like a careful engineer at the tool
                # level too, not only when a model provider fails -- Mistral
                # failed 0 times in its first 32 runs, so without this the arm
                # was the ordinary comparison wearing a different name
                # (22 Sep). The difference that bites here: GitHub sends
                # x-ratelimit-reset, so a competent client waits for the reset
                # instead of sleeping three seconds and trying again.
                plan = [a for a in local_plan(et, code, exc, 0) if a != "switch_model"]
                action = plan[0] if plan else None
                rec["local_plan"] = plan
            advice = {}
            if self.asks:
                rec["asked"] = True
                advice = self.ask(service, name, et, code) or {}
                fingerprint = advice.get("fingerprint")
                r = advice.get("recommendation") or {}
                if r.get("action"):
                    rec["recommended"] = r["action"]
                    action = r["action"] if r["action"] in KNOWN_ACTIONS else action
                    if r.get("decaying"):
                        rec["recommended"] += " (decaying)"
                elif self.reporter in EXPLORER_PERSONAS:
                    chosen = explore(TOOL_ACTIONS.get(et, []), advice.get("recovery_actions") or [])
                    if chosen:
                        action = chosen
                        rec["explored"] = action
            if action == "skip":
                # the network says nothing tried recently has worked: an asker
                # spends no second attempt (since 2026-09-17 04:40 UTC; before
                # that askers retried anyway and tied with blind)
                rec["skipped"] = True
                rec["seconds"] = round(time.monotonic() - started, 2)
                # The same shape blind gets, plus one flag the model can act
                # on. "skipped" alone left the model calling the same tool
                # again -- a whole extra turn; "retry_pointless" says why.
                self.last_skip = (name, json.dumps(args, sort_keys=True))
                return json.dumps({"error": et, "code": code, "retry_pointless": True}), False
            if action is None:
                rec["seconds"] = round(time.monotonic() - started, 2)
                return json.dumps({"error": et, "code": code}), False
            token = None
            if action == "backoff":
                self._sleep(3)
            elif action == "wait_until_reset":
                headers = getattr(exc, "headers", None)
                wait = 0.0
                reset = headers.get("x-ratelimit-reset") if headers else None
                if reset and str(reset).isdigit():
                    wait = float(reset) - time.time()
                else:
                    for h in ("retry-after", "x-ratelimit-reset-tokens"):
                        if headers and headers.get(h):
                            wait = _seconds(headers.get(h)); break
                self._sleep(min(max(wait, 1.0), 30.0))
            elif action == "conditional_request":
                token = CONDITIONAL.set(True)
            rec["attempts"] += 1
            self.tool_calls += 1
            try:
                out = fn(**args)
            except Exception:  # noqa: BLE001
                self.report_recovery(service, name, action, False, fingerprint)
                rec["seconds"] = round(time.monotonic() - started, 2)
                return json.dumps({"error": et, "retried": True, "recovered": False}), False
            finally:
                if token is not None:
                    CONDITIONAL.reset(token)
            rec["recovered"] = True
            rec["seconds"] = round(time.monotonic() - started, 2)
            self.report_recovery(service, name, action, True, fingerprint)
            return json.dumps({"result": out, "recovered": True}), True

    def _wrapped_error(self, exc, et, code, rec, started) -> tuple[str, bool]:
        """What a model sees when a wrapped tool fails: the error, and for
        the advised twin the wrapper's one line. No retry here; the model
        may call the tool again, which is what a real agent loop does."""
        answer = getattr(exc, "failecho", None)
        out = {"error": et, "code": code}
        if answer is not None:
            rec["asked"] = True
            self.asks_made += 1
            rec["recommended"] = ((answer.get("recommendation") or {}).get("action")
                                  if isinstance(answer, dict) else None)
            line = FailEcho.advice_text(answer)
            if line:
                out["failecho"] = line
        rec["seconds"] = round(time.monotonic() - started, 2)
        return json.dumps(out), False

    # -- a provider failing: ask, explore, or give up ---------------------------

    def recover_provider(self, p: dict, exc: BaseException, chat, state: dict):
        """One recovery attempt after a model provider failed, or None.

        Blind, and an asker the network has nothing for: one plain retry
        after three seconds -- the control, what an agent without the
        network does (since PROVIDER_CONTROL_SINCE; before that they gave
        up at once). Ask: follow the network's recommendation if it is one
        of the actions this loop knows; `skip` means give up. Explore: if
        the network recommends, follow it; otherwise try the action the
        network still lacks evidence for (see explore()) and report the
        outcome either way. Explorers are how the evidence askers inherit
        gets made.

        A provider is marked out of daily quota only when a model switch
        also hit a wall, or there was no model left to switch to. A blind
        retry into the same wall says nothing about the provider's other
        models, and marking on it would skip the askers too; a switch that
        worked means the provider still serves. The personas keep running
        -- the difference between the cohorts under a real quota is the
        point, and a 429 costs a second.
        """
        host = p["host"]
        et, code = classify(exc)
        model_now = state.get("model", "")
        self._consecutive[model_now] = self._consecutive.get(model_now, 0) + 1
        rec = {"service": host, "operation": "chat.completions", "error_type": et, "error_code": code,
               "asked": False, "recommended": None, "attempts": 1, "recovered": False}
        self.failures.append(rec)
        quota = et == "rate_limit" and _daily_quota(exc)
        fingerprint = None
        action = None
        if self.asks and self.path != "wrapped":
            rec["asked"] = True
            advice = self.ask(host, "chat.completions", et, code) or {}
            fingerprint = advice.get("fingerprint")
            recommended = (advice.get("recommendation") or {}).get("action")
            if recommended:
                rec["recommended"] = recommended
            if recommended and recommended != "skip" and recommended in KNOWN_ACTIONS:
                action = recommended
            if action is None and self.reporter in EXPLORER_PERSONAS:
                # a skip verdict is built from what has been tried; an
                # explorer's job is what has not, so it explores past a skip
                # and honours it only once every candidate has been ruled on
                action = explore(PROVIDER_ACTIONS.get(et, []), advice.get("recovery_actions") or [])
                rec["explored"] = action
            if action is None and recommended == "skip":
                rec["skipped"] = True
                if quota:
                    self.provider_dead_today = True
                return None
        local = self.reporter in LOCAL_PERSONAS
        if local and action is None:
            # no network answer (or the blind twin, which never asks): do what
            # a careful engineer would, not a bare retry
            plan = local_plan(et, code, exc, self._consecutive.get(state.get("model", ""), 0))
            if not plan:
                rec["skipped"] = True
                return None
            action = plan[0]
            rec["local_plan"] = plan
        if action is None:
            action = "retry"   # the naive control
        # -- apply the action ---------------------------------------------
        if action in ("backoff", "retry"):
            self._sleep(3)
        elif action == "wait_until_reset":
            headers = getattr(exc, "headers", None)
            wait = 0.0
            for name in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
                value = headers.get(name) if headers else None
                if value:
                    wait = _seconds(value)
                    break
            self._sleep(min(max(wait, 1.0), 30.0))
        elif action == "switch_model":
            alts = [m for m in p.get("alt_models", []) if m != state["model"]]
            if not alts:
                if quota:
                    self.provider_dead_today = True
                return None
            state["model"] = alts[0]
            self.model = f"{self.model}->{alts[0]}"
        elif action == "retry_without_tool_choice":
            state["tools"] = False
        rec["attempts"] += 1
        self.model_calls += 1
        try:
            resp = chat()
            self._consecutive[state.get("model", "")] = 0
        except Exception as again:  # noqa: BLE001
            self.report_recovery(host, "chat.completions", action, False, fingerprint)
            # only a failed *switch* proves the provider has nothing left; a
            # blind retry into the same wall says nothing about its other
            # models, and marking on it would skip the askers too
            if action == "switch_model" and (quota or (classify(again)[0] == "rate_limit" and _daily_quota(again))):
                self.provider_dead_today = True
            return None
        rec["recovered"] = True
        self.report_recovery(host, "chat.completions", action, True, fingerprint)
        return resp

    # -- the two kinds of workload ---------------------------------------------

    def run_cron(self, calls: list) -> str:
        for kind, arg in calls:
            if kind == "tool":
                self.call(arg, {})
            elif kind == "pypi":
                self.call("pypi_latest_version", {"package": arg})
            elif kind == "npm":
                self.call("npm_latest_version", {"package": arg})
            elif kind == "github":
                o, r = arg.split("/"); self.call("github_repo", {"owner": o, "repo": r})
            elif kind == "github_release":
                o, r = arg.split("/"); self.call("github_latest_release", {"owner": o, "repo": r})
            elif kind == "crate":
                self.call("crates_io_latest", {"crate": arg})
            elif kind == "so":
                self.call("stackexchange_questions", {"tag": arg})
            elif kind == "gh_search":
                self.call("github_search", {"query": arg})
        return f"cron: {len(calls)} calls"

    def run_model(self, task: str, run_index: int = 0, used_today: int = 0) -> str:
        p = PROVIDERS[self.provider]
        if not p["key"]:
            return "(no provider key)"
        if used_today >= p.get("daily_cap", 10**9):
            return f"(provider {self.provider} daily cap reached; run skipped)"
        model = p["models"][run_index % len(p["models"])]
        self.model = model
        messages = [{"role": "system", "content": "Use the tools to answer with real data. If a tool errors you may "
                                                   "try once more, then answer with what you have. Under 60 words."},
                    {"role": "user", "content": task}]
        state = {"model": model, "tools": True}

        def chat():
            body = {"model": state["model"], "messages": messages, "max_tokens": 500}
            if state["tools"]:
                body.update(tools=TOOL_SCHEMAS, tool_choice="auto")
            req = urllib.request.Request(p["url"], data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "User-Agent": UA,
                                                  "Authorization": f"Bearer {p['key']}", **p.get("headers", {})})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return self._count_tokens(completion(json.load(r)))
            except urllib.error.HTTPError as e:
                # a provider rejecting our request is data, and if it is OUR
                # request shape that is wrong, the journal is where we find out
                body = e.read()[:400].decode(errors="ignore")
                e.failecho_body = body
                log(f"  provider {p['host']} HTTP {e.code}: {body[:200]!r}")
                raise

        # The auto path's urllib patch already observes this call by route.
        # Wrapping it again would report every provider call twice, and the
        # network cannot tell a double count from two agents agreeing.
        if self.path != "auto":
            chat = self.fe.watch(service=p["host"], operation="chat.completions", mutates=False)(chat)

        for _ in range(6):
            self.model_calls += 1
            try:
                resp = chat()
            except Exception as exc:  # noqa: BLE001
                resp = self.recover_provider(p, exc, chat, state)
                if resp is None:
                    et, _ = classify(exc)
                    return f"(provider failed: {et})"
            msg = resp["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            if not calls:
                return (msg.get("content") or "").strip()[:200]
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for c in calls:
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                except ValueError:
                    args = {}
                name = c["function"]["name"]
                if self.last_skip == (name, json.dumps(args, sort_keys=True)):
                    self.model_retries_after_skip += 1
                self.last_skip = None
                result, _ = self.call(name, args) if name in self.tools else (json.dumps({"error": "unknown tool"}), False)
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
        return "(model budget exhausted)"

    def run_opencode(self, task: tuple[str, str], run_index: int = 0, used_today: int = 0) -> str:
        """A headless OpenCode run in a fresh VM (see failecho_fleet.opencode).
        Nothing here asks or reports on the agent's behalf: the ask twin has
        the MCP server in its config and the network sees what OpenCode
        sends. Steps count as model calls, OpenCode's token counts as ours,
        its FailEcho tool calls as asks."""
        p = PROVIDERS[self.provider]
        if not p["key"]:
            return "(no provider key)"
        if used_today >= p.get("daily_cap", 10**9):
            return f"(provider {self.provider} daily cap reached; run skipped)"
        model = OPENCODE_MODELS[self.provider]
        self.model = f"opencode:{model}"
        proxied = self.reporter in OCPROXY_PERSONAS
        hooked = self.reporter in OCHOOK_PERSONAS
        out = run_opencode(reporter=self.reporter, asks=self.asks, task=task, provider=self.provider, model=model,
                           key=p["key"], lab_public_url=LAB_PUBLIC_URL, timeout=OPENCODE_TIMEOUT, proxy=proxied,
                           hook=hooked)
        self.opencode = out
        self.model_calls += int(out.get("steps") or 0)
        self.tool_calls += int(out.get("tool_calls") or 0)
        # the proxy pair never calls a FailEcho tool; what it got is advice in
        # a tool's error, counted as the tool outputs that carried the line
        self.asks_made += int(out.get("advice_seen" if (proxied or hooked) else "failecho_calls") or 0)
        self.tokens_prompt += int(out.get("tokens_in") or 0)
        self.tokens_completion += int(out.get("tokens_out") or 0)
        if out.get("error") and not out.get("completed"):
            et, code = classify(RuntimeError(str(out["error"])))
            self.failures.append({"service": p["host"], "operation": "opencode", "error_type": et, "error_code": code,
                                  "asked": False, "recommended": None, "attempts": 1, "recovered": False})
        return out.get("answer") or ("done" if out.get("completed") else f"(opencode: {out.get('error') or 'no result'})")

    def run_build(self, task: str, run_index: int = 0, used_today: int = 0) -> str:
        """A builder run: the model writes code and the sandbox runs it.

        Same provider loop as run_model, different tools. run_python and
        resolve_python_deps go to the VM through the Builder; fetch_doc is a
        host read through call(), so it asks or does not exactly as the other
        personas' tools do. The Builder's ledger (VM runs, local failures,
        shared failures) lands on self.build for the scoreboard."""
        p = PROVIDERS[self.provider]
        if not p["key"]:
            return "(no provider key)"
        if used_today >= p.get("daily_cap", 10**9):
            return f"(provider {self.provider} daily cap reached; run skipped)"
        model = p["models"][run_index % len(p["models"])]
        self.model = model
        messages = [{"role": "system", "content": BUILDER_PROMPT}, {"role": "user", "content": task}]
        state = {"model": model}

        def chat():
            body = {"model": state["model"], "messages": messages, "tools": BUILDER_SCHEMAS, "tool_choice": "auto",
                    "max_tokens": 2500}
            req = urllib.request.Request(p["url"], data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "User-Agent": UA,
                                                  "Authorization": f"Bearer {p['key']}", **p.get("headers", {})})
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    return self._count_tokens(completion(json.load(r)))
            except urllib.error.HTTPError as e:
                body = e.read()[:400].decode(errors="ignore")
                e.failecho_body = body
                log(f"  provider {p['host']} HTTP {e.code}: {body[:200]!r}")
                raise

        chat = self.fe.watch(service=p["host"], operation="chat.completions", mutates=False)(chat)

        tried: set[str] = set()
        with Builder(self.reporter, LAB_PUBLIC_URL, self.fe) as b:
            answer = "(model budget exhausted)"
            for _ in range(10):
                # A builder's context grows with every program and traceback,
                # and groq's free tier is 8,000 tokens a minute: the second or
                # third call of a longer task meets a 429 as a matter of
                # course. Both twins wait the minute out, up to three times --
                # that is what any agent on that tier does -- and the wait is
                # counted. The tool results are trimmed for the same reason.
                resp = None
                for attempt in range(4):
                    self.model_calls += 1
                    try:
                        resp = chat()
                        break
                    except Exception as exc:  # noqa: BLE001
                        et, code = classify(exc)
                        if et == "rate_limit" and attempt < 3:
                            # a per-day quota does not come back in a minute;
                            # the alternate model has its own. Not the network's
                            # call -- a fact about the agent's own account -- so
                            # both twins do it alike.
                            if _daily_quota(exc):
                                tried.add(state["model"])
                                alts = [m for m in p.get("alt_models", []) if m not in tried]
                                if alts:
                                    state["model"] = alts[0]
                                    self.model = f"{self.model}->{alts[0]}"
                                    continue
                                # every model on the provider has hit its
                                # day's wall: only then is the provider dead
                                # (a successful switch used to mark it too,
                                # and skipped its askers for two hours)
                                self.provider_dead_today = True
                            self._sleep(25)
                            continue
                        self.failures.append({"service": p["host"], "operation": "chat.completions", "error_type": et,
                                              "error_code": code, "asked": False, "recommended": None,
                                              "attempts": attempt + 1, "recovered": False})
                        answer = f"(provider failed: {et})"
                        break
                if resp is None:
                    break
                msg = resp["choices"][0]["message"]
                calls = msg.get("tool_calls") or []
                if not calls:
                    answer = (msg.get("content") or "").strip()[:300]
                    break
                messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
                for c in calls:
                    try:
                        args = json.loads(c["function"].get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    name = c["function"]["name"]
                    self.tool_calls += 1
                    if name == "run_python":
                        result = json.dumps(b.run_python(str(args.get("code") or ""), str(args.get("filename") or "task.py"),
                                                         args.get("requirements") if isinstance(args.get("requirements"), list) else None))
                    elif name == "resolve_python_deps":
                        result = json.dumps(b.resolve_python_deps(args.get("requirements") if isinstance(args.get("requirements"), list) else []))
                    elif name == "fetch_doc":
                        url = str(args.get("url") or "")
                        host = (urllib.parse.urlsplit(url).hostname or "").lower() or "invalid"
                        if host not in DOC_HOSTS:
                            # our allowlist refusing is not the host failing:
                            # nothing was called, nothing is reported (18 Sep
                            # 03:xx both builders filed httpbingo.org
                            # fetch_doc/error for fetching task data this way)
                            self.tool_calls += 1
                            result = json.dumps({"error": "fetch_doc reads documentation only, from: " + ", ".join(DOC_HOSTS)
                                                 + ". Fetch data from inside run_python."})
                        else:
                            self.tool_calls -= 1   # call() counts it
                            wrapped = self.fe.watch(service=host, operation="fetch_doc", mutates=False)(fetch_doc)
                            result, _ = self.call("fetch_doc", {"url": url}, fn=wrapped, service=host)
                    else:
                        result = json.dumps({"error": "unknown tool"})
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": result[:6000]})
            self.build = b.summary()
            self.build["task_done"] = b.last_ok and not answer.startswith("(")
            return answer


# ---------------------------------------------------------------------------
# scheduling and the scoreboard
# ---------------------------------------------------------------------------


def _quota_probe_due(marked: str) -> bool:
    """True when a dead-provider mark is old enough to probe. A bare date
    (the pre-18-Sep format) counts as marked at that day's midnight UTC."""
    try:
        when = dt.datetime.fromisoformat(marked)
    except (TypeError, ValueError):
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - when).total_seconds() >= QUOTA_PROBE_SECONDS


@contextlib.contextmanager
def _state_lock():
    """Two lanes share one state file. The lock is held while choosing a
    persona and while filing a run's record, never during the run."""
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, "state.lock"), "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


#: The ledger (state.json) keeps the last LEDGER_CAP runs, and the light lane
#: fills that in about two and a half days. That was fine for groups that run
#: every minute and quietly fatal for the ones that run every few hours: the
#: OpenCode pairs could never hold more than ~25 runs a side, because older
#: runs fell off the end as fast as new ones arrived. Their records now go to
#: an archive when the cap would drop them, and are kept ARCHIVE_DAYS.
LEDGER_CAP = 5000
ARCHIVE_DAYS = 30


def _slow_arms() -> set[str]:
    """Personas whose runs are too rare to survive the ledger's cap."""
    return (OPENCODE_PERSONAS | OCPROXY_PERSONAS | OCHOOK_PERSONAS | LOCAL_PERSONAS | PROD_ADVICE_PERSONAS
            | TEAM_PERSONAS)


def _archive_path() -> str:
    return os.path.join(STATE_DIR, "archive.jsonl")


def _archived() -> list[dict]:
    """Every archived record, oldest first. Unreadable lines are skipped: an
    archive that half-wrote once must not take the scoreboard down with it."""
    out = []
    try:
        with open(_archive_path(), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return out


def _archive(evicted: list[dict]) -> None:
    """Keep the slow arms' records the ledger cap is about to drop."""
    keep = [r for r in evicted if r.get("reporter") in _slow_arms()]
    if not keep:
        return
    cutoff = (dt.datetime.now(dt.UTC) - dt.timedelta(days=ARCHIVE_DAYS)).isoformat(timespec="seconds")
    records = [r for r in _archived() if str(r.get("at", "")) >= cutoff] + keep
    tmp = _archive_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    os.replace(tmp, _archive_path())


def _state() -> dict:
    os.makedirs(STATE_DIR, exist_ok=True)
    try:
        with open(os.path.join(STATE_DIR, "state.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"next": 0, "runs": [], "day": "", "runs_today": 0}


def _save(state: dict) -> None:
    with open(os.path.join(STATE_DIR, "state.json"), "w", encoding="utf-8") as fh:
        json.dump(state, fh)


#: A run that never started because a cap said no is not a run.
_NOT_A_RUN = ("daily cap reached; run skipped", "(no provider key)")

#: Answers that are the harness speaking rather than the agent. A run whose
#: provider refused it never produced an answer, so there is nothing to grade:
#: on 22 Sep one `(provider failed: rate_limit)` was scored as three wrong
#: values and put the asking side at 75% against 100% on four runs. The run
#: itself still counts -- meeting a provider failure is the provider group's
#: entire subject -- it just has no answer in it.
_NO_ANSWER = _NOT_A_RUN + ("(provider failed", "(provider ", "(no answer", "(model budget exhausted")


def _is_run(r: dict) -> bool:
    """False for a record of a run that never started. Records before
    18 Sep 19:40 carry no answer for those; they are the ones with a
    provider, no calls of any kind, no failures, and no time spent."""
    if any(m in (r.get("answer") or "") for m in _NOT_A_RUN):
        return False
    return not (r.get("provider") and not r.get("tool_calls") and not r.get("model_calls")
                and not r.get("failures") and float(r.get("seconds") or 0) < 1.0)


def _lost_to_guest_memory(r: dict) -> bool:
    """An OpenCode run the guest kernel killed for memory before it produced
    its result: the harness failing, not the agent. The twins with FailEcho
    run one more process (an MCP client, or the proxy) in the same 768 MB,
    and took 4 of 5 such kills on 19 Sep -- counting them as failed tasks
    scored the harness against the product. Excluded from every rate and
    counted on their own row, per side."""
    o = r.get("opencode") or {}
    return bool(o.get("guest_oom_kills")) and not o.get("completed")


#: Below this many runs a side a table marks no winner. The page bolds "the
#: better side", and on 23 Sep it bolded the plugin arm's 100% vs 0% on two
#: runs a side and the MCP twin's 100% vs 50% on four: true arithmetic, false
#: impression. Both sides lose their bold equally.
MIN_RUNS_FOR_A_WINNER = 10


def _no_winner_on_too_few_runs(versus: list[dict]) -> None:
    for group in versus:
        if min(group.get("runs_ask") or 0, group.get("runs_blind") or 0) < MIN_RUNS_FOR_A_WINNER:
            group["too_few_runs"] = True
            for row in group["rows"]:
                row["better"] = "tie"
            continue
        # rows over a sub-sample carry their own count: the stricter-rules split
        rows = {r["metric"]: r for r in group["rows"]}
        since = rows.get("runs since the stricter rules")
        if since and min(since.get("ask") or 0, since.get("blind") or 0) < MIN_RUNS_FOR_A_WINNER:
            for row in group["rows"]:
                if "since the rules" in row["metric"]:
                    row["better"] = "tie"


def _scored(r: dict) -> dict:
    """The run as the scoreboard counts it.

    Since 08:06 on 23 Sep a model run with an empty answer is not completed
    (the rule in main()). The seven recorded before then still say completed, and
    were counted as finished work that produced nothing. The ledger is left as
    written; the report applies today's rule to every run that kept its
    answer. Builders and OpenCode runs finish by their own rules.
    """
    answer = r.get("answer")
    if (isinstance(answer, str) and not answer.strip() and not r.get("build") and not r.get("opencode")
            and (r.get("metrics") or {}).get("completed")):
        return {**r, "metrics": {**r["metrics"], "completed": False}}
    return r


def write_report(state: dict) -> None:
    archived = _archived()
    everything = [_scored(r) for r in archived + state["runs"]]
    lost_memory = {"ask": {}, "blind": {}}
    for r in everything:
        if _lost_to_guest_memory(r):
            side = lost_memory["ask" if r.get("asks") else "blind"]
            side[r["reporter"]] = side.get(r["reporter"], 0) + 1
    runs = [r for r in everything if _is_run(r) and not _lost_to_guest_memory(r)]
    by_persona: dict[str, dict] = {}
    blank = lambda: {"runs": 0, "failures": 0, "attempts": 0, "recovered": 0, "asked": 0, "recommended": 0, "skipped": 0,
                     "provider_failures": 0, "provider_recovered": 0, "explored": 0}
    cohorts = {"real / ask": blank(), "real / blind": blank(), "test / ask": blank(), "test / blind": blank(),
               "build / ask": blank(), "build / blind": blank(), "explore": blank()}
    # the builders' second ledger: what happened inside the VM
    blank_build = lambda: {"vm_runs": 0, "local": 0, "shared": 0, "tasks_done": 0, "sandbox_down": 0,
                           "traffic_runs": 0, "unobserved_runs": 0, "connections": 0, "observed_calls": 0}
    build = {"build / ask": blank_build(), "build / blind": blank_build()}
    for r in runs:
        p = by_persona.setdefault(r["reporter"], {"reporter": r["reporter"], "path": r["path"], "provider": r["provider"] or "none",
                                                   "asks": r["asks"], "runs": 0, "tool_calls": 0, "failures": 0, "last": ""})
        p["runs"] += 1; p["tool_calls"] += r["tool_calls"]; p["failures"] += len(r["failures"]); p["last"] = r["at"]
        kind = ("test" if r["reporter"] in TEST_PERSONAS else "build" if r["reporter"] in BUILD_PERSONAS
                else "opencode" if r["reporter"] in OPENCODE_PERSONAS
                else "ochook" if r["reporter"] in OCHOOK_PERSONAS
                else "ocproxy" if r["reporter"] in OCPROXY_PERSONAS
                else "prod" if r["reporter"] in PROD_ADVICE_PERSONAS
                else "local" if r["reporter"] in LOCAL_PERSONAS
                else "team" if r["reporter"] in TEAM_PERSONAS
                else "wrapped" if r["reporter"] in WRAPPED_PERSONAS else "real")
        key = "explore" if r["reporter"] in EXPLORER_PERSONAS else kind + (" / ask" if r["asks"] else " / blind")
        c = cohorts.setdefault(key, blank())
        c["runs"] += 1
        if kind == "build" and r.get("build"):
            bl, b = build[key], r["build"]
            bl["vm_runs"] += b.get("vm_runs", 0); bl["local"] += b.get("local_failures", 0)
            bl["shared"] += len(b.get("shared_failures") or []); bl["tasks_done"] += int(bool(b.get("task_done")))
            bl["sandbox_down"] += int(b.get("sandbox", "ok") != "ok")
            for cov in b.get("coverage") or []:
                bl["connections"] += cov["connections"]; bl["observed_calls"] += cov["observed"]
                if cov["connections"] > 0:
                    bl["traffic_runs"] += 1; bl["unobserved_runs"] += int(cov["missed"])
                    if cov["missed"]:
                        for lib in cov.get("libs") or ["?"]:
                            bl.setdefault("missed_libs", {})[lib] = bl.get("missed_libs", {}).get(lib, 0) + 1
        for f in r["failures"]:
            c["failures"] += 1; c["attempts"] += f["attempts"]; c["recovered"] += int(f["recovered"])
            c["asked"] += int(f["asked"]); c["recommended"] += int(bool(f["recommended"])); c["skipped"] += int(bool(f.get("skipped")))
            if f["operation"] == "chat.completions":
                c["provider_failures"] += 1; c["provider_recovered"] += int(f["recovered"]); c["explored"] += int(bool(f.get("explored")))

    # real targets, per service and cohort: the proof table. Test endpoints
    # and model providers are left out on purpose; this is PyPI, GitHub,
    # crates.io, Stack Exchange answering real agents behind one address.
    real: dict[tuple[str, str], dict] = {}
    for r in runs:
        if r["reporter"] in TEST_PERSONAS or r["reporter"] in BUILD_PERSONAS or r["reporter"] in EXPLORER_PERSONAS \
                or r["reporter"] in OPENCODE_PERSONAS or r["reporter"] in WRAPPED_PERSONAS \
                or r["reporter"] in OCPROXY_PERSONAS or r["reporter"] in OCHOOK_PERSONAS \
                or r["reporter"] in PROD_ADVICE_PERSONAS or r["reporter"] in LOCAL_PERSONAS \
                or r["reporter"] in TEAM_PERSONAS:
            continue
        if r["at"] < FAIR_ORDER_SINCE:
            continue   # before the twins alternated order; see TWINS
        side = "ask" if r["asks"] else "blind"
        for f in r["failures"]:
            if f["operation"] == "chat.completions" or f["service"] == "httpbingo.org":
                continue
            row = real.setdefault((f["service"], side), {"service": f["service"], "cohort": side, "failures": 0,
                                                          "attempts": 0, "recovered": 0, "skipped": 0, "seconds": 0.0})
            row["failures"] += 1; row["attempts"] += f["attempts"]; row["recovered"] += int(f["recovered"])
            row["skipped"] += int(bool(f.get("skipped"))); row["seconds"] += float(f.get("seconds") or 0)
    real_targets = sorted(real.values(), key=lambda x: (x["service"], x["cohort"]))

    # Cost and outcome per run, by cohort: every recorded cost, so a change
    # to the product can be judged on all of them, not on the one it moved.
    # Only runs that carry metrics (recorded since 2026-09-17 10:30 UTC).
    costs: dict[str, dict] = {}
    #: first and last run per cohort, so every table can say what it covers
    spans: dict[str, list[str]] = {}
    for r in runs:
        m = r.get("metrics")
        if not m:
            continue
        kind = ("test" if r["reporter"] in TEST_PERSONAS else "build" if r["reporter"] in BUILD_PERSONAS
                else "opencode" if r["reporter"] in OPENCODE_PERSONAS
                else "ochook" if r["reporter"] in OCHOOK_PERSONAS
                else "ocproxy" if r["reporter"] in OCPROXY_PERSONAS
                else "prod" if r["reporter"] in PROD_ADVICE_PERSONAS
                else "local" if r["reporter"] in LOCAL_PERSONAS
                else "team" if r["reporter"] in TEAM_PERSONAS
                else "wrapped" if r["reporter"] in WRAPPED_PERSONAS else "real")
        key = "explore" if r["reporter"] in EXPLORER_PERSONAS else kind + (" / ask" if r["asks"] else " / blind")
        span = spans.setdefault(key, [r["at"], r["at"]])
        span[0], span[1] = min(span[0], r["at"]), max(span[1], r["at"])
        c = costs.setdefault(key, {"cohort": key, "runs": 0, "completed": 0, "tokens": 0, "tokens_prompt": 0,
                                   "tokens_completion": 0, "model_calls": 0, "tool_calls": 0, "seconds": 0.0,
                                   "asks": 0, "ask_seconds": 0.0, "wait_seconds": 0.0, "calls_first_try": 0,
                                   "calls_recovered": 0, "calls_failed": 0, "failure_seconds": 0.0,
                                   "model_retries_after_skip": 0})
        c["runs"] += 1; c["completed"] += int(bool(m.get("completed")))
        c["tokens_prompt"] += m.get("tokens_prompt", 0); c["tokens_completion"] += m.get("tokens_completion", 0)
        c["tokens"] = c["tokens_prompt"] + c["tokens_completion"]
        c["model_calls"] += r.get("model_calls", 0); c["tool_calls"] += r.get("tool_calls", 0)
        c["seconds"] += float(r.get("seconds") or 0); c["asks"] += m.get("asks", 0)
        c["ask_seconds"] += float(m.get("ask_seconds") or 0); c["wait_seconds"] += float(m.get("wait_seconds") or 0)
        c["calls_first_try"] += m.get("calls_first_try", 0); c["calls_recovered"] += m.get("calls_recovered", 0)
        c["calls_failed"] += m.get("calls_failed", 0)
        c["failure_seconds"] += sum(float(f.get("seconds") or 0) for f in r["failures"])
        c["model_retries_after_skip"] += m.get("model_retries_after_skip", 0)
        g = _current_grade(r) if r["at"] >= GRADING_SINCE else {}
        if any(m in (r.get("answer") or "") for m in _NO_ANSWER):
            g = {}          # the harness spoke, not the agent: nothing graded
        if g.get("valid") is not None:
            c["graded_runs"] = c.get("graded_runs", 0) + 1
            c["valid_runs"] = c.get("valid_runs", 0) + int(bool(g.get("valid")))
        if g.get("correct") is not None:
            c["checkable_runs"] = c.get("checkable_runs", 0) + 1
            c["correct_runs"] = c.get("correct_runs", 0) + int(bool(g.get("correct")))
        c["refused_values"] = c.get("refused_values", 0) + int(g.get("refused") or 0)
    for c in costs.values():
        n = c["runs"] or 1
        c.update(completed_rate=round(c["completed"] / n, 3), tokens_per_run=round(c["tokens"] / n),
                 tokens_per_completed=(round(c["tokens"] / c["completed"]) if c["completed"] else None),
                 seconds_per_run=round(c["seconds"] / n, 1), model_calls_per_run=round(c["model_calls"] / n, 2),
                 tool_calls_per_run=round(c["tool_calls"] / n, 2), asks_per_run=round(c["asks"] / n, 2),
                 ask_seconds_per_run=round(c["ask_seconds"] / n, 3), wait_seconds_per_run=round(c["wait_seconds"] / n, 2),
                 failure_seconds_per_run=round(c["failure_seconds"] / n, 2))
        for k in ("seconds", "ask_seconds", "wait_seconds", "failure_seconds"):
            c[k] = round(c[k], 1)
    cost_rows = [costs[k] for k in ("real / ask", "real / blind", "test / ask", "test / blind",
                                    "build / ask", "build / blind", "opencode / ask", "opencode / blind",
                                    "wrapped / ask", "wrapped / blind", "ocproxy / ask", "ocproxy / blind", "prod / ask", "prod / blind",
                                    "local / ask", "local / blind",
                                    "explore") if k in costs]

    # With FailEcho against without, the way a vendor benchmark table reads:
    # metrics as rows, the two sides as columns, the better side marked. Built
    # from the same numbers as the cost and cohort tables, so it cannot say
    # anything they do not.
    def _better(ask, blind, lower_is_better=True):
        if ask is None or blind is None:
            return "tie"
        if max(abs(ask), abs(blind)) == 0 or abs(ask - blind) / max(abs(ask), abs(blind), 1e-9) < 0.02:
            return "tie"
        return ("ask" if ask < blind else "blind") if lower_is_better else ("ask" if ask > blind else "blind")

    def _pct(v):
        return None if v is None else round(v * 100, 1)

    versus = []
    for key, label in (("test", "Flaky, broken and slow endpoints (httpbingo)"),
                       ("real", "Real APIs (PyPI, npm, GitHub, crates.io, Stack Exchange)"),
                       ("build", "Coding agents (write and run code in a VM)"),
                       ("opencode", "OpenCode, a real agent product, with FailEcho's MCP server and without"),
                       ("ochook", "OpenCode with the FailEcho plugin (no tool for the model to choose) and without"),
                       ("wrapped", "The shipped wrapper: advice in the tool error, the model decides (no harness help)"),
                       ("ocproxy", "OpenCode with an MCP server behind failecho-mcp proxy, and without"),
                       ("prod", "Real APIs, advice read from production (what a new user gets today)"),
                       ("local", "Both sides recover like a careful engineer; one also asks the network"),
                       ("team", "A small team in private mode: two agents with only their own evidence, against two without")):
        a, b = costs.get(f"{key} / ask"), costs.get(f"{key} / blind")
        ca, cb = cohorts.get(f"{key} / ask"), cohorts.get(f"{key} / blind")
        if not a or not b:
            continue
        apf_a = (ca["attempts"] / ca["failures"]) if ca and ca["failures"] else None
        apf_b = (cb["attempts"] / cb["failures"]) if cb and cb["failures"] else None
        rows = [
            {"metric": "runs marked completed (see grading)", "unit": "%", "ask": _pct(a["completed_rate"]), "blind": _pct(b["completed_rate"]),
             "better": _better(a["completed_rate"], b["completed_rate"], lower_is_better=False)},
            {"metric": "tokens per completed task", "unit": "", "ask": a["tokens_per_completed"], "blind": b["tokens_per_completed"],
             "better": _better(a["tokens_per_completed"], b["tokens_per_completed"])},
            {"metric": "seconds per run", "unit": "s", "ask": a["seconds_per_run"], "blind": b["seconds_per_run"],
             "better": _better(a["seconds_per_run"], b["seconds_per_run"])},
            {"metric": "seconds lost inside failures, per run", "unit": "s", "ask": a["failure_seconds_per_run"], "blind": b["failure_seconds_per_run"],
             "better": _better(a["failure_seconds_per_run"], b["failure_seconds_per_run"])},
            {"metric": "retry attempts per failure", "unit": "", "ask": round(apf_a, 2) if apf_a else None, "blind": round(apf_b, 2) if apf_b else None,
             "better": _better(apf_a, apf_b)},
            {"metric": "retries the network said to skip", "unit": "", "ask": ca["skipped"] if ca else 0, "blind": cb["skipped"] if cb else 0,
             "better": _better(-(ca["skipped"] if ca else 0), -(cb["skipped"] if cb else 0))},
        ]
        if key == "test":
            rows = [r for r in rows if r["metric"] not in ("runs marked completed (see grading)", "tokens per completed task")]
        if key == "build":
            rows.append({"metric": "seconds waiting on rate limits, per run", "unit": "s", "ask": a["wait_seconds_per_run"],
                         "blind": b["wait_seconds_per_run"], "better": _better(a["wait_seconds_per_run"], b["wait_seconds_per_run"])})
        if key in ("opencode", "ocproxy", "ochook"):
            members = {"opencode": OPENCODE_PERSONAS, "ocproxy": OCPROXY_PERSONAS,
                       "ochook": OCHOOK_PERSONAS}[key]
            lost = {side: sum(n for rep, n in lost_memory[side].items() if rep in members) for side in ("ask", "blind")}
            # the agent's own failures are inside OpenCode; what is visible is the
            # artefact, the steps, the tokens, and whether it reached for FailEcho
            rows = [r for r in rows if r["metric"] in ("runs marked completed (see grading)", "tokens per completed task", "seconds per run")]
            rows.append({"metric": "model steps per run", "unit": "", "ask": a["model_calls_per_run"], "blind": b["model_calls_per_run"],
                         "better": _better(a["model_calls_per_run"], b["model_calls_per_run"])})
            rows.append({"metric": "FailEcho tool calls per run" if key == "opencode" else "tool outputs carrying advice, per run",
                         "unit": "", "ask": a["asks_per_run"], "blind": b["asks_per_run"], "better": "tie"})
            if key == "opencode":
                # the same two numbers since the instruction became a rule
                since = [r for r in runs if r["reporter"] in OPENCODE_PERSONAS and r["at"] >= MCP_RULES_SINCE]
                sides = {True: [r for r in since if r["asks"]], False: [r for r in since if not r["asks"]]}
                def _per_run(rs, field):
                    if not rs:
                        return None
                    return round(sum((r.get("metrics") or {}).get(field, 0) for r in rs) / len(rs), 2)
                def _done(rs):
                    return _pct(sum(1 for r in rs if (r.get("metrics") or {}).get("completed")) / len(rs)) if rs else None
                rows.append({"metric": "runs since the stricter rules", "unit": "",
                             "ask": len(sides[True]), "blind": len(sides[False]), "better": "tie"})
                rows.append({"metric": "FailEcho tool calls per run, since the rules", "unit": "",
                             "ask": _per_run(sides[True], "asks"), "blind": _per_run(sides[False], "asks"),
                             "better": "tie"})
                rows.append({"metric": "runs marked completed, since the rules", "unit": "%",
                             "ask": _done(sides[True]), "blind": _done(sides[False]),
                             "better": _better(_done(sides[True]), _done(sides[False]), lower_is_better=False)})
            # Whether the run gave FailEcho anything to do. A run that met no
            # failing tool cannot show an instruction being obeyed or ignored,
            # and before 23 Sep nothing counted them -- the only failures on
            # record were the provider's.
            def _met(rs):
                counted = [r for r in rs if "tool_failures" in (r.get("opencode") or {})]
                if not counted:
                    return None, None
                met = [r for r in counted if (r["opencode"].get("tool_failures") or 0) > 0]
                calls = sum(int(r["opencode"].get("failecho_calls") or 0)
                            + int(r["opencode"].get("advice_seen") or 0) for r in met)
                return len(met), (round(calls / len(met), 2) if met else None)
            group_runs = [r for r in runs if r["reporter"] in members]
            met_a = _met([r for r in group_runs if r["asks"]])
            met_b = _met([r for r in group_runs if not r["asks"]])
            rows.append({"metric": "runs that met a failing tool", "unit": "",
                         "ask": met_a[0], "blind": met_b[0], "better": "tie"})
            rows.append({"metric": "FailEcho calls or advice lines, per run that met one", "unit": "",
                         "ask": met_a[1], "blind": met_b[1], "better": "tie"})
            rows.append({"metric": "runs lost to guest memory (not counted above)", "unit": "",
                         "ask": lost["ask"], "blind": lost["blind"], "better": "tie"})
            # graded against truth fetched independently, not against a pattern
            def _rate(side, num, den):
                n, d = side.get(num, 0), side.get(den, 0)
                return _pct(n / d) if d else None
            valid = (_rate(a, "valid_runs", "graded_runs"), _rate(b, "valid_runs", "graded_runs"))
            correct = (_rate(a, "correct_runs", "checkable_runs"), _rate(b, "correct_runs", "checkable_runs"))
            rows.append({"metric": "result complete and not a placeholder", "unit": "%",
                         "ask": valid[0], "blind": valid[1],
                         "better": _better(valid[0], valid[1], lower_is_better=False)})
            rows.append({"metric": "every checked value correct", "unit": "%",
                         "ask": correct[0], "blind": correct[1],
                         "better": _better(correct[0], correct[1], lower_is_better=False)})
            rows.append({"metric": "runs where truth was checkable", "unit": "",
                         "ask": a.get("checkable_runs", 0), "blind": b.get("checkable_runs", 0), "better": "tie"})
            rows.append({"metric": "values declined rather than invented", "unit": "",
                         "ask": a.get("refused_values", 0), "blind": b.get("refused_values", 0),
                         "better": "tie"})
        if key == "wrapped":
            # nothing retries for the model here, so attempts and skips are
            # the harness's numbers and mean nothing; what the model did is
            # in its tool calls, and whether it got the task done
            rows = [r for r in rows if r["metric"] in ("runs marked completed (see grading)", "tokens per completed task", "seconds per run")]
            rows.append({"metric": "tool calls per run", "unit": "", "ask": a["tool_calls_per_run"], "blind": b["tool_calls_per_run"],
                         "better": _better(a["tool_calls_per_run"], b["tool_calls_per_run"])})
            rows.append({"metric": "failures with advice attached", "unit": "", "ask": ca["asked"] if ca else 0,
                         "blind": cb["asked"] if cb else 0, "better": "tie"})
        if key == "team":
            # The question a paying team asks: if we switch this on, when does
            # it start helping? A recommendation needs five recovered attempts
            # on one failure shape, and a small team produces those slowly.
            asked = sorted((r for r in runs if r["reporter"] in TEAM_ASKERS), key=lambda r: r["at"])
            with_fix = [r for r in asked if any(f.get("recommended") for f in r.get("failures") or [])]
            hours = None
            if asked and with_fix:
                start = dt.datetime.fromisoformat(asked[0]["at"])
                hours = round((dt.datetime.fromisoformat(with_fix[0]["at"]) - start).total_seconds() / 3600, 1)
            failures_met = sum(len(r.get("failures") or []) for r in asked)
            advised = sum(1 for r in asked for f in r.get("failures") or [] if f.get("recommended"))
            rows.append({"metric": "hours until the team's own history had its first fix", "unit": "h",
                         "ask": hours, "blind": None, "better": "tie"})
            rows.append({"metric": "failures where the team's own history had a fix", "unit": "%",
                         "ask": _pct(advised / failures_met) if failures_met else None, "blind": None,
                         "better": "tie"})
        if key not in ("opencode", "ocproxy", "ochook") and (a.get("graded_runs") or b.get("graded_runs")):
            # The light lane answers in prose and was graded on a pattern
            # until 22 Sep: "completed" meant the model said something. These
            # three rows say what it actually got right, on far more runs than
            # the OpenCode pair will ever produce.
            def _rate(side, num, den):
                n, d = side.get(num, 0), side.get(den, 0)
                return _pct(n / d) if d else None
            valid = (_rate(a, "valid_runs", "graded_runs"), _rate(b, "valid_runs", "graded_runs"))
            correct = (_rate(a, "correct_runs", "checkable_runs"), _rate(b, "correct_runs", "checkable_runs"))
            rows.append({"metric": "answer complete and not a placeholder", "unit": "%",
                         "ask": valid[0], "blind": valid[1],
                         "better": _better(valid[0], valid[1], lower_is_better=False)})
            rows.append({"metric": "every checked value correct", "unit": "%",
                         "ask": correct[0], "blind": correct[1],
                         "better": _better(correct[0], correct[1], lower_is_better=False)})
            rows.append({"metric": "runs where truth was checkable", "unit": "",
                         "ask": a.get("checkable_runs", 0), "blind": b.get("checkable_runs", 0), "better": "tie"})
            rows.append({"metric": "values declined rather than invented", "unit": "",
                         "ask": a.get("refused_values", 0), "blind": b.get("refused_values", 0),
                         "better": "tie"})
        covered = spans.get(f"{key} / ask", []) + spans.get(f"{key} / blind", [])
        versus.append({"group": key, "label": label, "runs_ask": a["runs"], "runs_blind": b["runs"],
                       "from": min(covered) if covered else None, "to": max(covered) if covered else None,
                       "rows": rows})
    # Model providers under their real quotas: the model-driven personas
    # (not builders, not explorers) since blind started retrying once. A
    # provider 429 is the most common real failure the fleet meets, and the
    # one where a right answer (switch model, wait the stated seconds) turns
    # a failed task into a finished one.
    prov: dict[str, dict] = {}
    for r in runs:
        if r["at"] < PROVIDER_CONTROL_SINCE or not r.get("provider") or not r.get("metrics"):
            continue
        if r["reporter"] in BUILD_PERSONAS or r["reporter"] in EXPLORER_PERSONAS or r["reporter"] in OPENCODE_PERSONAS \
                or r["reporter"] in WRAPPED_PERSONAS or r["reporter"] in OCPROXY_PERSONAS \
                or r["reporter"] in OCHOOK_PERSONAS \
                or r["reporter"] in PROD_ADVICE_PERSONAS or r["reporter"] in LOCAL_PERSONAS \
                or r["reporter"] in TEAM_PERSONAS:
            continue
        side = "ask" if r["asks"] else "blind"
        c = prov.setdefault(side, {"runs": 0, "completed": 0, "failures": 0, "recovered": 0, "tokens": 0, "seconds": 0.0,
                                   "skipped_for_quota": 0, "first": r["at"], "last": r["at"]})
        c["first"], c["last"] = min(c["first"], r["at"]), max(c["last"], r["at"])
        c["runs"] += 1; c["completed"] += int(bool(r["metrics"].get("completed")))
        c["tokens"] += r["metrics"].get("tokens_prompt", 0) + r["metrics"].get("tokens_completion", 0)
        c["seconds"] += float(r.get("seconds") or 0)
        for f in r["failures"]:
            if f["operation"] == "chat.completions":
                c["failures"] += 1; c["recovered"] += int(f["recovered"])
    if "ask" in prov and "blind" in prov:
        a, b = prov["ask"], prov["blind"]
        cr = lambda c: c["completed"] / c["runs"] if c["runs"] else None   # noqa: E731
        rr = lambda c: c["recovered"] / c["failures"] if c["failures"] else None   # noqa: E731
        tpc = lambda c: round(c["tokens"] / c["completed"]) if c["completed"] else None   # noqa: E731
        spr = lambda c: round(c["seconds"] / c["runs"], 1) if c["runs"] else None   # noqa: E731
        versus.append({"group": "provider", "label": "Model providers under real quotas (groq, Gemini, OpenRouter, Ollama)",
                       "since": PROVIDER_CONTROL_SINCE, "runs_ask": a["runs"], "runs_blind": b["runs"],
                       "from": min(a["first"], b["first"]), "to": max(a["last"], b["last"]), "rows": [
            {"metric": "runs marked completed (see grading)", "unit": "%", "ask": _pct(cr(a)), "blind": _pct(cr(b)),
             "better": _better(cr(a), cr(b), lower_is_better=False)},
            {"metric": "provider failures met", "unit": "", "ask": a["failures"], "blind": b["failures"], "better": "tie"},
            {"metric": "provider failures recovered", "unit": "%", "ask": _pct(rr(a)), "blind": _pct(rr(b)),
             "better": _better(rr(a), rr(b), lower_is_better=False)},
            {"metric": "tokens per completed task", "unit": "", "ask": tpc(a), "blind": tpc(b), "better": _better(tpc(a), tpc(b))},
            {"metric": "seconds per run", "unit": "s", "ask": spr(a), "blind": spr(b), "better": _better(spr(a), spr(b))},
        ]})
    # What the advice is worth, by failure shape: once a call has failed,
    # what happened to the second attempt when the network recommended it,
    # when the asker had no advice (it then does what blind does), and when
    # blind retried -- and, for the skip verdict, what blind's retries
    # yielded in the same hour on the same shape the network told askers to
    # skip. The forfeited-recovery rate is the cost of a skip.
    shapes: dict[tuple, dict] = {}
    skipped_keys: set[tuple] = set()
    for r in runs:
        if r["at"] < FAIR_ORDER_SINCE or r["reporter"] in EXPLORER_PERSONAS:
            continue
        for f in r["failures"]:
            k = (f["service"], f["error_type"], f.get("error_code"))
            row = shapes.setdefault(k, {"service": k[0], "error_type": k[1], "error_code": k[2], "skipped": 0,
                                        "advised_retries": 0, "advised_recovered": 0, "unadvised_retries": 0,
                                        "unadvised_recovered": 0, "blind_retries": 0, "blind_recovered": 0,
                                        "blind_retries_where_skipped": 0, "blind_recovered_where_skipped": 0})
            if r["asks"] and f.get("skipped"):
                row["skipped"] += 1
                skipped_keys.add((r["at"][:13], f["service"], f["operation"], f["error_type"], f.get("error_code")))
                continue
            if f["attempts"] < 2:
                continue
            side = ("advised" if f.get("recommended") else "unadvised") if r["asks"] else "blind"
            row[side + "_retries"] += 1
            row[side + "_recovered"] += int(f["recovered"])
    for r in runs:
        if r["at"] < FAIR_ORDER_SINCE or r["asks"] or r["reporter"] in EXPLORER_PERSONAS:
            continue
        for f in r["failures"]:
            if f["attempts"] >= 2 and (r["at"][:13], f["service"], f["operation"], f["error_type"], f.get("error_code")) in skipped_keys:
                row = shapes[(f["service"], f["error_type"], f.get("error_code"))]
                row["blind_retries_where_skipped"] += 1
                row["blind_recovered_where_skipped"] += int(f["recovered"])
    advice = sorted((row for row in shapes.values()
                     if row["skipped"] + row["advised_retries"] + row["unadvised_retries"] + row["blind_retries"] >= 8),
                    key=lambda x: -(x["skipped"] + x["advised_retries"] + x["unadvised_retries"] + x["blind_retries"]))

    # Does the network get better as evidence accumulates? Per half day, for
    # the model-driven and cron twins: the share of an asker's failures the
    # network had a recommendation for, and what each side completed.
    halves: dict[str, dict] = {}
    for r in runs:
        if r["reporter"] in TEST_PERSONAS or r["reporter"] in BUILD_PERSONAS or r["reporter"] in EXPLORER_PERSONAS \
                or r["reporter"] in OPENCODE_PERSONAS or r["reporter"] in WRAPPED_PERSONAS \
                or r["reporter"] in OCPROXY_PERSONAS or r["reporter"] in OCHOOK_PERSONAS \
                or r["reporter"] in PROD_ADVICE_PERSONAS or r["reporter"] in LOCAL_PERSONAS \
                or r["reporter"] in TEAM_PERSONAS:
            continue
        if not r.get("metrics"):
            continue
        half = r["at"][:10] + (" 00-12" if r["at"][11:13] < "12" else " 12-24")
        h = halves.setdefault(half, {"period": half, "ask_runs": 0, "ask_completed": 0, "blind_runs": 0, "blind_completed": 0,
                                     "ask_failures": 0, "ask_advised": 0, "ask_failure_seconds": 0.0, "blind_failure_seconds": 0.0})
        side = "ask" if r["asks"] else "blind"
        h[side + "_runs"] += 1
        h[side + "_completed"] += int(bool(r["metrics"].get("completed")))
        h[side + "_failure_seconds"] += sum(float(f.get("seconds") or 0) for f in r["failures"])
        if r["asks"]:
            for f in r["failures"]:
                h["ask_failures"] += 1
                h["ask_advised"] += int(bool(f.get("recommended")))
    timeline = []
    for h in (halves[k] for k in sorted(halves)):
        h["advised_share"] = round(h["ask_advised"] / h["ask_failures"], 3) if h["ask_failures"] else None
        h["ask_completed_rate"] = round(h["ask_completed"] / h["ask_runs"], 3) if h["ask_runs"] else None
        h["blind_completed_rate"] = round(h["blind_completed"] / h["blind_runs"], 3) if h["blind_runs"] else None
        h["ask_failure_seconds_per_run"] = round(h["ask_failure_seconds"] / h["ask_runs"], 2) if h["ask_runs"] else None
        h["blind_failure_seconds_per_run"] = round(h["blind_failure_seconds"] / h["blind_runs"], 2) if h["blind_runs"] else None
        timeline.append(h)

    for row in real_targets:
        row["attempts_per_failure"] = round(row["attempts"] / row["failures"], 2) if row["failures"] else None
        row["seconds"] = round(row["seconds"], 1)

    repeats, naming, totals_db = [], [], {}
    if LAB_DB and os.path.exists(LAB_DB):
        try:
            c = sqlite3.connect(f"file:{LAB_DB}?mode=ro", uri=True)
            for row in c.execute(
                "select o.service, o.operation, o.error_type, o.error_code, count(distinct o.reporter_hash), count(*), "
                "(select group_concat(action || ' ' || sum_ok || '/' || n, ', ') from (select action, sum(successful) sum_ok, count(*) n "
                " from recovery_outcomes ro where ro.fingerprint = o.fingerprint group by action)) "
                "from observations o where o.outcome='failure' group by o.fingerprint having count(distinct o.reporter_hash) >= 2 "
                "order by 5 desc, 6 desc limit 25"):
                repeats.append({"service": row[0], "operation": row[1], "error_type": row[2], "error_code": row[3],
                                "reporters": row[4], "observations": row[5], "fixes": row[6]})
            for row in c.execute(
                "select service, group_concat(distinct operation), count(distinct fingerprint) from observations "
                "where outcome='failure' group by service order by 3 desc"):
                ops = sorted(set((row[1] or "").split(",")))
                paths = sorted({"route" if o.startswith(("GET ", "POST ")) else "tool" for o in ops})
                naming.append({"service": row[0], "operations": ops, "fingerprints": row[2],
                               "expected": len({o for o in ops if not o.startswith(("GET ", "POST "))}) or 1, "paths": paths})
            totals_db["cross_reporter_fingerprints"] = len(repeats)
            # raw rows plus the hourly rollups retention folds them into: the
            # lab prunes since 23 Sep, and a total that counted raw rows only
            # would fall every hour for no reason
            totals_db["recovery_outcomes"] = c.execute("select count(*) from recovery_outcomes").fetchone()[0] + (
                c.execute("select coalesce(sum(attempts), 0) from hourly_recovery_stats").fetchone()[0])
            totals_db["cross_agent_help"] = (c.execute("select coalesce(sum(value),0) from daily_counters where name='cross_agent_help'").fetchone() or [0])[0]
        except sqlite3.Error as e:
            totals_db["db_error"] = str(e)

    # the install canary and the onboarding test write their own files beside
    # the state; shown, not merged
    def _side(name):
        try:
            with open(os.path.join(STATE_DIR, name), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None
    canary, onboard = _side("canary.json"), _side("onboard.json")

    ledger = state["runs"]
    _no_winner_on_too_few_runs(versus)
    report = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        # What the numbers below are computed over. The fast groups cover the
        # ledger; the slow arms also cover the archive. Without this the page
        # said "since 17 Sep" over tables that held two and a half days.
        "window": {
            "ledger_runs": len(ledger), "ledger_cap": LEDGER_CAP,
            "ledger_from": ledger[0]["at"] if ledger else None,
            "ledger_to": ledger[-1]["at"] if ledger else None,
            "archived_runs": len(archived), "archive_days": ARCHIVE_DAYS,
            "archive_from": archived[0]["at"] if archived else None,
        },
        "canary": canary, "onboard": onboard,
        "totals": {"runs": len(runs), "personas": len(by_persona),
                   "providers_out_of_quota": sorted(state.get("provider_dead", {})),
                   "skipped_for_quota_today": state.get("skipped_for_quota_today", 0),
                   "tool_calls": sum(r["tool_calls"] for r in runs),
                   "failures": sum(len(r["failures"]) for r in runs), **totals_db},
        "cohorts": [{"cohort": k, **v, "attempts_per_failure": (v["attempts"] / v["failures"]) if v["failures"] else None}
                    for k, v in cohorts.items()],
        "build": [{"cohort": k, **v, "shared_share": (v["shared"] / (v["shared"] + v["local"])) if (v["shared"] + v["local"]) else None}
                  for k, v in build.items()],
        # the live feed: the last builder runs, what was asked and what happened
        "recent_builds": [
            {"at": r["at"], "reporter": r["reporter"], "task": r.get("task"), "seconds": r.get("seconds"),
             "vm_runs": r["build"].get("vm_runs", 0), "local": r["build"].get("local_failures", 0),
             "shared": len(r["build"].get("shared_failures") or []), "done": bool(r["build"].get("task_done")),
             "traffic": sum(c["connections"] for c in r["build"].get("coverage") or []),
             "observed": sum(c["observed"] for c in r["build"].get("coverage") or []),
             "answer": r.get("answer")}
            for r in runs if r.get("build") and r["reporter"] in BUILD_PERSONAS
        ][-12:][::-1],
        "repeats": repeats, "naming": naming, "real_targets": real_targets, "costs": cost_rows, "versus": versus,
        "advice": advice, "timeline": timeline,
        "personas": sorted(by_persona.values(), key=lambda p: p["reporter"]),
    }
    os.makedirs(os.path.dirname(REPORT_PATH) or ".", exist_ok=True)
    tmp = REPORT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report, fh)
    os.replace(tmp, REPORT_PATH)


def main(argv: list[str] | None = None) -> int:
    # a stop mid-run (systemd restarting a unit we depend on) must still close
    # the VM: SIGTERM becomes SystemExit so context managers unwind
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    argv = sys.argv[1:] if argv is None else argv
    assert_lab_only()
    lane = argv[argv.index("--lane") + 1] if "--lane" in argv else None
    if lane is not None and lane not in LANES:
        raise SystemExit(f"fleet: unknown lane {lane!r}; one of {LANES}")
    cursor = "next" if lane is None else f"next_{lane}"
    today = dt.date.today().isoformat()

    # -- choose, under the lock ------------------------------------------
    with _state_lock():
        state = _state()
        if state.get("day") != today:
            state["day"], state["runs_today"], state["skipped_for_quota_today"] = today, 0, 0
        if state["runs_today"] >= MAX_RUNS_PER_DAY:
            log("daily budget spent"); return 0
        dead = {k: v for k, v in state.get("provider_dead", {}).items() if not _quota_probe_due(v)}
        state["provider_dead"] = dead
        state.setdefault(cursor, 0)
        if "--persona" in argv:
            idx = int(argv[argv.index("--persona") + 1])
        else:
            # A provider whose daily quota is gone answers nothing for hours; its
            # personas are skipped, not run, and the slot goes to the next live
            # one. The skip is recorded so the day's counts stay explicable. The
            # day boundary is the provider's, not ours (groq was alive again by
            # 02:49 UTC on 18 Sep after "used 199178 of 200000" at 00:13; Gemini
            # resets at midnight Pacific), so a dead mark expires after
            # QUOTA_PROBE_SECONDS and the next persona on that provider is the
            # probe: it either runs, or re-marks the provider for another spell.
            # A provider at our own daily cap is skipped the same way: before
            # 18 Sep 19:30 its personas ran, returned "(daily cap reached)"
            # in 0 s, and were filed as failed tasks -- 45 of them for NVIDIA
            # in three hours, dragging the build and OpenCode rows.
            calls = state.get("provider_calls_today", {}) if state.get("provider_day") == today else {}
            tokens = state.get("provider_tokens_today", {}) if state.get("provider_day") == today else {}

            def _capped(i: int) -> bool:
                """Whether this persona's provider is spent for the day.

                Every persona shares one budget, and the cheap lanes run every
                minute while an OpenCode run takes four -- so on 22 Sep the
                light lane reached nvidia's cap at 20:06 and the OpenCode
                twins, the ones the current experiment is about, got no runs
                at all between 17:16 and midnight. The reserve fixes the
                queueing, not the caps: the last slice of each provider's day
                is spendable only by the twins.
                """
                provider = PERSONAS[i][2]
                cap, token_cap = effective_caps(provider, PERSONAS[i][1])
                return calls.get(provider, 0) >= cap or tokens.get(provider, 0) >= token_cap

            skipped = 0
            members = len(lane_personas(lane))
            idx = persona_index(state[cursor], lane)
            last_oc = float(state.get("last_opencode_at") or 0)
            def _too_soon(i: int) -> bool:
                return (PERSONAS[i][1] == "opencode"
                        and 0 < time.time() - last_oc < OPENCODE_MIN_GAP_SECONDS)
            while (PERSONAS[idx][2] in dead or _capped(idx) or _too_soon(idx)) and skipped < members:
                if _too_soon(idx):
                    log(f"skip {PERSONAS[idx][0]}: another OpenCode run less than "
                        f"{OPENCODE_MIN_GAP_SECONDS // 60} min ago (memory)")
                elif _capped(idx):
                    reserved = "" if PERSONAS[idx][1] == "opencode" else " (OpenCode reserve)"
                    log(f"skip {PERSONAS[idx][0]}: {PERSONAS[idx][2]} at its daily cap{reserved}")
                else:
                    log(f"skip {PERSONAS[idx][0]}: {PERSONAS[idx][2]} daily quota gone; next probe after "
                        f"{QUOTA_PROBE_SECONDS // 60} min")
                state[cursor] += 1
                state["skipped_for_quota_today"] = state.get("skipped_for_quota_today", 0) + 1
                skipped += 1
                idx = persona_index(state[cursor], lane)
            if skipped >= members:
                _save(state)
                log("nothing runnable this tick (quota, cap or the OpenCode gap)"); return 0
            state[cursor] += 1
        state.setdefault("provider_calls_today", {})
        if state.get("provider_day") != today:
            state["provider_day"], state["provider_calls_today"], state["provider_tokens_today"] = today, {}, {}
        run_index = state["runs_today"]
        used_today = dict(state["provider_calls_today"])
        # the slot is taken: the other lane, or the next tick, moves on
        state["runs_today"] += 1
        _save(state)

    reporter, path, provider, asks, workload = PERSONAS[idx]
    if path == "opencode":
        with _state_lock():
            st = _state()
            # The gap consumes whichever OpenCode persona the lane cursor
            # reaches, and on 20 Sep that was the same pair every time: the
            # proxy pair did not run between 10:46 and 18:44 while the other
            # pair ran nine times. The OpenCode personas take their turns on
            # a cursor of their own, so a gap costs frequency, not fairness.
            order = [i for i, p in enumerate(PERSONAS) if p[1] == "opencode"]
            turn = int(st.get("next_opencode") or 0)
            idx = order[turn % len(order)]
            st["next_opencode"] = turn + 1
            st["last_opencode_at"] = time.time()
            _save(st)
        reporter, path, provider, asks, workload = PERSONAS[idx]
    run = Run(reporter, path, provider, asks)
    started = time.monotonic()
    task = None
    if provider is None:
        answer = run.run_cron(workload)
    elif path == "builder":
        task = workload[(run_index // len(PERSONAS)) % len(workload)]
        answer = run.run_build(task, run_index=run_index, used_today=used_today.get(provider, 0))
    elif path == "opencode":
        task = workload[(run_index // len(PERSONAS)) % len(workload)]
        answer = run.run_opencode(task, run_index=run_index, used_today=used_today.get(provider, 0))
    else:
        task = workload[(run_index // len(PERSONAS)) % len(workload)]
        answer = run.run_model(task, run_index=run_index, used_today=used_today.get(provider, 0))
    run.fe.flush(timeout=20)
    run.save_etags()
    # Did the run do its job? A model run: a real answer, not a parenthesised
    # failure. A cron run: every call eventually succeeded. A builder: the
    # task came out done. The one number a user of an agent cares about.
    if run.build is not None:
        run.completed = bool(run.build.get("task_done"))
    elif run.opencode is not None:
        run.completed = bool(run.opencode.get("completed"))
    elif provider is None:
        run.completed = all(f["recovered"] for f in run.failures)
    else:
        # An answer, not a parenthesised failure -- and not nothing. An empty
        # reply was counted as a finished run until 23 Sep; the grader then
        # marked it wrong, which is how it was found.
        run.completed = bool((answer or "").strip()) and not answer.startswith("(")

    record = {"at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "reporter": reporter, "path": path,
              "provider": provider, "model": getattr(run, "model", None), "asks": asks, "tool_calls": run.tool_calls, "model_calls": run.model_calls,
              "failures": run.failures, "seconds": round(time.monotonic() - started, 1),
              "metrics": {"tokens_prompt": run.tokens_prompt, "tokens_completion": run.tokens_completion,
                          "asks": run.asks_made, "ask_seconds": round(run.ask_seconds, 3),
                          "wait_seconds": round(run.wait_seconds, 2), "completed": run.completed,
                          "model_retries_after_skip": run.model_retries_after_skip,
                          "calls_first_try": run.tool_calls - sum(f["attempts"] for f in run.failures),
                          "calls_recovered": sum(1 for f in run.failures if f["recovered"]),
                          "calls_failed": sum(1 for f in run.failures if not f["recovered"])}}
    if run.build is not None:
        record["build"] = run.build
        record["task"] = task[:160] if path == "builder" else None
        record["answer"] = answer[:200]
    if any(m in answer for m in _NOT_A_RUN):
        record["answer"] = answer[:200]
    if run.opencode is None and run.build is None and isinstance(task, str):
        # The light lane answers in prose, and for three years' worth of runs
        # "completed" meant only that the model said something. Grade the
        # sentence against truth fetched from the same APIs the task names --
        # here, before the answer is truncated for storage.
        graded = (grade(task, answer, bool(run.completed))
                  if not any(m in (answer or "") for m in _NO_ANSWER)
                  else {"checked": 0, "valid": None})
        if graded["checked"] or graded["valid"] is not None:
            record["graded"] = graded
            # The task and the answer are kept with the truth the grade used,
            # so a fixed grader can grade this run again (see _current_grade).
            # Our own task, our own model, our own ledger. Prose answers are
            # asked to stay under sixty words; the cap is far above that, and
            # a run that hits it is not regraded, because a cut answer would.
            record["task"] = task[:200]
            record["answer"] = answer[:ANSWER_KEEP]
    if run.opencode is not None:
        # what it actually produced, checked against truth fetched from the
        # same public APIs the task names (see grading.py)
        record["graded"] = grade(task[0] if task else "",
                                 run.opencode.get("result_text") or run.opencode.get("result_head") or "",
                                 bool(run.opencode.get("completed")))
        record["opencode"] = {k: run.opencode.get(k) for k in ("completed", "tool_calls", "failecho_calls", "advice_seen", "tool_failures", "idle_after_result", "guest_oom_kills",
                                                                "mcp_log", "steps", "exit",
                                                                "error", "tool_names", "result_head", "timed_out")}
        record["task"] = task[0][:160]
        record["answer"] = answer[:200]

    # -- file, under the lock, on a fresh copy: the other lane may have written
    with _state_lock():
        state = _state()
        state.setdefault("provider_calls_today", {})
        if provider:
            state["provider_calls_today"][provider] = state["provider_calls_today"].get(provider, 0) + run.model_calls
            state.setdefault("provider_tokens_today", {})
            state["provider_tokens_today"][provider] = (state["provider_tokens_today"].get(provider, 0)
                                                        + run.tokens_prompt + run.tokens_completion)
        state.setdefault("provider_dead", {})
        if provider and run.provider_dead_today:
            state["provider_dead"][provider] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            log(f"  {provider}: daily quota gone; its personas are skipped for {QUOTA_PROBE_SECONDS // 60} min")
        elif provider and provider in state["provider_dead"]:
            del state["provider_dead"][provider]
            log(f"  {provider}: quota is back")
        state.setdefault("runs", []).append(record)
        if len(state["runs"]) > LEDGER_CAP:
            _archive(state["runs"][:-LEDGER_CAP])
            state["runs"] = state["runs"][-LEDGER_CAP:]
        _save(state)
        write_report(state)

    log(f"{reporter} [{path}/{provider or 'none'}/{'ask' if asks else 'blind'}] "
        f"{run.tool_calls} tool calls, {run.model_calls} model calls, {len(run.failures)} failures, "
        f"sent={run.fe.sent} failed={run.fe.failed}, {record['seconds']}s")
    for f in run.failures:
        log(f"  {f['service']} {f['operation']} {f['error_type']}{'/' + f['error_code'] if f['error_code'] else ''}"
            f" asked={f['asked']} rec={f['recommended']} attempts={f['attempts']} recovered={f['recovered']}")
    if run.build is not None:
        b = run.build
        log(f"  build: vm_runs={b['vm_runs']} local={b['local_failures']} shared={len(b['shared_failures'])} "
            f"done={b['task_done']} sandbox={b['sandbox']}")
    log(f"  answer: {answer[:120]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
