"""Request/response schemas for POST /v1/observe."""

from __future__ import annotations

from pydantic import Field, model_validator

from app.schemas.common import CallIdentity, ErrorCode, Outcome, ShortToken, StrictModel
from app.core.config import OUTCOME_FAILURE

#: Raw messages longer than this are rejected. Long messages are usually
#: pasted payloads, which is exactly what this network does not want.
MAX_ERROR_MESSAGE_LENGTH = 2000


class ObserveRequest(CallIdentity):
    """One anonymous tool-call outcome.

    Send this after *every* call if you can -- successes included. Failure
    rates are only meaningful when the network sees the denominator.

    PRIVACY: never put prompts, tool arguments, tool results, request or
    response bodies, headers, cookies, keys or customer data in any field.
    ``error_message`` is normalized server-side (identifiers replaced,
    credential-shaped substrings redacted) and the raw string is discarded.
    """

    outcome: Outcome = Field(
        description="'success' or 'failure'.", examples=["failure"]
    )
    error_type: ShortToken | None = Field(
        default=None,
        description="Short failure class, e.g. 'validation_error', 'timeout'.",
        examples=["validation_error"],
    )
    error_code: ErrorCode | None = Field(
        default=None,
        description="Protocol/vendor code, e.g. '422', 'ECONNRESET'.",
        examples=["422"],
    )
    error_message: str | None = Field(
        default=None,
        max_length=MAX_ERROR_MESSAGE_LENGTH,
        description=(
            "Short error message. Normalized before storage; the raw value is "
            "never persisted. Do not include secrets or customer data."
        ),
        examples=["Repository 918272 was not found"],
    )
    latency_ms: int | None = Field(
        default=None,
        ge=0,
        le=3_600_000,
        description="Observed call latency in milliseconds.",
        examples=[421],
    )
    mutates: bool | None = Field(
        default=None,
        description=(
            "Does this operation change state? Declare it if you know: HTTP "
            "GET is a read, anything else is probably a write, GraphQL is all "
            "POST so only the caller can say. Where declared, this decides "
            "whether a never-failing operation is reported as an unverified "
            "success; where absent, a heuristic on the name is used and "
            "labelled as such."
        ),
        examples=[True],
    )

    @model_validator(mode="after")
    def _failures_need_a_signature(self) -> "ObserveRequest":
        if self.outcome == OUTCOME_FAILURE and not (
            self.error_type or self.error_code or self.error_message
        ):
            raise ValueError(
                "a failure observation needs at least one of "
                "error_type, error_code or error_message"
            )
        return self


class ObserveResponse(StrictModel):
    """Acknowledgement, plus an immediate hint about what was just reported."""

    accepted: bool = Field(description="True when the observation was stored.")
    fingerprint: str | None = Field(
        default=None,
        description=(
            "Fingerprint of the failure signature. Null for success "
            "observations, which do not have one."
        ),
    )
    known: bool = Field(
        description=(
            "True when the network had already seen this fingerprint before "
            "this report. Always false for success observations."
        )
    )
    observations: int = Field(
        description=(
            "For failures: total observations of this fingerprint, including "
            "this one. For successes: total observations recorded for this "
            "service/operation/version/schema_hash scope."
        )
    )
    normalized_error: str | None = Field(
        default=None,
        description="What we actually stored, so you can audit the normalizer.",
    )
    private: bool = Field(
        default=False,
        description=(
            "True when this report was stored for your team only: kept apart "
            "from the public network, never pooled, never counted as adoption "
            "and answered back only to a caller with the same team token."
        ),
    )
