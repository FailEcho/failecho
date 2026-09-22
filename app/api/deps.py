"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import (
    OPERATOR_BEARER_HEADER,
    OPERATOR_HEADER,
    TEAM_HEADER,
    settings,
)
from app.core.identity import SIGNATURE_HEADER, TIMESTAMP_HEADER, is_key_id, verify
from app.core.private import MIN_TOKEN_LENGTH, hash_team
from app.core.privacy import hash_reporter_id
from app.core.service import operator_token_from, source_from_kind
from app.core.ratelimit import check_write_limit, client_key
from app.db.database import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def reporter_hash(
    x_reporter_id: Annotated[
        str | None,
        Header(
            alias="X-Reporter-ID",
            description=(
                "Optional, stable, self-chosen reporter identifier (any opaque "
                "string). It is salted and hashed on arrival and never stored "
                "raw. Supplying one improves unique-reporter counts and raises "
                "the confidence the network can place in your evidence; "
                "omitting it is fully supported and keeps you anonymous."
            ),
        ),
    ] = None,
) -> str | None:
    return hash_reporter_id(x_reporter_id)


ReporterDep = Annotated[str | None, Depends(reporter_hash)]


async def reporter_verified(
    request: Request,
    x_reporter_id: Annotated[str | None, Header(alias="X-Reporter-ID", include_in_schema=False)] = None,
    x_reporter_signature: Annotated[
        str | None,
        Header(
            alias=SIGNATURE_HEADER,
            description=(
                "Optional. A reporter whose id is an Ed25519 public key "
                "(`ed25519:<base64url>`) can sign each request, proving the "
                "report is really from that reporter rather than from anyone "
                "who knows its id. Signed over "
                "`failecho-sig-v1 \\n timestamp \\n METHOD \\n path \\n "
                "sha256(body)` and sent with X-Reporter-Timestamp. A signature "
                "that does not verify is rejected, never downgraded: a "
                "reporter that thinks it is signing should find out."
            ),
        ),
    ] = None,
    x_reporter_timestamp: Annotated[
        str | None, Header(alias=TIMESTAMP_HEADER, include_in_schema=False)
    ] = None,
) -> bool:
    """Whether this request carried a signature that checks out.

    Unsigned is the normal case and stays fully supported -- this returns
    False and nothing changes. It does not decide whether a report is stored
    or whether it counts as adoption; the threshold does that.
    """
    if not x_reporter_signature:
        return False
    if not is_key_id(x_reporter_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "A signed request needs X-Reporter-ID to be the key that "
                "signed it: 'ed25519:<base64url public key>'."
            ),
        )
    body = await request.body()
    if not verify(x_reporter_id, x_reporter_timestamp, x_reporter_signature,
                  request.method, request.url.path, body):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Signature did not verify. Check the clock (5 minutes of skew "
                "allowed), that the signature covers this exact body, and that "
                "the id is the public key that signed it. Send no signature at "
                "all to report anonymously."
            ),
        )
    return True


VerifiedDep = Annotated[bool, Depends(reporter_verified)]


async def reporter_source(
    x_reporter_kind: Annotated[
        str | None,
        Header(
            alias="X-Reporter-Kind",
            description=(
                "Optional self-label. Send 'demo' when the caller is an "
                "example or demo agent: its reports are stored and usable, but "
                "excluded from the network's real-adoption metrics. Anything "
                "else (or nothing) is treated as real agent telemetry. "
                "Self-labelling can only downgrade a report, never promote one."
            ),
        ),
    ] = None,
    x_failecho_operator: Annotated[
        str | None,
        Header(
            alias=OPERATOR_HEADER,
            include_in_schema=False,
            description=(
                "Operator secret, sent only by FailEcho's own agents. With the "
                "right value the report is labelled first_party; a wrong one "
                "is stored as demo."
            ),
        ),
    ] = None,
    authorization: Annotated[
        str | None,
        Header(
            alias=OPERATOR_BEARER_HEADER,
            include_in_schema=False,
            description=(
                "Accepted only as an alternative way to send the operator "
                "token, for hosts that filter custom header names. The "
                "service needs no credential: without one you are an ordinary "
                "reporter, which is the normal case."
            ),
        ),
    ] = None,
) -> str:
    return source_from_kind(
        x_reporter_kind, operator_token_from(x_failecho_operator, authorization)
    )


SourceDep = Annotated[str, Depends(reporter_source)]


async def team(
    x_failecho_team: Annotated[
        str | None,
        Header(
            alias=TEAM_HEADER,
            description=(
                "Optional. A secret your team generates (32 random bytes is "
                "right; 16 characters is the minimum). With it, a report is "
                "stored privately for your team alone: never pooled, never "
                "counted as adoption, never visible to anyone else, and never "
                "part of any public number. A query with the same token "
                "returns the public answer plus your team's own evidence. "
                "There is no account: the token is the only thing that "
                "identifies the team, it is stored as a salted hash, and "
                "losing it loses access to that evidence. Free while the "
                "network is bootstrapping."
            ),
        ),
    ] = None,
) -> str | None:
    """The team hash for this request, or None for the public network."""
    if x_failecho_team is None or not x_failecho_team.strip():
        return None
    if not settings.private_mode_open:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Private mode is not open on this instance.",
        )
    if len(x_failecho_team.strip()) < MIN_TOKEN_LENGTH:
        # Rejected rather than accepted-and-weak: a team that thinks its
        # evidence is private deserves a token nobody can guess.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"A team token must be at least {MIN_TOKEN_LENGTH} characters. "
                "It is the only thing separating your evidence from everyone "
                "else's; generate a random one."
            ),
        )
    return hash_team(x_failecho_team)


TeamDep = Annotated[str | None, Depends(team)]


async def enforce_write_rate_limit(request: Request) -> None:
    """Throttle write endpoints per client IP. Reads are never throttled."""
    key = client_key(
        request.client.host if request.client else None, dict(request.headers)
    )
    allowed, retry_after = check_write_limit(key)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Write rate limit exceeded. Batch your reports or slow down; "
                "reads (/v1/query) are never rate limited."
            ),
            headers={"Retry-After": str(retry_after)},
        )


RateLimitDep = Depends(enforce_write_rate_limit)
