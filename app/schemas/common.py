"""Shared field types and validators.

Field descriptions here are not decoration: the generated OpenAPI schema at
/openapi.json is meant to be read by *agents* deciding how to call us, so
every field explains what it is for and what it must never contain.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.config import STATUS_DEGRADED, STATUS_HEALTHY  # noqa: F401  (docs)

Outcome = Literal["success", "failure"]
Status = Literal["HEALTHY", "DEGRADED", "MAJOR", "INSUFFICIENT_DATA"]

ServiceName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]
OperationName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]
ShortToken = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]
ErrorCode = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)
]
FingerprintStr = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9a-f]{32}$")
]

_ACTION_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")


def validate_action(value: str) -> str:
    """Canonicalize a recovery action name so aggregation stays clean."""
    canonical = value.strip().lower().replace(" ", "_")
    if not _ACTION_RE.match(canonical):
        raise ValueError(
            "action must be 1-64 chars of [a-z0-9_.-], e.g. 'refresh_schema'"
        )
    return canonical


class StrictModel(BaseModel):
    """Base for every request body.

    PRIVACY: ``extra="ignore"`` means any field we did not ask for (a prompt,
    a tool argument, an auth header someone pasted in) is dropped by Pydantic
    before the handler runs. It can never reach the database.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class CallIdentity(StrictModel):
    """The four fields that identify *what was called*.

    Together they are the health scope: the same operation on a different
    version or a different tool schema is a different thing to reason about.
    """

    service: ServiceName = Field(
        description="Tool/service identifier, e.g. 'github-mcp'.",
        examples=["github-mcp"],
    )
    operation: OperationName = Field(
        description="Operation/tool name within the service, e.g. 'create_issue'.",
        examples=["create_issue"],
    )
    version: ShortToken | None = Field(
        default=None,
        description="Version of the service/tool, if the agent knows it.",
        examples=["2.8.1"],
    )
    schema_hash: ShortToken | None = Field(
        default=None,
        description=(
            "Short hash of the tool schema the agent used. Lets the network "
            "separate 'the API broke' from 'your schema is stale'."
        ),
        examples=["a817ce"],
    )
