"""Private mode: a team's own failure evidence, shared with nobody.

The public network needs other agents to be useful. A team with a private
staging environment, an internal API, or a compliance answer to give has
neither and still has the original problem: its agents retry blindly into
failures its other agents already solved yesterday.

Private mode is the same machinery pointed at one team. Send
``X-FailEcho-Team: <secret>`` and the report is stored apart, answered back to
that team alone, and never pooled, counted, aggregated or shown to anyone
else. A query with the token gets the public answer *plus* what the team's own
agents have seen -- and when the public network has nothing, the team's own
evidence can carry a recommendation on its own, marked ``scope: "team"``.

Three properties, each enforced rather than described (tests/test_private.py):

* **A private row never reaches a public number.** Not by policy -- by table.
  Everything public reads ``observations``; private rows are not in it.
* **A team sees its own evidence and nothing else.** The token is hashed with
  the same salt as a reporter id and compared as a hash; there is no listing
  endpoint and no way to ask for a team you do not have the token for.
* **There is no account.** The token is self-chosen, like ``reporter_id``,
  just secret. Nothing is issued, nothing is billed, nothing to leak. Losing
  the token loses the evidence, which is stated wherever it is offered.

Free while the network bootstraps (``FIN_PRIVATE_MODE_OPEN``). It is the first
thing in ``docs/business-model.md`` that would ever be paid for, and it is
useful at zero independent reporters, which is exactly where FailEcho is.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import ago
from app.core.config import settings
from app.core.intelligence import _cap, wilson_lower_bound
from app.db.models import PrivateObservation, PrivateRecoveryOutcome

#: Minimum length of a team token. A short token is a guessable one, and the
#: whole guarantee here is that nobody else has it.
MIN_TOKEN_LENGTH = 16


def hash_team(token: str | None) -> str | None:
    """Salt and hash a team token. The token itself is never stored.

    Returns None for no token (the normal, public case) and for a token too
    short to be a secret -- refusing quietly would give a team the feeling of
    privacy without it, so the caller turns this into a 400.
    """
    if token is None:
        return None
    value = token.strip()
    if not value:
        return None
    digest = hashlib.sha256(f"team:{settings.reporter_salt}:{value}".encode("utf-8"))
    return digest.hexdigest()[:32]


def token_is_usable(token: str | None) -> bool:
    return token is None or len(token.strip()) >= MIN_TOKEN_LENGTH or not token.strip()


def _weigh(rows, recent_since) -> tuple[list[dict], dict | None]:
    """Per-action counts for one team and one failure, weighed the way the
    public network weighs its own.

    One agent's retry storm counts at most ``max_reporter_weight_per_hour``
    attempts an hour, successes scaled down with it -- so a team of two cannot
    have one agent's loop decide a recommendation, or a skip, on its own.
    """
    cap = settings.max_reporter_weight_per_hour
    buckets: dict[tuple, list[int]] = {}
    recent_buckets: dict[tuple, list[int]] = {}
    raw: dict[str, list[int]] = {}
    for action, reporter, created, successful in rows:
        hour = created.replace(minute=0, second=0, microsecond=0)
        key = (action, reporter, hour)
        for target in (buckets,) + ((recent_buckets,) if created >= recent_since else ()):
            bucket = target.setdefault(key, [0, 0])
            bucket[0] += 1
            bucket[1] += int(bool(successful))
        counts = raw.setdefault(action, [0, 0])
        counts[0] += 1
        counts[1] += int(bool(successful))

    def capped(source):
        out: dict[str, list[int]] = {}
        for (action, _, _), (n, ok) in source.items():
            a, s = _cap(n, ok, cap)
            total = out.setdefault(action, [0, 0])
            total[0] += a
            total[1] += s
        return out

    capped_all, capped_recent = capped(buckets), capped(recent_buckets)
    actions = []
    for action, (attempts, successes) in raw.items():
        ca, cs = capped_all.get(action, [0, 0])
        actions.append({"action": action, "attempts": attempts, "successes": successes,
                        "capped_attempts": ca, "capped_successes": cs,
                        "confidence": round(wilson_lower_bound(cs, ca), 4) if ca else 0.0})
    actions.sort(key=lambda a: (a["confidence"], a["attempts"]), reverse=True)

    recent_attempts = sum(a for a, _ in capped_recent.values())
    recent_successes = sum(s for _, s in capped_recent.values())
    skip = None
    if recent_attempts >= settings.min_recovery_attempts and recent_successes == 0:
        skip = {"action": "skip",
                "confidence": round(min(wilson_lower_bound(recent_attempts, recent_attempts),
                                        settings.max_confidence), 4),
                "scope": "team", "from_other_agents": False,
                "based_on_attempts": recent_attempts}
    return actions, skip


async def team_evidence(session: AsyncSession, team_hash: str,
                        fingerprint: str | None, service: str, operation: str) -> dict | None:
    """What this team's own agents have seen for this failure.

    Shaped like the public evidence so a caller reads it the same way, with
    one difference stated in the payload: it is `private`, so it is never
    somebody else's experience and never proof about the service at large.
    """
    window = ago(settings.private_retention_days * 86400)

    totals = (
        await session.execute(
            select(
                func.count().label("total"),
                func.sum(case((PrivateObservation.outcome == "failure", 1), else_=0)),
            ).where(
                PrivateObservation.team_hash == team_hash,
                PrivateObservation.service == service,
                PrivateObservation.operation == operation,
                PrivateObservation.created_at >= window,
            )
        )
    ).one()
    total = int(totals[0] or 0)
    failures = int(totals[1] or 0)

    actions: list[dict] = []
    skip = None
    if fingerprint:
        rows = (
            await session.execute(
                select(
                    PrivateRecoveryOutcome.action,
                    PrivateRecoveryOutcome.reporter_hash,
                    PrivateRecoveryOutcome.created_at,
                    PrivateRecoveryOutcome.successful,
                ).where(
                    PrivateRecoveryOutcome.team_hash == team_hash,
                    PrivateRecoveryOutcome.fingerprint == fingerprint,
                    PrivateRecoveryOutcome.created_at >= window,
                )
            )
        ).all()
        actions, skip = _weigh(rows, recent_since=ago(settings.decay_window_seconds))

    if not total and not actions:
        return None

    # The same bar as the public network, on the same capped counts: five
    # attempts at 60% for a fix, and -- the verdict a small team needs most --
    # "skip" when at least five recent attempts all failed. Without it a team
    # that kept hitting an exhausted quota was never told to stop (23 Sep: all
    # eight of the lab team's Stack Exchange calls failed on its first run).
    recommendation = None
    for action in actions:
        if (action["capped_attempts"] >= settings.min_recovery_attempts
                and action["capped_successes"] / action["capped_attempts"] >= settings.min_recovery_success_rate):
            recommendation = {
                "action": action["action"],
                "confidence": action["confidence"],
                "scope": "team",
                # Never "other agents": it is this team's own history, and
                # saying otherwise would be the one lie this project cannot
                # afford.
                "from_other_agents": False,
            }
            break
    if recommendation is None and skip is not None:
        recommendation = skip

    return {
        "private": True,
        "observations": total,
        "failures": failures,
        "recovery_actions": [{k: a[k] for k in ("action", "attempts", "successes", "confidence")}
                             for a in actions[:5]],
        "recommendation": recommendation,
        "window_days": settings.private_retention_days,
    }
