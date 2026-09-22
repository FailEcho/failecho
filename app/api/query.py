"""POST /v1/query -- ask the network before you retry."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import ReporterDep, SessionDep, SourceDep, TeamDep, VerifiedDep
from app.core.private import team_evidence
from app.core.service import canonical_service, query_intelligence
from app.schemas.query import QueryRequest, QueryResponse, TeamEvidence

router = APIRouter(tags=["network"])


@router.post(
    "/query",
    response_model=QueryResponse,
    summary="Ask what the network knows about a failure",
    description=(
        "Send the failure you just hit. The server normalizes and fingerprints "
        "it exactly like /v1/observe, then answers: is anyone else seeing this "
        "right now, is it new, how bad is the service, what did other agents "
        "try, and what actually worked.\n\n"
        "Call this **before** retrying -- that is the whole point: a retry that "
        "fails for everyone else is a retry you can skip.\n\n"
        "No telemetry is stored: this call creates no observation, no "
        "fingerprint and no reporter record. It increments anonymous aggregate "
        "counters (did the query find evidence, and did that evidence come "
        "from another reporter) so we can measure whether the network works.\n\n"
        "Not rate limited. When evidence is thin you get "
        "`status: INSUFFICIENT_DATA` and `recommendation: null` -- FailEcho "
        "does not guess.\n\n"
        "Send `X-FailEcho-Team` to also get back what your own team has seen: "
        "private evidence, never pooled, never public, and additional to the "
        "public answer rather than a replacement for it.\n\n"
        "Send `X-Reporter-ID` if you have one: it is salted and hashed on "
        "arrival, never stored by this endpoint, and lets FailEcho tell "
        "whether the evidence you just received came from somebody else."
    ),
)
async def query(
    payload: QueryRequest,
    session: SessionDep,
    reporter: ReporterDep,
    source: SourceDep,
    verified: VerifiedDep,   # noqa: ARG001 - stores nothing; rejects a bad signature
    team: TeamDep = None,
) -> QueryResponse:
    answer = await query_intelligence(
        session, payload, reporter_hash=reporter, source=source
    )
    if team is not None:
        # Additional to the public answer, never a substitute for it: the
        # public recommendation above was computed from public evidence
        # alone, and stays exactly as it was.
        evidence = await team_evidence(
            session, team, answer.fingerprint,
            canonical_service(payload.service), payload.operation,
        )
        answer.team_evidence = TeamEvidence.model_validate(evidence) if evidence else None
    return answer
