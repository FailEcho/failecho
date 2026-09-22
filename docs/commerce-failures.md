# Browser and commerce failures: the taxonomy, before any code

Written 22 September 2026. Nothing here is built. It exists so that if an
agent-shopping or browser-automation surface is worth doing, the naming and the
privacy contract are decided first -- the two things that are expensive to
change once evidence exists under them.

Why this surface is interesting: an agent buying or searching on the web fails
far more often than an agent calling an API, and its failures repeat across
*everyone* on the same day. A merchant's checkout that breaks for agents breaks
the same way for every agent until it is fixed. That is the shape FailEcho's
fingerprint and recovery model were built for.

Why it is dangerous: the page holds carts, addresses, prices and payment
details. The default HTTP contract (host plus first path segment) is already
too wide here -- `bank.example.com/alice-smith` is an identifier. This surface
needs a narrower contract, decided before the first report.

## Naming

| Field | Value | Why |
|---|---|---|
| `service` | the registrable domain, `example.com` | Never the full URL. A path is where identifiers live |
| `operation` | one step from the controlled vocabulary below | Free text fragments evidence the way `github_repo` and `GET /repos` already do |
| `version` | **structure hash**: a hash of the page's form field names and button roles | The commerce equivalent of `schema_hash`. It is what lets the network say "this merchant's checkout changed 40 minutes ago", the most valuable signal on this surface |

**Operation vocabulary** (extend by decision, not by accident):
`search.query`, `product.open`, `cart.add`, `cart.open`, `checkout.start`,
`checkout.address`, `checkout.shipping`, `checkout.payment`, `checkout.confirm`,
`auth.login`, `auth.2fa`, `account.open`.

## Failure classes, split by whose failure it is

**Site-side -- shareable.** These are the merchant's, they generalise, and
another agent hitting the same page benefits from knowing.

| Class | What it is |
|---|---|
| `element_missing` | The step's element is not there: the page changed |
| `navigation_timeout` | The page never settled |
| `consent_wall` | A cookie or consent overlay blocks the flow |
| `auth_wall` | The step needs a signed-in session |
| `bot_challenge` | Anti-bot protection stopped the agent |
| `out_of_stock` | The item cannot be bought right now |
| `price_changed` | The price moved between steps |
| `payment_auth_required` | A 3-D Secure or SCA step the agent cannot complete |
| `server_error`, `rate_limit` | The existing classes, unchanged |

**Account-side -- never shared, private mode only.** These are about one
person's card, address or session. They do not generalise, and pooling them
would be invasive and useless at once: `payment_declined`,
`insufficient_funds`, `address_rejected`, `session_expired`.

That split is the design decision to keep: **everything shared is about the
merchant's site; everything about a person stays home.**

## Recovery vocabulary

`reload`, `wait_and_retry`, `dismiss_overlay`, `re_authenticate`,
`switch_payment_method`, `use_site_api`, `human_handoff`, `abandon`.

**One rule in the code, not only in the documentation:** a `bot_challenge`
never recommends anything but `use_site_api`, `human_handoff` or `abandon`.
FailEcho must not become a way around merchants' bot protection. That would be
evasion, it would get the network blocked by the sites it depends on, and it is
not what the evidence supports anyway. "This site refuses agents; use their API
or stop" is useful advice and is honest.

## Privacy contract for this surface

**Sent:** registrable domain; operation from the vocabulary; error class; HTTP
status when there is one; duration; structure hash; two booleans (overlay
present, challenge present).

**Never sent:** full URLs, query strings, page text, screenshots, form values,
cookies, session tokens, order ids, amounts, item names, selectors.

Selectors are the tempting exception, because "the button moved" is exactly
what one agent wants to tell another. The compromise: send a hash of the
selector by default, and the selector itself only when the reporter is the
site's own operator reporting about their own site.

## The adapter, when it is built

Wrap the page object so failures are caught where they happen, with the same
three properties the Python wrapper has (never raises, never blocks, never
changes behaviour):

    page = failecho.watch(page, site="example.com", flow="checkout")
    page.click("#pay")     # on failure: classify, report shape, attach advice

The advice arrives on the exception the agent already handles, exactly as it
does in `failecho-autoreport`.

## Staging

1. **This document.** Naming, classes, privacy contract.
2. **Adapter in private mode only**: a team's own evidence, nothing shared.
   Useful at zero independent reporters, and it is the first paid product in
   `docs/business-model.md`.
3. **Share site-side classes publicly**, once the taxonomy has survived contact
   with real sites and the structure hash proves stable.
4. **Merchant report last**: "agents fail at your checkout 34% of the time,
   mostly at `checkout.payment`, since your build changed at 14:10". The
   merchant's incentive is to fix it, which is the aligned-buyer product.

## What would make this a mistake

Building the adapter before anyone is asking for it. The blocker for FailEcho
is 0 independent reporters, not a missing surface. This document costs an hour
and keeps the option open; the adapter costs a day and only pays when there is
someone to use it.
