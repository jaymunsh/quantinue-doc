"""Immutable input and computed output contracts owned by role 11."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal, LiteralString, assert_never

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, computed_field, model_validator
from pydantic_core import PydanticCustomError
from typing_extensions import override

from quantinue.core.typing import require_value
from quantinue.roles.role_11_reviewer.calendar import TradingCalendar

_REVIEW_DAY_COUNT = 5
_TIMEZONE_ERROR = ("timezone_required", "timestamp must include a timezone")
_BUY_FILL_ERROR = ("missing_buy_fill", "buy review requires filled_price")
_HOLD_CLOSE_ERROR = ("missing_hold_close", "hold review requires decision_close")
_BUY_NA_ERROR = ("contradictory_applicability", "buy filled_price cannot be N/A")
_HOLD_FILL_ERROR = ("contradictory_hold_fill", "hold review cannot contain filled_price")
_HOLD_NA_ERROR = (
    "missing_na_reason",
    "hold review must mark filled_price N/A with reason",
)
_FUTURE_ERROR = ("future_evidence", "observed_at must not be after captured_at")
_OFFSETS_ERROR = (
    "invalid_offsets",
    "snapshots must contain each day_offset from 1 through 5 exactly once",
)
_CROSS_RUN_ERROR = ("cross_run_evidence", "snapshot run_id must match signal run_id")
_STALE_ERROR = ("stale_snapshot", "snapshot observed_at must be after decided_at")
_PARENT_ERROR = ("missing_parent", "snapshot must reference signal evidence as parent")


def _error(code: LiteralString, message: LiteralString) -> PydanticCustomError:
    """Build a structured boundary validation error."""
    return PydanticCustomError(code, message)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise _error(*_TIMEZONE_ERROR)
    return value.astimezone(UTC)


AwareDateTime = Annotated[datetime, AfterValidator(_utc)]


@dataclass(frozen=True, slots=True)
class SessionDateError(ValueError):
    """A snapshot is not associated with its declared trading offset."""

    offset: int
    expected: date

    @override
    def __str__(self) -> str:
        """Describe the expected exchange session."""
        return f"day_offset {self.offset} price_date must be {self.expected.isoformat()}"


class Role11Model(BaseModel):
    """Strict immutable role boundary."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class NotApplicable(Role11Model):
    """Explicitly explain why a matrix dimension has no value."""

    dimension: Literal["filled_price"]
    reason: str = Field(min_length=1)


class ReviewSignal(Role11Model):
    """Final signal with execution lineage and its side-specific base."""

    run_id: str = Field(min_length=1)
    signal_id: int = Field(gt=0)
    side: Literal["buy", "hold"]
    trade_date: date
    decided_at: AwareDateTime
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    not_applicable: tuple[NotApplicable, ...]
    filled_price: Decimal | None = Field(default=None, gt=0)
    decision_close: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_side_base(self) -> "ReviewSignal":
        """Require fill for a buy and decision close for a hold."""
        match self.side:
            case "buy" if self.filled_price is None:
                raise _error(*_BUY_FILL_ERROR)
            case "hold" if self.decision_close is None:
                raise _error(*_HOLD_CLOSE_ERROR)
            case "buy" if self.not_applicable:
                raise _error(*_BUY_NA_ERROR)
            case "hold" if self.filled_price is not None:
                raise _error(*_HOLD_FILL_ERROR)
            case "hold" if tuple(item.dimension for item in self.not_applicable) != (
                "filled_price",
            ):
                raise _error(*_HOLD_NA_ERROR)
            case "buy" | "hold":
                return self
            case unreachable:
                assert_never(unreachable)

    @property
    def base_price(self) -> Decimal:
        """Return fill for buy and decision close for hold."""
        match self.side:
            case "buy":
                return require_value(self.filled_price, component="11", field_name="filled_price")
            case "hold":
                return require_value(
                    self.decision_close, component="11", field_name="decision_close"
                )
            case unreachable:
                assert_never(unreachable)


