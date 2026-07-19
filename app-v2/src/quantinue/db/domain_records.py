"""Typed write records for canonical trading-domain persistence."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from typing_extensions import override

from quantinue.core.ontology import FillSide


@dataclass(frozen=True, slots=True)
class StrategistSignalWrite:
    """Database-complete strategist signal linked to source snapshots."""

    run_id: str
    trade_date: date
    ticker: str
    cycle_ts: datetime
    side: str
    conviction: Decimal
    summary: str
    decision_close: Decimal
    evidence: tuple[str, ...]
    disclosure_score: Decimal = Decimal(0)
    news_score: Decimal = Decimal(0)
    signal_consensus: int = 0
    inv_type: str = "conservative"


@dataclass(frozen=True, slots=True)
class CriticVerdictWrite:
    """Canonical critic outcome for a persisted signal."""

    signal_id: int
    ticker: str
    decision: str
    category: str
    objection: str
    confidence: Decimal
    decided_layer: str
    verdict_source: str = "fresh"


@dataclass(frozen=True, slots=True)
class AccountWrite:
    """Paper account snapshot used by risk and order records."""

    broker_account_id: str
    cash: Decimal
    equity: Decimal
    buying_power: Decimal
    currency: str = "USD"
    inv_type: str | None = None
    """공격형/안전형 — 프로필 선택의 근거. None이면 기본 프로필을 쓴다."""


@dataclass(frozen=True, slots=True)
class OrderReconciliation:
    """Broker state applied to an already-reserved canonical order."""

    idempotency_key: str
    status: str
    broker_order_id: str | None
    parent_order_id: str | None = None
    stop_leg_order_id: str | None = None
    take_profit_leg_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class FillWrite:
    """One broker fill linked to its canonical order."""

    order_id: int
    side: str
    quantity: int
    price: Decimal
    filled_at: datetime
    broker_fill_id: str


@dataclass(frozen=True, slots=True)
class CompletedFillWrite:
    """One app-owned filled order applied atomically to the local account.

    ``side`` defaults to a buy so every pre-close call site keeps its meaning;
    only a close order has to state it.
    """

    idempotency_key: str
    broker_order_id: str
    broker_fill_id: str
    quantity: int
    price: Decimal
    filled_at: datetime
    side: FillSide = FillSide.BUY


class InsufficientSimulatedCashError(ValueError):
    """A local fill whose notional exceeds durable available cash."""

    def __init__(self, available: Decimal, required: Decimal) -> None:
        """Retain typed amounts while exposing only a stable error message."""
        self.available = available
        self.required = required
        super().__init__("insufficient simulated cash")

    @override
    def __str__(self) -> str:
        """Return a stable non-sensitive boundary message."""
        return "insufficient simulated cash"


@dataclass(frozen=True, slots=True)
class OrderPlanWrite:
    """Role 09's decision for one ticker and cycle, blocked or not."""

    run_id: str
    ticker: str
    cycle_ts: datetime
    trade_date: date
    decision: str
    quantity: int
    account_id: int | None = None
    signal_id: int | None = None
    skipped_reason: str | None = None
    entry_price: Decimal | None = None
    stop_price: Decimal | None = None
    take_profit_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class AccountRiskState:
    """One account's capital and book size at decision time."""

    account_id: int
    cash: Decimal
    equity: Decimal
    open_position_count: int
    inv_type: str | None


@dataclass(frozen=True, slots=True)
class CloseOrderReservation:
    """One idempotent close order awaiting broker execution."""

    signal_id: int
    account_id: int
    ticker: str
    quantity: int
    reference_price: Decimal
    closes_order_id: int
    idempotency_key: str
