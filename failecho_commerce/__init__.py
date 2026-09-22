"""Commerce failures: the naming and classes from docs/commerce-failures.md,
as code, so the taxonomy can be tested before an adapter is built.

Nothing here drives a browser. A browser costs 400-700 MB and the fleet's
host does not have it to spare, and the uncertain part is not the driving: it
is whether these failures fingerprint stably, whether the structure hash
notices a checkout changing, and whether the split between the merchant's
failures and a person's holds up. All of that can be tested over plain HTTP.

Two rules from the document are enforced here rather than described:

* **Account-side classes are never shared.** A declined card is about one
  person, it does not generalise, and pooling it would be invasive and
  useless at once. `shareable()` is the gate.
* **A bot challenge never recommends a way around itself.** The only honest
  advice is the site's API, a human, or stopping. Evasion would get the
  network blocked by the sites it depends on.
"""

from __future__ import annotations

import hashlib
import re

#: The step vocabulary. Free text would fragment the evidence the way
#: `github_repo` and `GET /repos` already do for APIs.
OPERATIONS = (
    "search.query", "product.open", "cart.add", "cart.open",
    "checkout.start", "checkout.address", "checkout.shipping",
    "checkout.payment", "checkout.confirm", "auth.login", "auth.2fa",
    "account.open",
)

#: The merchant's failures: they generalise, so they are worth sharing.
SITE_SIDE = ("element_missing", "navigation_timeout", "consent_wall", "auth_wall",
             "bot_challenge", "out_of_stock", "price_changed", "payment_auth_required",
             "server_error", "rate_limit")

#: One person's card, address or session. Private mode only, never pooled.
ACCOUNT_SIDE = ("payment_declined", "insufficient_funds", "address_rejected", "session_expired")

#: What an agent may do next. `use_site_api`, `human_handoff` and `abandon`
#: are the only answers to a challenge.
RECOVERIES = ("reload", "wait_and_retry", "dismiss_overlay", "re_authenticate",
              "switch_payment_method", "use_site_api", "human_handoff", "abandon")
CHALLENGE_RECOVERIES = ("use_site_api", "human_handoff", "abandon")

_FIELD = re.compile(r'<(?:input|select|textarea)[^>]*name=["\']([^"\']+)', re.I)
_BUTTON = re.compile(r'<button[^>]*id=["\']([^"\']+)', re.I)


def shareable(error_type: str) -> bool:
    """Whether this failure may leave the machine at all."""
    return error_type in SITE_SIDE


def structure_hash(html: str) -> str:
    """A fingerprint of the page's shape: field names and button ids, sorted.

    This is the commerce equivalent of `schema_hash`. It is what lets the
    network say "this merchant's checkout changed 40 minutes ago", which is
    the most valuable thing one shopping agent can tell another. Text,
    prices and ids are not part of it -- only the shape.
    """
    fields = sorted(set(_FIELD.findall(html or "")))
    buttons = sorted(set(_BUTTON.findall(html or "")))
    material = "|".join(fields) + "#" + "|".join(buttons)
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def classify_page(status: int, html: str, waited: float = 0.0) -> str:
    """The failure class for a step that did not work."""
    text = (html or "").lower()
    if status == 403 and ("robot" in text or "challenge" in text or "captcha" in text):
        return "bot_challenge"
    if status in (401, 403):
        return "auth_wall"
    if status == 429:
        return "rate_limit"
    if status >= 500:
        return "server_error"
    if "out of stock" in text or "sold out" in text:
        return "out_of_stock"
    if "accept cookies" in text or "consent" in text:
        return "consent_wall"
    if waited and status == 0:
        return "navigation_timeout"
    return "element_missing"


def advice_for(error_type: str, evidence: dict | None = None) -> str | None:
    """The recovery to suggest, refusing to suggest evasion for a challenge."""
    if error_type == "bot_challenge":
        return "use_site_api"
    if error_type in ACCOUNT_SIDE:
        return None            # the agent's own problem; the network has nothing
    if error_type in ("server_error", "navigation_timeout", "rate_limit"):
        return "wait_and_retry"
    if error_type == "consent_wall":
        return "dismiss_overlay"
    if error_type == "auth_wall":
        return "re_authenticate"
    if error_type == "out_of_stock":
        return "abandon"
    if error_type == "element_missing":
        # the shape changed: reloading will not bring the old button back
        return "reload" if not (evidence or {}).get("structure_changed") else "human_handoff"
    return None


def report_for(domain: str, operation: str, error_type: str, html: str, status: int | None = None) -> dict | None:
    """The report that would be sent, or None when nothing may be.

    Everything the document forbids is absent by construction: no URL beyond
    the domain, no page text, no selectors, no form values, no amounts.
    """
    if not shareable(error_type):
        return None
    if operation not in OPERATIONS:
        raise ValueError(f"operation {operation!r} is not in the vocabulary")
    report = {
        "service": domain.lower(),
        "operation": operation,
        "error_type": error_type,
        "version": structure_hash(html),
        "outcome": "failure",
    }
    if status:
        report["error_code"] = str(status)
    return report
