# How FailEcho could make money, and what has to be true first

Written 22 September 2026, after the outside review and the first releases;
revised the same day after a second reviewer argued with it. This is an
argument, not a plan of record. Lab numbers are our own agents'
(`docs/report-48h.md`); production has **0 independent reporters**.

**What FailEcho is, in one paragraph.** A shared reliability-intelligence
network for AI agents and tools. Independent agents contribute privacy-safe
failure and recovery evidence. FailEcho turns that evidence into incident
detection, proven recovery strategies, and eventually real-time reliability
intelligence. Not a marketplace for error data.

## Two bootstraps, run in parallel

The first version of this document said "independent reporters first, nothing
has value until that moves", and then said private team networks are useful at
zero reporters. Both are true because they are different problems:

    PUBLIC NETWORK BOOTSTRAP          BUSINESS BOOTSTRAP
    independent reporters             one team's own agents
    -> corroboration                  -> repeated failures
    -> incidents                      -> recovery history
    -> cross-provider intelligence    -> team intelligence
    -> reliability feed               -> first paying customer

The public network is the moat and takes outsiders. The private product needs
nobody: five recoveries and an agent starts getting its own evidence back.
Running them in parallel is also safer -- revenue does not wait on adoption we
do not control.

## What exists already

Ingestion with auth and rate limits, schema validation, privacy and
normalization, fingerprints, the recovery engine, confidence scoring, the query
API, and three client paths (Claude Code plugin, Python wrapper, MCP proxy).

**The denominator is not missing, which the second reviewer assumed.** Rates
need attempts, not just failures, and successes have been reported since the
beginning: `report_tool_success` in the MCP tools, `report_success` in the
wrapper (on by default), and the proxy reports every successful tool call. The
lab holds **61,425 successes against 28,110 failures**, and `/v1/query` returns
a failure rate computed from both, per fingerprint and per hour.

What *is* missing around it:

| Missing | Why it matters |
|---|---|
| **Aggregate counters** (`attempts/successes/failures` per 5-minute window) as an alternative to per-call success reports | Cheaper for high-traffic callers, who will not send one report per request |
| **Coverage flag** | A reporter that sends only failures must not be silently mixed into a rate. The answer must say what share of the denominator it actually has |
| **Version and environment breakdown** | "Provider A is 99.2%" is meaningless across versions and regions |
| **Persistent reporter identity** | Below |
| **Incident detection** | Correlated spikes across *independent* reporters |
| **Reliability history** | Rates over time, per provider/operation/version |

Never publish a reliability percentage from a denominator we do not have. That
rule goes in the API response, not only in the documentation.

## Provenance before reputation

A reputation algorithm on top of unstable identity is worthless. Today
`reporter_id` is optional and a restart mints a new one, so "unique reporters"
counts processes.

1. **Persist the id.** On first run, generate one and store it
   (`~/.config/failecho/reporter`), so a restart is the same reporter. Small,
   and it removes the accidental inflation.
2. **Sign observations.** Ed25519 keypair at install; `reporter_id =
   hash(public key)`; observations signed. Now an id cannot be borrowed, and
   "independent" means something checkable.
3. **Then layer identities**: installation, organization, integration --
   "Reporter a91f..., Acme Corp, Claude MCP proxy, organization verified".
4. **Only then reputation**, weighting evidence by corroboration and history.

## The layers, in order of value

    L1  Known failure        "have we seen this?"
    L2  Recovery             "what actually fixed it?"          <- we are here
    L3  Incident             "is this happening everywhere, now?"
    L4  Reliability          "how reliable is this operation, this version?"
    L5  Routing              "what should my agent use right now?"

L5 is the biggest business and the one FailEcho should *not* build into a
router. It supplies `P(success | provider, operation, version, time,
environment)`; other people build routing on top. That is a clean
infrastructure position and it keeps us out of the traffic path.

## What must not be sold

Contributors send metadata under a plain promise: no prompts, arguments,
results or bodies; reads store nothing; error text off by default. **Selling
their records, even aggregated, breaks that promise** -- the terms did not say
so when the data arrived. What can be sold is what the network *computes*, which
no contributor owns.

