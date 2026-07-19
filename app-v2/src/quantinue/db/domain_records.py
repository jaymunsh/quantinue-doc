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
    # 기본값이 없는 이유: 이 열은 `UNIQUE (ticker, cycle_ts, inv_type)`의 축이라
    # 어느 페르소나가 판단했는지가 곧 행의 정체성이다. 기본값을 두면 부르는
    # 쪽이 말하지 않아도 통과하는데, 실제로 그렇게 해서 aggressive로 돌린
    # 판단이 원장에 전부 conservative로 찍혀 있었다 — 성향 2종 팬아웃이
    # 붙는 순간 두 페르소나가 같은 행을 덮어쓴다. 말하지 않으면 못 쓰게 한다.
    inv_type: str
    disclosure_score: Decimal = Decimal(0)
    news_score: Decimal = Decimal(0)
    signal_consensus: int = 0


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


@dataclass(frozen=True, slots=True)
class DailyBarWrite:
    """One exchange session's OHLCV for one ticker.

    ``source``를 함께 담는 이유: 시세 소스가 폴백 체인(Alpaca → Stooq → …)이라
    같은 날 다른 소스가 섞일 수 있고, 값이 이상할 때 어디서 왔는지 물을 수
    있어야 한다.
    """

    trade_date: date
    ticker: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    source: str


@dataclass(frozen=True, slots=True)
class RawDisclosureWrite:
    """One filing from the day's whole-market index, matched to a ticker.

    ``tb_disclosure``(채점 결과)와 따로 두는 이유: 그쪽은
    ``(trade_date, ticker) → tb_daily_pick`` FK를 걸어 **그날 분석 대상이 아닌
    종목에는 행을 넣을 수 없다**. 그런데 일괄 수집이 노리는 것이 정확히 그
    바깥이다 — 스크리너에서 탈락한 보유 종목의 상장폐지 공시. 원시 원장은
    픽과 무관하게 받고, 채점은 분석 대상에만 한다.
    """

    filing_no: str
    trade_date: date
    ticker: str
    cik: str
    form_type: str
    company_name: str
    source_ref: str
    event_type: str | None
    is_hard_event: bool
