"""POST /v1/outcome -- report whether a recovery action worked."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import RateLimitDep, ReporterDep, SessionDep, SourceDep
from app.core.service import record_recovery_outcome
from app.schemas.outcome import OutcomeRequest, OutcomeResponse

router = APIRouter(tags=["network"])


@router.post(
    "/outcome",
    response_model=OutcomeResponse,
    dependencies=[RateLimitDep],
    summary="Report the result of a recovery attempt",
    description=(
        "After you act on a known failure -- retry, wait, refresh the schema, "
        "fall back -- tell the network whether it worked. These reports are "
        "what turn 'everyone is failing' into 'and here is what fixes it'.\n\n"
        "Send one report per attempt, not one per retry loop iteration: a "
        "single reporter contributes at most 5 attempts per hour to any "
        "action's confidence.\n\n"
        "Unknown fingerprints are accepted: another node may already know the "
        "signature even if this one does not."
    ),
)
async def outcome(
    payload: OutcomeRequest,
    session: SessionDep,
    reporter: ReporterDep,
    source: SourceDep,
) -> OutcomeResponse:
    return await record_recovery_outcome(
        session, payload, reporter_hash=reporter, source=source
    )
