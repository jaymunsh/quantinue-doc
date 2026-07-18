"""Persistence contracts for claimed runs and atomic stage boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, unique
from typing import TYPE_CHECKING, Annotated, Final, Protocol

from pydantic import Field, TypeAdapter

if TYPE_CHECKING:
    from datetime import date, datetime

    from quantinue.core.contracts import PipelineContext, PipelineRequest, PipelineRun, RunId
    from quantinue.db.active_snapshot import ActivePipelineSnapshot
    from quantinue.db.domain_records import CompletedBuyWrite
    from quantinue.db.simulated_portfolio import SimulatedPortfolioSnapshot

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
class RunClaim:
    """Atomic claim outcome with the last durable context, when acquired."""

    acquired: bool
    terminal_run: PipelineRun | None = None
    context: PipelineContext | None = None


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
    """Persistence boundary used by orchestration and operational views."""

    async def initialize(self) -> None:
        """Prepare storage."""
        ...

    async def latest_cycle_ts(self) -> datetime | None:
        """Return the newest cycle timestamp not lost to failure."""
        ...

    async def close(self) -> None:
        """Release storage resources."""
        ...

    async def claim(
        self, key: str, request: PipelineRequest, *, resume_failed: bool = False
    ) -> RunClaim:
        """Atomically claim a run key."""
        ...

    async def wait_for_release(self, key: str) -> PipelineRun | None:
        """Wait for the current claimant."""
        ...

    async def complete_stage(
        self,
        key: str,
        context: PipelineContext,
        attempt: PersistedAttempt,
    ) -> None:
        """Commit an attempt and checkpoint atomically."""
        ...

    async def start_attempt(
        self,
        key: str,
        component: str,
        started_at: datetime,
    ) -> PersistedAttempt:
        """Persist a running attempt."""
        ...

    async def fail_attempt(
        self,
        key: str,
        attempt: PersistedAttempt,
        finished_at: datetime,
        failure: AttemptFailure,
    ) -> None:
        """Persist a failed attempt."""
        ...

    async def finish_run(self, key: str, run: PipelineRun, *, resumable: bool = False) -> None:
        """Publish a terminal run."""
        ...

    async def abandon(self, key: str) -> None:
        """Release a nonterminal claim."""
        ...

    async def get_by_key(self, key: str) -> PipelineRun | None:
        """Get a terminal run by key."""
        ...

    async def list_attempts(self, run_id: RunId) -> tuple[PersistedAttempt, ...]:
        """List attempts in insertion order."""
        ...

    async def list_recent(self, limit: int = 20) -> tuple[PipelineRun, ...]:
        """List recent terminal runs."""
        ...

    async def list_active(self, limit: int = 20) -> tuple[ActivePipelineSnapshot, ...]:
        """List safe snapshots for nonterminal runs only."""
        ...

    async def simulated_portfolio(self, opening_cash: Decimal) -> SimulatedPortfolioSnapshot:
        """Return the derived local buy-only account read model."""
        ...

    async def record_completed_buy(self, value: CompletedBuyWrite) -> int:
        """Apply one unique app-owned completed buy to the simulated account."""
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
