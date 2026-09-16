"""Shared field types and validators.

Field descriptions here are not decoration: the generated OpenAPI schema at
/openapi.json is meant to be read by *agents* deciding how to call us, so
every field explains what it is for and what it must never contain.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from app.core.config import STATUS_DEGRADED, STATUS_HEALTHY  # noqa: F401  (docs)

Outcome = Literal["success", "failure"]
Status = Literal["HEALTHY", "DEGRADED", "MAJOR", "INSUFFICIENT_DATA"]

#: Every metadata field is a name, and a name has no control characters in
#: it. NUL, escape sequences and the C1 range were all accepted, stored, and
#: served back out of /v1/services -- a NUL in a service name truncates that
#: string in a surprising number of consumers, and an escape sequence in a
#: terminal is a cursor instruction rather than a word. Rejected at the door
#: rather than stripped, so the caller learns the name they chose is not the
#: name we stored.
_NO_CONTROL = r"^[^\x00-\x1f\x7f-\x9f]+$"

#: A service is a host name or an MCP server's own name. Names in any
#: script are names, and the test suite holds that line -- but a URL is not
#: a name, a path is not a name, and a printf format string is not a name.
#: The first fuzzer to find /v1/observe (2026-09-16 19:41 UTC) got
#: `http://example.invalid/x` and `%n` stored as services and counted on the
#: front page as independent agents. Rejected at the door: a scheme
#: separator, a leading slash, or a `%` followed by a letter, digit or `%`.
_FORMAT_DIRECTIVE = re.compile(r"%[0-9A-Za-z%]")


def _service_shape(value: str) -> str:
    if value.startswith("/") or "://" in value or _FORMAT_DIRECTIVE.search(value):
        raise ValueError(
            "service is a host name or an MCP server name, e.g. 'api.github.com' "
            "or 'github-mcp' -- not a URL, a path or a format string"
        )
    return value


ServiceName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128,
                           pattern=_NO_CONTROL), AfterValidator(_service_shape)
]
OperationName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128,
                           pattern=_NO_CONTROL)
]
ShortToken = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64,
                           pattern=_NO_CONTROL)
]
ErrorCode = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32,
                           pattern=_NO_CONTROL)
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


#: How to name what was called. Fingerprints only match when agents name the
#: same thing the same way, which is the difference between shared evidence
#: and a private log.
SERVICE_NAMING = (
    "What was called, named the way other agents will name it: an MCP "
    "server's own name (the one it reports in serverInfo.name), or an HTTP "
    "API's host, e.g. 'api.github.com'. Not your client's local alias for it."
)
OPERATION_NAMING = (
    "The tool or endpoint exactly as the server defines it, e.g. "
    "'create_issue' -- without client prefixes such as 'mcp__github__'."
)


class CallIdentity(StrictModel):
    """The four fields that identify *what was called*.

    Together they are the health scope: the same operation on a different
    version or a different tool schema is a different thing to reason about.
    """

    service: ServiceName = Field(
        description=SERVICE_NAMING,
        examples=["github-mcp"],
    )
    operation: OperationName = Field(
        description=OPERATION_NAMING,
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
