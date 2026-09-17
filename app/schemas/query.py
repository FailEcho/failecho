"""Request/response schemas for POST /v1/query -- the read side of the network."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import (
    CallIdentity,
    ErrorCode,
    ShortToken,
    Status,
    StrictModel,
)
from app.schemas.observe import MAX_ERROR_MESSAGE_LENGTH


class QueryRequest(CallIdentity):
    """Ask the network what is happening with a failure you just hit.

    Call this *before* retrying. Send the same fields you would send to
    /v1/observe; the server normalizes and fingerprints them the same way, so
    your local error matches what other agents reported.
    """

    error_type: ShortToken | None = Field(
        default=None,
        description="Short failure class, e.g. 'validation_error'.",
        examples=["validation_error"],
    )
    error_code: ErrorCode | None = Field(
        default=None, description="Protocol/vendor code.", examples=["422"]
    )
    error_message: str | None = Field(
        default=None,
        max_length=MAX_ERROR_MESSAGE_LENGTH,
        description=(
            "Your raw error message. Normalized server-side and never stored "
            "by this endpoint."
        ),
        examples=["Repository 555812 was not found"],
    )


class ObservationCounts(BaseModel):
    """How common this exact fingerprint is."""

    total: int = Field(description="All-time observations of this fingerprint.")
    last_5m: int = Field(description="Observations in the last 5 minutes.")
    last_1h: int = Field(description="Observations in the last hour.")
    unique_reporters: int = Field(
        description=(
            "Distinct identified reporters in the last hour. Anonymous "
            "observations are excluded, so this is a lower bound: one noisy "
            "reporter cannot look like a crowd."
        )
    )


class FailureRates(BaseModel):
    """Failure rate for the service/operation/version/schema_hash scope.

    Null means the window held no observations at all -- which is not 0.0.
    """

    last_5m: float | None = Field(description="failures / observations, last 5 minutes.")
    last_1h: float | None = Field(description="failures / observations, last hour.")


class RecoveryActionStats(BaseModel):
    """What other agents tried against this fingerprint, and how it went."""

    action: str = Field(description="Recovery action name, e.g. 'refresh_schema'.")
    attempts: int = Field(description="Times this action was attempted.")
    successes: int = Field(description="Times it resolved the failure.")
    success_rate: float = Field(
        description="successes / attempts, as reported (uncapped)."
    )
    effective_attempts: int = Field(
        description=(
            "Attempts after the per-reporter hourly cap. One reporter can "
            "contribute at most 5 attempts per hour to this number, so a "
            "single noisy agent cannot manufacture evidence."
        )
    )
    effective_successes: int = Field(
        description="Successes after the same cap, scaled down proportionally."
    )
    unique_reporters: int = Field(
        description=(
            "Distinct identified reporters behind this action. 0 means the "
            "evidence is entirely anonymous, which lowers confidence."
        )
    )
    confidence: float = Field(
        description=(
            "Wilson score lower bound (95%) computed on the capped counts and "
            "discounted when fewer than 3 distinct reporters back it. Rewards "
            "sample size, so 5/5 ranks below 117/124."
        )
    )
    recent_attempts: int = Field(
        default=0,
        description="Attempts inside the decay window (default 24h).",
    )
    recent_success_rate: float | None = Field(
        default=None,
        description="successes / attempts inside the decay window; null when none.",
    )
    decaying: bool = Field(
        default=False,
        description=(
            "True when this action used to work and recently has not: at "
            "least 5 prior attempts at 60%+ success, at least 3 recent "
            "attempts, and a drop of 0.4 or more. The early warning that the "
            "root cause changed while the error shape stayed the same. Flagged, "
            "never silently re-ranked -- both rates are here for you to weigh."
        ),
    )


class SuccessEvidence(BaseModel):
    """Whether the successes counted for this service+operation can be
    believed, from the outside."""

    successes_total: int = Field(description="Successes ever seen for this service+operation.")
    failures_total: int = Field(description="Failures ever seen for it.")
    write_like: bool = Field(
        description=(
            "Whether this operation changes state. From reporters' `mutates` "
            "declarations when any exist (majority wins), otherwise a "
            "heuristic on the name (create_, update_, delete_, ...). See "
            "write_source for which."
        )
    )
    write_source: str = Field(
        description=(
            "'declared' when reporters said so via `mutates`; 'name' when it "
            "was inferred from the operation name, which is a guess. Declare "
            "it -- HTTP GET is a read, anything else is probably a write, and "
            "GraphQL cannot be inferred at all."
        )
    )
    verified: bool = Field(
        description=(
            "False when the operation looks like a write, has 20 or more "
            "successes, and has never once failed. From outside, a backend that "
            "returns 200 for writes it never performs is indistinguishable from a "
            "flawless one, so such successes are reported as unverified rather "
            "than as success. Only state-checking tests on your side can tell "
            "the two apart."
        )
    )


class FixEvidence(BaseModel):
    action: str = Field(description="Recovery action that resolved the neighbouring failure.")
    successes: int = Field(description="Times it did.")
    attempts: int = Field(description="Times it was tried.")


class RelatedFailure(BaseModel):
    """Another failure shape on the same service+operation, with what fixed it.

    One root cause often wears several masks: an expired token surfaces as
    not_found from one client, auth_error from another, a timeout from a
    third. Each shape alone may never reach the recommendation floor; what
    joins them is the fix. This is the evidence for that join, not the join
    itself -- if the same action fixed this and the failure you asked about,
    they are probably one thing, and you are the one who decides.
    """

    fingerprint: str
    error_type: str | None
    error_code: str | None
    observations: int = Field(description="How often this neighbouring shape has been seen.")
    fixed_by: list[FixEvidence] = Field(
        description="Actions with at least one success against this neighbour, best first."
    )
    shares_a_fix_with_you: bool = Field(
        description=(
            "True when one of these actions has also succeeded against the "
            "failure you asked about. The strongest hint here that the two "
            "shapes are one cause."
        )
    )


class Recommendation(BaseModel):
    """The single action the network would try next, when evidence allows."""

    action: str = Field(
        description=(
            "Recommended recovery action. Usually something that worked for "
            "others (retry, backoff, refresh_schema, ...). The value 'skip' is "
            "the network saying nothing anyone tried recently has worked: do "
            "not spend another attempt, fail fast or escalate -- see warning."
        )
    )
    confidence: float = Field(
        description=(
            "Wilson lower bound, capped below 1.0. Not a model output -- "
            "recompute it yourself from attempts/successes if you like."
        )
    )
    based_on_attempts: int = Field(description="Attempts backing the recommendation.")
    based_on_successes: int = Field(description="Successes backing it.")
    effective_attempts: int = Field(
        default=0,
        description="Attempts that actually counted, after the per-reporter cap.",
    )
    unique_reporters: int = Field(
        default=0,
        description=(
            "Distinct identified reporters behind it. Below 3 the confidence "
            "above has already been discounted."
        ),
    )
    from_other_agents: bool | None = Field(
        default=None,
        description=(
            "True when this rests on evidence somebody else reported, false "
            "when it is your own history coming back to you -- which is a "
            "real answer, not a lesser one, and is what FailEcho gives you "
            "before anyone else has joined. Null when you did not send a "
            "reporter id, because then it cannot be known."
        ),
    )
    decaying: bool = Field(
        default=False,
        description=(
            "True when the recommended action's recent success rate has "
            "dropped well below its long-run rate. It is still the best "
            "evidence on record, which is why it is recommended; treat it as "
            "'this used to work' rather than 'this works'."
        ),
    )
    warning: str | None = Field(
        default=None,
        description="Plain-language note when decaying is true; null otherwise.",
    )


class QueryResponse(StrictModel):
    """Everything the network knows about this failure right now."""

    known: bool = Field(
        description="True when this fingerprint has been observed before."
    )
    fingerprint: str = Field(
        description="Fingerprint computed from your request. Pass it to /v1/outcome."
    )
    status: Status = Field(
        description=(
            "Health of the service/operation scope: HEALTHY, DEGRADED, MAJOR "
            "or INSUFFICIENT_DATA. MVP threshold heuristic, see /docs."
        )
    )
    first_seen: str | None = Field(
        default=None, description="ISO-8601 UTC of the first sighting."
    )
    last_seen: str | None = Field(
        default=None, description="ISO-8601 UTC of the most recent sighting."
    )
    looks_new: bool = Field(
        default=False,
        description=(
            "True when this fingerprint was first seen inside the last 5 "
            "minutes -- i.e. it looks like a fresh incident rather than a "
            "long-standing failure mode."
        ),
    )
    observations: ObservationCounts
    failure_rate: FailureRates
    normalized_error: str | None = Field(
        default=None, description="Normalized form of your error message."
    )
    recovery_actions: list[RecoveryActionStats] = Field(
        default_factory=list,
        description="All reported recovery actions, strongest evidence first.",
    )
    success_evidence: SuccessEvidence | None = Field(
        default=None,
        description=(
            "Whether this service+operation's successes can be believed from "
            "outside. Null when nothing has been observed for it at all."
        ),
    )
    related_failures: list[RelatedFailure] = Field(
        default_factory=list,
        description=(
            "Other failure shapes on the same service+operation that something "
            "has fixed, most-seen first, at most five. Present even when the "
            "failure you asked about is unknown: a brand-new shape on a "
            "service where two other shapes were both fixed by refresh_schema "
            "is worth knowing about before your first retry."
        ),
    )
    recommendation: Recommendation | None = Field(
        default=None,
        description=(
            "Null when no action has enough evidence (min 5 effective attempts "
            "and 60% success rate). The network never fabricates confidence."
        ),
    )
    demo_data_included: bool = Field(
        default=False,
        description=(
            "True when synthetic demo rows contribute to these numbers. Treat "
            "the recommendation as an illustration, not as field evidence."
        ),
    )
    evidence_sources: list[str] = Field(
        default_factory=list,
        description=(
            "Where the evidence behind this answer came from. 'agent': "
            "independent agents. 'first_party': FailEcho's own agents -- real "
            "calls, but not independent. 'demo_agent' and 'synthetic': demo "
            "data. Weigh an answer backed only by 'first_party' as one "
            "reporter's experience."
        ),
    )
