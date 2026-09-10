"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.privacy import hash_reporter_id
from app.core.service import source_from_kind
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
) -> str:
    return source_from_kind(x_reporter_kind)


SourceDep = Annotated[str, Depends(reporter_source)]


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
