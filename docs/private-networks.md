# Public network vs private network

Written 2026-09-14. A design note, not a shipped feature. Nothing here is
built except self-hosting, which already works.

## The difference is the threat model, not the data

On the public network, anyone can query any fingerprint. That is the whole
point — evidence is only worth something if the next agent can read it — and
it decides everything about what is safe to send.

So the public client is built to **not send** things:

- `plugin/hooks/failecho_hook.py:136` refuses to name a service unless its
  host is public. Bare IPs are dropped ("an address says where, not what"),
  and so is anything ending `.local`, `.internal`, `.corp`, `.lan`, `.private`
  and friends.
- Error text is omitted unless `FAILECHO_HOOK_SEND_ERRORS=1`.
- Reporter IDs are hashed with a server-side salt before storage.
- Unknown fields are dropped by the schema before a handler sees them.

That is the right design for a public network and it throws away most of what
an operator actually wants. **Your internal services are the ones you most
need failure intelligence about, and they are exactly the ones the public hook
refuses to name.** `payments-api.internal` timing out at 03:00 is the most
useful failure in your estate, and today the hook silently skips it.

On a private network the audience is one organisation, so:

| | Public network | Private network |
|---|---|---|
| Who can read it | anyone | one organisation |
| Privacy comes from | not sending | isolation |
| Internal hostnames | skipped entirely | the main point |
| Error text | opt-in, normalized, raw discarded | safe to keep in full |
| Reporter identity | hashed, optional | can be a real team or host name |
| Value of a fingerprint | needs strangers to match it | your own fleet matching itself |

A private network is not "the same thing, hidden". It is a **different and
more useful product**, because it can record the things the public one is
deliberately blind to.

## Three models, only one of which exists

### 1. Self-hosted — works today, MIT, free

Run the whole thing. Own database, own salt, own network. Nothing reports
outward. This is already possible and already documented in `SECURITY.md`.

The one thing to get right: **generate your own `FIN_REPORTER_SALT`.** With a
different salt, the same reporter ID hashes differently on your instance than
on ours, so the two datasets cannot be correlated even if both leaked.

Also worth changing for an internal deployment: relax `public_host()` so
internal suffixes are allowed. On a private instance that check is not
protecting anyone, it is only deleting your own data.

### 2. Hosted private tenant — the paid idea, not built

We run it, the data is isolated per tenant. What makes this hard is not the
hosting, it is three invariants that have to hold architecturally rather than
by convention:

- **One-way only.** A tenant may read the public corpus. The public may never
  read a tenant. If that is ever enforced by a `WHERE` clause someone can
  forget, it is not enforced.
- **Private traffic must never count as public adoption.** `real_observations
  _total` is the one number this project promises is honest. Tenant writes
  need their own source label or their own database — not a flag on a shared
  row.
- **Separate salts per tenant**, or the hashes are comparable across tenants.

### 3. Hybrid: private writes, public reads — the interesting one

A tenant writes only to its own store, but *queries* fall through to the
public corpus when the private one has nothing. This is the version worth
building, because it fixes the cold-start problem that otherwise makes a
private network useless on day one: a fresh tenant with four agents has no
evidence about anything, and the public network might.

It is also the version that justifies a price, because the tenant gets
something it cannot self-host: everyone else's evidence.

## What this means for pricing

The earlier idea was $0.002 per call for private-network access.

The honest problem with charging for it **today**: the thing a paying tenant
would be buying is access to the public corpus, and the public corpus is
empty. Right now a private tenant gets isolation they could have for free by
self-hosting, and a fall-through query that returns nothing.

So the sequence is forced:

1. Public network gets real evidence in it (the current bottleneck).
2. Fall-through querying becomes worth something.
3. *Then* a private tier has something to sell.

Selling it before step 1 means selling hosting, and hosting a free MIT
application is a weak business against `docker run`.

## What would have to change in the code

Rough, for when this is real:

- `public_host()` gated on a setting rather than hardcoded, so a private
  deployment can name its own hosts.
- A tenant identity on every write and every read, enforced at the session or
  connection level rather than in query bodies.
- Separate `real_observations_total` accounting, so a tenant's traffic can
  never inflate the public adoption number.
- A per-tenant salt, generated once, stored like the current one.
- Rate limiting per tenant rather than per client IP.

None of that is large. All of it is easy to get subtly wrong, which is why it
should wait until there is a reason to build it.
