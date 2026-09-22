"""POST /v1/observe -- the write side of the network."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import (
    RateLimitDep,
    ReporterDep,
    SessionDep,
    SourceDep,
    TeamDep,
    VerifiedDep,
)
from app.core.service import record_observation, record_private_observation
from app.schemas.observe import ObserveRequest, ObserveResponse

router = APIRouter(tags=["network"])


@router.post(
    "/observe",
    response_model=ObserveResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[RateLimitDep],
    summary="Report a tool-call outcome (success or failure)",
    description=(
        "Anonymously report what happened when you called a tool. Failures are "
        "normalized and fingerprinted so other agents can recognise the same "
        "problem; successes are counted so failure rates mean something.\n\n"
        "**Send both outcomes if you can** -- a network that only sees failures "
        "cannot tell a broken service from a busy one.\n\n"
        "**Never send** prompts, tool arguments, tool results, request or "
        "response bodies, headers, cookies, API keys, or customer data. Only "
        "the fields below are accepted; anything else is discarded before "
        "storage. `error_message` is normalized (identifiers replaced, "
        "credential-shaped substrings redacted) and the raw string is dropped.\n\n"
        "No account and no API key. Writes are rate limited per client IP."
    ),
)
async def observe(
    payload: ObserveRequest,
    session: SessionDep,
    reporter: ReporterDep,
    source: SourceDep,
    verified: VerifiedDep,
    team: TeamDep,
) -> ObserveResponse:
    if team is not None:
        # Private mode: stored for this team alone and never for the network.
        return await record_private_observation(
            session, payload, team_hash=team, reporter_hash=reporter
        )
    return await record_observation(
        session, payload, reporter_hash=reporter, source=source, verified=verified
    )
