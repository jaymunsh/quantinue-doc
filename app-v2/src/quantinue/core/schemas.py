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
    """Broker-independent order contract covering both entries and closes.

    ``entry_price`` is the decision-time reference price, not necessarily a buy
    price — on a close it is what the exit was priced against. The name predates
    selling and was kept because renaming it would ripple through the schema.
    """

    order_id: OrderId = Field(gt=0)
    signal_id: SignalId = Field(gt=0)
    account_id: AccountId = Field(gt=0)
    ticker: TickerSymbol
    quantity: int = Field(gt=0)
    entry_price: PositiveMoney
    # 청산에는 보호 레그가 없다 — 더미 값을 채우면 원장이 거짓을 말하게 되고
    # 나중에 "이 손절가는 뭐였지"라고 물었을 때 지어낸 숫자가 나온다.
    stop_price: PositiveMoney | None = None
    take_profit_price: PositiveMoney | None = None
    order_type: Literal["bracket", "close"] = "bracket"
    closes_order_id: OrderId | None = None
    status: OrderStatus
    idempotency_key: str = Field(min_length=1)
    broker_order_id: str | None = None
    parent_order_id: str | None = None
    stop_leg_order_id: str | None = None
    take_profit_leg_order_id: str | None = None

    @model_validator(mode="after")
    def require_valid_bracket(self) -> "Order":
        """Enforce the DDL's conditional shape at the trust boundary.

        db/schema.sql:144-148과 같은 규칙이다. 두 제약이 갈라지면 계약을 통과한
        주문이 INSERT에서 터지므로, 여기서 먼저 막아 실패를 앞당긴다.
        """
        if self.order_type == "close":
            if self.closes_order_id is None:
                # 어느 매수를 닫는지가 실현손익의 짝이다. 없으면 이 청산은
                # 영원히 짝을 못 찾고 성과 집계에서 유령이 된다.
                message = "close order must name the order it closes"
                raise ValueError(message)
            return self
        if self.stop_price is None or self.take_profit_price is None:
            # 매수는 보호 없이 나갈 수 없다 — 이게 손절 공백을 막는 마지막 관문.
            message = "bracket order requires stop and take-profit prices"
            raise ValueError(message)
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
