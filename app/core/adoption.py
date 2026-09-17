"""Which reporters count as independent agents.

Every `agent` row is stored and used as evidence. Not every `agent` row is
adoption. A reporter counts as an independent agent on the front page once
it looks like one: at least ``adoption_min_observations`` observations,
across at least ``adoption_min_services`` distinct services, spanning at
least ``adoption_min_span_seconds`` from first to last. Anonymous rows (no
reporter id) never establish adoption; they still count as evidence.

The threshold exists because of 2026-09-16: a fuzzer sent eleven junk rows
under eight fresh reporter ids and the front page said "11 independent
observations". One scanner should not be able to write the adoption number.
Five observations over ten minutes is a low bar for any real agent and an
awkward one for a probe; it is stated on the page. The services leg is a
knob left at one: an agent whose whole job is one API is still an agent.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import SOURCE_AGENT, settings
from app.db.models import Observation


def meets_threshold(observations: int, services: int, first: datetime, last: datetime) -> bool:
    span = (last - first).total_seconds() if first and last else 0.0
    return (observations >= settings.adoption_min_observations
            and services >= settings.adoption_min_services
            and span >= settings.adoption_min_span_seconds)


async def established_reporters(session: AsyncSession) -> set[str]:
    """Reporter hashes, among raw `agent` rows, that meet the threshold."""
    rows = (
        await session.execute(
            select(
                Observation.reporter_hash,
                func.count().label("n"),
                func.count(func.distinct(Observation.service)).label("services"),
                func.min(Observation.created_at).label("first"),
                func.max(Observation.created_at).label("last"),
            )
            .where(Observation.source == SOURCE_AGENT, Observation.reporter_hash.is_not(None))
            .group_by(Observation.reporter_hash)
        )
    ).all()
    return {r.reporter_hash for r in rows
            if meets_threshold(int(r.n), int(r.services), _dt(r.first), _dt(r.last))}


def _dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", ""))


def threshold_description() -> dict:
    return {"min_observations": settings.adoption_min_observations,
            "min_services": settings.adoption_min_services,
            "min_span_seconds": settings.adoption_min_span_seconds}
