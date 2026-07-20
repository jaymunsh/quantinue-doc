"""Persistence contracts for claimed runs and atomic stage boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, unique
from typing import TYPE_CHECKING, Annotated, Final, Protocol

from pydantic import Field, TypeAdapter

if TYPE_CHECKING:
    from datetime import date, datetime

    from quantinue.db.domain_records import CompletedFillWrite

_CENT: Final = Decimal("0.01")
_MAX_APP_ORDER_MONEY_DIGITS: Final = 12
AppOrderMoney = Annotated[
    Decimal,
    Field(
        gt=Decimal(0),
        max_digits=_MAX_APP_ORDER_MONEY_DIGITS,
        decimal_places=2,
        allow_inf_nan=False,
    ),
]
_APP_ORDER_MONEY_ADAPTER: Final = TypeAdapter[Decimal](AppOrderMoney)


def parse_app_order_money(value: Decimal | float | str) -> Decimal:
    """Parse one Role 09 money value into a finite positive cent Decimal."""
    return _APP_ORDER_MONEY_ADAPTER.validate_python(value)


def _normalize_app_order_money(value: Decimal, field_name: str) -> Decimal:
    if not value.is_finite() or value <= Decimal(0):
        msg = f"{field_name} must be finite and positive"
        raise ValueError(msg)
    normalized = value.quantize(_CENT)
    if normalized != value or len(normalized.as_tuple().digits) > _MAX_APP_ORDER_MONEY_DIGITS:
        msg = f"{field_name} must be a cent value with at most 12 digits"
        raise ValueError(msg)
    return normalized


@dataclass(frozen=True, slots=True)
class PersistedAttempt:
    """Observable execution attempt for one canonical component."""

    component: str
    attempt_no: int
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class AttemptFailure:
    """Redacted stable failure persisted for one attempt."""

    status: str
    error_code: str
    error_message: str


@dataclass(frozen=True, slots=True)
class DailyOrderReservation:
    """Complete canonical planned order used by the atomic app-order budget gate."""

    account_id: int
    trade_date: date
    signal_id: int
    idempotency_key: str
    ticker: str
    quantity: int
    entry_price: Decimal
    stop_price: Decimal
    take_profit_price: Decimal
    cap: int
    max_app_order_exposure_usd: Decimal = Decimal("1000.00")

    def __post_init__(self) -> None:
        """Normalize and validate every persisted money value before reservation."""
        for field_name, value in (
            ("account_id", self.account_id),
            ("signal_id", self.signal_id),
            ("quantity", self.quantity),
            ("cap", self.cap),
        ):
            if value <= 0:
                msg = f"{field_name} must be positive"
                raise ValueError(msg)
        object.__setattr__(
            self,
            "entry_price",
            _normalize_app_order_money(self.entry_price, "entry_price"),
        )
        object.__setattr__(
            self,
            "stop_price",
            _normalize_app_order_money(self.stop_price, "stop_price"),
        )
        object.__setattr__(
            self,
            "take_profit_price",
            _normalize_app_order_money(self.take_profit_price, "take_profit_price"),
        )
        object.__setattr__(
            self,
            "max_app_order_exposure_usd",
            _normalize_app_order_money(
                self.max_app_order_exposure_usd,
                "max_app_order_exposure_usd",
            ),
        )

    @property
    def reference_notional(self) -> Decimal:
        """Return the exact app-owned reference notional for this planned buy."""
        return Decimal(self.quantity) * self.entry_price


@unique
class AppOrderExposureStatus(StrEnum):
    """Canonical lifecycle state that determines app-owned budget eligibility."""

    PLANNED = "planned"
    SUBMITTED = "submitted"
    FILLED = "filled"
    FAILED = "failed"
    CANCELED = "canceled"


@unique
class AppOrderExposureReservationOutcome(StrEnum):
    """Whether an app-order request acquired, replayed, or was denied."""

    ACQUIRED = "acquired"
    REPLAYED = "replayed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class AppOrderExposureSummary:
    """App-owned planned-order exposure without broker balance claims."""

    account_id: int
    cap: Decimal
    planned_or_reserved: Decimal
    remaining: Decimal


@dataclass(frozen=True, slots=True)
class AppOrderExposureReservationResult:
    """One atomic app-order exposure reservation outcome and its visible summary."""

    outcome: AppOrderExposureReservationOutcome
    summary: AppOrderExposureSummary


class RunStore(Protocol):
    """Persistence boundary used by the jobs and the control room.

    Phase 5까지는 런 생명주기(claim·stage·finish)의 프로토콜이기도 했다.
    그 절반은 구 11단계 러너와 함께 죽었고, 남은 것은 잡과 웹이 실제로
    부르는 여섯 개다. 잡의 도메인 읽기·쓰기는 이 프로토콜이 아니라
    ``PostgresRunStore.domain``(도메인 저장소)을 탄다.
    """

    async def initialize(self) -> None:
        """Prepare storage."""
        ...

    async def close(self) -> None:
        """Release storage resources."""
        ...

    async def record_completed_fill(self, value: CompletedFillWrite) -> int:
        """Apply one unique app-owned completed fill to the simulated account."""
        ...

    async def reserve_daily_new_order(
        self, request: DailyOrderReservation
    ) -> AppOrderExposureReservationResult:
        """Atomically reserve one canonical order under daily and exposure limits."""
        ...

    async def app_order_exposure_summary(
        self, account_id: int, cap: Decimal
    ) -> AppOrderExposureSummary:
        """Return the account's app-owned eligible planned-order exposure."""
        ...

    async def reconcile_app_order_exposure(
        self, idempotency_key: str, status: AppOrderExposureStatus
    ) -> AppOrderExposureSummary | None:
        """Apply one canonical order lifecycle state without duplicate accumulation."""
        ...