class ReviewPriceSnapshot(Role11Model):
    """One source-addressable close snapshot for T+1 through T+5."""

    run_id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    parent_evidence_ids: tuple[str, ...] = Field(min_length=1)
    day_offset: int = Field(ge=1, le=5)
    price_date: date
    close: Decimal = Field(gt=0)
    source: Literal["fixture", "market_data"] = "fixture"
    observed_at: AwareDateTime
    captured_at: AwareDateTime

    @model_validator(mode="after")
    def require_available_observation(self) -> "ReviewPriceSnapshot":
        """Forbid future evidence at its capture boundary."""
        if self.observed_at > self.captured_at:
            raise _error(*_FUTURE_ERROR)
        return self


class ReviewInput(Role11Model):
    """Complete deterministic scorer input."""

    signal: ReviewSignal
    snapshots: tuple[ReviewPriceSnapshot, ...]

    @model_validator(mode="after")
    def require_exact_offsets(self) -> "ReviewInput":
        """Reject missing or duplicate T+1 through T+5 closes."""
        offsets = [snapshot.day_offset for snapshot in self.snapshots]
        if sorted(offsets) != list(range(1, _REVIEW_DAY_COUNT + 1)):
            raise _error(*_OFFSETS_ERROR)
        signal_evidence = set(self.signal.evidence_ids)
        for snapshot in self.snapshots:
            if snapshot.run_id != self.signal.run_id:
                raise _error(*_CROSS_RUN_ERROR)
            if snapshot.observed_at <= self.signal.decided_at:
                raise _error(*_STALE_ERROR)
            if signal_evidence.isdisjoint(snapshot.parent_evidence_ids):
                raise _error(*_PARENT_ERROR)
        return self

    def validated_for(self, calendar: TradingCalendar) -> "ReviewInput":
        """Validate session dates against the injected market calendar."""
        for snapshot in self.snapshots:
            expected = calendar.offset(self.signal.trade_date, trading_days=snapshot.day_offset)
            if snapshot.price_date != expected:
                raise SessionDateError(snapshot.day_offset, expected)
        return self


class ReviewOutput(Role11Model):
    """Final review whose numeric fields cannot be caller supplied."""

    review_input: ReviewInput
    reviewed_at: AwareDateTime
    lesson: str = Field(min_length=1)

    @property
    def run_id(self) -> str:
        """Return the originating execution identifier."""
        return self.review_input.signal.run_id

    @property
    def signal_id(self) -> int:
        """Return the reviewed signal identifier."""
        return self.review_input.signal.signal_id

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        """Return signal and price evidence lineage in stable order."""
        price_ids = tuple(snapshot.evidence_id for snapshot in self.review_input.snapshots)
        return (*self.review_input.signal.evidence_ids, *price_ids)

    def _return(self, offset: int) -> float:
        closes = {snapshot.day_offset: snapshot.close for snapshot in self.review_input.snapshots}
        base = self.review_input.signal.base_price
        return float(((closes[offset] / base) - Decimal(1)) * Decimal(100))

    @computed_field
    @property
    def ret_1d(self) -> float:
        """Return percentage change at T+1."""
        return self._return(1)

    @computed_field
    @property
    def ret_3d(self) -> float:
        """Return percentage change at T+3."""
        return self._return(3)

    @computed_field
    @property
    def ret_5d(self) -> float:
        """Return percentage change at T+5."""
        return self._return(5)

    @computed_field
    @property
    def is_hit(self) -> bool:
        """Apply buy-positive and hold-nonpositive success policy."""
        match self.review_input.signal.side:
            case "buy":
                return self.ret_5d > 0
            case "hold":
                return self.ret_5d <= 0
            case unreachable:
                assert_never(unreachable)

    @computed_field
    @property
    def max_drawdown(self) -> float:
        """Return the worst percentage change from the review base."""
        returns = tuple(self._return(offset) for offset in range(1, _REVIEW_DAY_COUNT + 1))
        return min(0.0, *returns)
