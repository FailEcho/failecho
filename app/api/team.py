"""DELETE /v1/team and POST /v1/team/rotate -- a team's control over its own data.

Private mode has no account, so the token is the team: whoever holds it can
read the team's evidence. Two things a real team needs before it can trust
that, and which it had no way to do:

* **delete** everything stored under its token, at once, without asking
  anyone -- the right answer to "we are leaving" and to "that was a mistake";
* **rotate** to a new token when the old one leaks, keeping its history.

Both need the current token and nothing else, the same as every other private
call. That also means a leaked token lets its holder do both; there is no
account to appeal to. It is said wherever private mode is offered.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import Field
from sqlalchemy import delete, func, select, update

from app.api.deps import RateLimitDep, SessionDep, TeamDep
from app.core.private import MIN_TOKEN_LENGTH, hash_team
from app.db.models import PrivateObservation, PrivateRecoveryOutcome
from app.schemas.common import StrictModel

router = APIRouter(tags=["private mode"])


class TeamChange(StrictModel):
    deleted_observations: int = 0
    deleted_outcomes: int = 0
    moved_observations: int = 0
    moved_outcomes: int = 0


class RotateRequest(StrictModel):
    new_token: str = Field(
        min_length=MIN_TOKEN_LENGTH, max_length=512,
        description="The team's new secret. At least 16 characters; 32 random bytes is right.",
    )


def _require(team: str | None) -> str:
    if team is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Send the team's current token in X-FailEcho-Team.",
        )
    return team


@router.delete(
    "/team",
    response_model=TeamChange,
    dependencies=[RateLimitDep],
    summary="Delete everything stored under a team token",
    description=(
        "Deletes every private observation and recovery outcome stored under "
        "the token in `X-FailEcho-Team`, immediately and for good. Public data "
        "is untouched -- a team's reports never reached it. Returns what was "
        "deleted. Idempotent: a second call deletes nothing and says so."
    ),
)
async def delete_team(session: SessionDep, team: TeamDep) -> TeamChange:
    team_hash = _require(team)
    observations = await session.execute(delete(PrivateObservation).where(PrivateObservation.team_hash == team_hash))
    outcomes = await session.execute(
        delete(PrivateRecoveryOutcome).where(PrivateRecoveryOutcome.team_hash == team_hash))
    await session.commit()
    return TeamChange(deleted_observations=observations.rowcount or 0, deleted_outcomes=outcomes.rowcount or 0)


@router.post(
    "/team/rotate",
    response_model=TeamChange,
    dependencies=[RateLimitDep],
    summary="Move a team's evidence to a new token",
    description=(
        "For a token that has leaked. Send the current one in "
        "`X-FailEcho-Team` and the new one in the body; everything stored "
        "under the old token moves to the new one, and the old token stops "
        "reading anything. Refused if the new token already has data of its "
        "own: two teams' evidence must never be merged by accident."
    ),
)
async def rotate_team(payload: RotateRequest, session: SessionDep, team: TeamDep) -> TeamChange:
    old_hash = _require(team)
    new_hash = hash_team(payload.new_token)
    if new_hash == old_hash:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The new token is the old one.")
    taken = int((await session.execute(
        select(func.count()).select_from(PrivateObservation).where(PrivateObservation.team_hash == new_hash)
    )).scalar_one() or 0) + int((await session.execute(
        select(func.count()).select_from(PrivateRecoveryOutcome).where(PrivateRecoveryOutcome.team_hash == new_hash)
    )).scalar_one() or 0)
    if taken:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That token already holds evidence. Generate a fresh random one.",
        )
    observations = await session.execute(
        update(PrivateObservation).where(PrivateObservation.team_hash == old_hash).values(team_hash=new_hash))
    outcomes = await session.execute(
        update(PrivateRecoveryOutcome).where(PrivateRecoveryOutcome.team_hash == old_hash).values(team_hash=new_hash))
    await session.commit()
    return TeamChange(moved_observations=observations.rowcount or 0, moved_outcomes=outcomes.rowcount or 0)
