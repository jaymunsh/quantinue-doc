"""Shared immutable boundary models for entities, evidence, decisions, and reviews."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal, NewType

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

from quantinue.core.ontology import (
    Decision,
    EventType,
    EvidenceKind,
    ModelProvider,
    OrderStatus,
    SubmissionState,
)

Score = Annotated[float, Field(ge=0.0, le=1.0)]
PositiveMoney = Annotated[Decimal, Field(gt=0)]
SignalId = NewType("SignalId", int)
AccountId = NewType("AccountId", int)
OrderId = NewType("OrderId", int)
SubmissionId = NewType("SubmissionId", int)
TickerSymbol = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[A-Z0-9.-]{1,12}$"),
]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        message = "timestamp must include a timezone"
        raise ValueError(message)
    return value.astimezone(UTC)


AwareDateTime = Annotated[datetime, AfterValidator(_utc)]


class ContractModel(BaseModel):
    """Strict immutable base for data crossing process and persistence boundaries."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", str_strip_whitespace=True)


class Entity(ContractModel):
    """Canonical security identity."""

    entity_id: str = Field(min_length=1)
    ticker: TickerSymbol
    company_name: str | None = None


class Evidence(ContractModel):
    """One immutable, source-addressable piece of evidence."""

    evidence_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    observed_at: AwareDateTime
    captured_at: AwareDateTime
    confidence: Score
    kind: EvidenceKind = EvidenceKind.MODEL_OUTPUT
    content_hash: str | None = None
    parent_evidence_ids: tuple[str, ...] = ()
    model_name: str | None = None
    model_provider: ModelProvider | None = None
    prompt_version: str | None = None
    policy_version: str | None = None
    input_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_available_evidence(self) -> "Evidence":
        """Reject evidence that was not available at its capture boundary."""
        if self.observed_at > self.captured_at:
            message = "observed_at must not be after captured_at"
            raise ValueError(message)
        return self


class Event(ContractModel):
    """A time-bound market event linked to its evidence."""

    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    ticker: TickerSymbol
    event_type: EventType
    occurred_at: AwareDateTime
    evidence_ids: tuple[str, ...] = Field(min_length=1)


class Judgment(ContractModel):
    """Evidence-backed critic judgment at a planned execution slot."""

    signal_id: SignalId = Field(gt=0)
    ticker: TickerSymbol
    cycle_ts: AwareDateTime
    decision: Decision
    confidence: Score
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    event_ids: tuple[str, ...] = ()
    rationale: str | None = None


class Order(ContractModel):
    """Broker-independent bracket order contract."""

    order_id: OrderId = Field(gt=0)
    signal_id: SignalId = Field(gt=0)
    account_id: AccountId = Field(gt=0)
    ticker: TickerSymbol
    quantity: int = Field(gt=0)
    entry_price: PositiveMoney
    stop_price: PositiveMoney
    take_profit_price: PositiveMoney
    order_type: Literal["bracket"] = "bracket"
    status: OrderStatus
    idempotency_key: str = Field(min_length=1)
    broker_order_id: str | None = None
    parent_order_id: str | None = None
    stop_leg_order_id: str | None = None
    take_profit_leg_order_id: str | None = None

    @model_validator(mode="after")
    def require_valid_bracket(self) -> "Order":
        """Reject an inverted buy bracket at the trust boundary."""
        prices_are_ordered = self.stop_price < self.entry_price < self.take_profit_price
        if not prices_are_ordered:
            message = "buy bracket must satisfy stop < entry < take-profit"
            raise ValueError(message)
        return self


class Review(ContractModel):
    """T+5 outcome and reusable lesson for one judgment."""

    signal_id: SignalId = Field(gt=0)
    ret_1d: float
    ret_3d: float
    ret_5d: float
    is_hit: bool
    max_drawdown: float = Field(le=0)
    lesson: str = Field(min_length=1)


class OrderSubmission(ContractModel):
    """Token-owned reservation created before broker submission or domain linkage."""

    submission_id: SubmissionId = Field(gt=0)
    client_order_id: str = Field(min_length=1)
    state: SubmissionState
    owner_token: str = Field(min_length=1)
    claimed_at: AwareDateTime
    stale_after: AwareDateTime
    run_id: str | None = None
    order_id: OrderId | None = None
    broker_order_id: str | None = None
    result_payload: JsonValue | None = None
    last_error: str | None = None
    created_at: AwareDateTime
    updated_at: AwareDateTime

    @model_validator(mode="after")
    def require_positive_lease(self) -> "OrderSubmission":
        """Require a non-empty ownership interval for stale-claim recovery."""
        if self.stale_after <= self.claimed_at:
            message = "stale_after must be after claimed_at"
            raise ValueError(message)
        return self
