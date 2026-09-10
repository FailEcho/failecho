"""Request/response schemas for POST /v1/outcome -- closing the feedback loop."""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, Field

from app.schemas.common import FingerprintStr, StrictModel, validate_action

#: Free-form in V1 on purpose. These are the names agents already use; a
#: controlled vocabulary can come later once we see what people actually send.
SUGGESTED_ACTIONS = (
    "retry",
    "wait",
    "refresh_schema",
    "remove_optional_field",
    "reconnect",
    "use_fallback",
    "reauthenticate",
    "abort",
)

ActionName = Annotated[str, AfterValidator(validate_action)]


class OutcomeRequest(StrictModel):
    """Tell the network whether a recovery action actually worked.

    This is the highest-value telemetry in the system: it is the difference
    between "everyone is failing" and "everyone is failing, and refreshing the
    schema fixes it".
    """

    fingerprint: FingerprintStr = Field(
        description="Fingerprint from /v1/observe or /v1/query.",
        examples=["3f2a1c0d4e5b6a7f8c9d0e1f2a3b4c5d"],
    )
    action: ActionName = Field(
        description=(
            "What you tried. Free-form string, lowercased and normalized. "
            f"Common values: {', '.join(SUGGESTED_ACTIONS)}."
        ),
        examples=["refresh_schema"],
    )
    successful: bool = Field(
        description="True when the action resolved the failure."
    )


class OutcomeResponse(StrictModel):
    accepted: bool = Field(description="True when the outcome was stored.")