It is also the only thing worth money. A raw record is a commodity:
`HTTP 429`, `connection reset`, `schema mismatch`. This is not:

    tool X, operation Y, version Z
    incident started 7 minutes ago
    842 observations from 31 independent reporters
    failure probability 31%
    likely recovery: refresh_schema, observed 91.4% across 5 organizations

That took a network to make.

## Why paying for error data is the wrong first move

1. **It pays for what cannot be verified.** Once data is worth money, the
   cheapest way to earn is to invent plausible failures and confirm plausible
   recoveries -- the exact input recovery rates are computed from.
2. **Buyers will ask how you stop it.** "We pay per report" ends the sale.
3. **It solves the wrong shortage**: not volume, but *trusted* volume from
   agents doing real work.

If contributors are ever paid, pay per **verified recovery** -- failure and fix
corroborated by independent reporters, capped per reporter, paid after a delay
-- which is an anti-fraud system to build and run. Later, funded. Crypto is the
same problem plus custody and regulation, and a token would make every counter
look like an incentive to inflate it.

## What could sell

| Product | Buyer | Why they pay | Needs first |
|---|---|---|---|
| **Private team network** | Teams with repeatable API workflows | Useful at zero independent reporters; their own evidence, private | Auth, tenancy, namespaces |
| **Incidents** (free) | Everyone | Most shareable signal; makes the network visible | Corroboration across independent reporters |
| **Provider reliability API** | Routers, agent platforms, gateways | They route real traffic and have no cross-vendor view | Denominator coverage, history, provenance |
| **Vendor claim profile** | API vendors, model providers | Their incentive is aligned: they want the profile accurate | The above |
| **Hosted or supported self-host** | Companies that send nothing outside | Runs and stays patched | Packaging, upgrade path |

**On the vendor product, "the right to correct it" was wrong.** A vendor must
never edit observed history. They get a claim profile: identify official
versions, mark deprecated operations, explain an incident, post a resolution
notice, publish a status endpoint, challenge an obviously invalid
classification. The two stay separate and both are shown:

    Network observed: failure spike, 28%, since 18:05 UTC
    Vendor statement: "regression in v2.7.1, fixed in v2.7.2 at 18:42 UTC"

That is more trustworthy than either alone.

## The order

    0. Private mode: one team's own evidence, useful immediately
    1. Persistent reporter identity (persist, then sign)
    2. Independent reporters: plugin, wrapper, proxy -- count organizations
    3. Denominator coverage: aggregate counters, and a coverage flag
    4. Incident engine, free and public
    5. Recovery intelligence under conditions (version, region, time)
    6. Private team product -- charge here first
    7. Reliability history per provider/operation/version/environment
    8. Provider reliability API
    9. Routing intelligence, as data, not as a router

Revenue arrives at 6, long before the moat at 8.

## The next milestone, deliberately narrow

**Prove that a failure reported by one independent agent helps a different
independent agent recover.** Not revenue, not dashboards, not more
integrations. The demonstration has to show:

- reporter A != reporter B, in different organizations;
- the same failure fingerprint matched;
- the recommendation derived from A's evidence;
- B recovered after following it;
- no request content needed at any point.

Early measures, as evidence rather than targets:

| | Now | Wanted |
|---|---|---|
| Independent organizations | 0 | 5 |
| Persistent reporters | 0 | 20 |
| Independent observations | 0 | 500+ |
| Failures corroborated by 2+ reporters | 0 | >20% |
| Failures where FailEcho helped another reporter recover | 0 | 10 |
| Recovery suggestions confirmed by a second organization | 0 | 5 |

Those are the numbers that show the network doing something a local error log
cannot.

## What would kill it

Selling contributor records, or being seen to. Paying per report. A token.
Publishing a reliability percentage without the denominator behind it. Any
claim the measurements do not support -- general task-completion uplift,
guaranteed secret exclusion, verified independent consensus
(`docs/review-2026-09-20.md` keeps that list).
