"""Pure contracts and accounting for the phase-one simulated buy-only account."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum, unique
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

from quantinue.db.domain_records import CompletedBuyWrite, InsufficientSimulatedCashError

if TYPE_CHECKING:
    from datetime import datetime

_CENT: Final = Decimal("0.01")
_ALLOCATION: Final = Decimal("0.0001")


@unique
class MarkSource(StrEnum):
    """Truthful source used to value one simulated position."""

    COMPLETED_RUN = "completed_run"
    LATEST_FILL = "latest_fill"


@unique
class RealizedPnlStatus(StrEnum):
    """Whether the ledger has closed anything to realize profit against."""

    NOT_APPLICABLE_BUY_ONLY = "not_applicable_buy_only"
    AVAILABLE = "available"


@unique
class FillSide(StrEnum):
    """Direction of one local fill, mirroring the ``tb_fill.side`` constraint."""

    BUY = "buy"
    SELL = "sell"


@unique
class SimulatedOrderStatus(StrEnum):
    """Observable lifecycle states for an app-owned local order."""

    PLANNED = "planned"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SimulatedOrder:
    """App-owned local order history record without brokerage claims."""

    order_id: str
    ticker: str
    quantity: int
    reference_price: Decimal
    status: SimulatedOrderStatus
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SimulatedFill:
    """Unique local fill used by simulated-account accounting.

    ``side`` defaults to a buy so that every pre-close call site keeps its
    meaning; a close order is the only writer that must state it.
    """

    fill_id: str
    order_id: str
    ticker: str
    quantity: int
    price: Decimal
    filled_at: datetime
    side: FillSide = FillSide.BUY


@dataclass(frozen=True, slots=True)
class PortfolioMark:
    """Observed price and provenance used for one position valuation."""

    ticker: str
    price: Decimal
    source: MarkSource
    as_of: datetime


@dataclass(frozen=True, slots=True)
class SimulatedAccount:
    """Derived local account totals in USD."""

    opening_cash: Decimal
    current_cash: Decimal
    equity: Decimal
    buying_power: Decimal
    currency: str = "USD"


@dataclass(frozen=True, slots=True)
class SimulatedPosition:
    """Derived buy-only holding valued with an observable mark."""

    ticker: str
    quantity: int
    average_cost: Decimal
    cost_basis: Decimal
    mark: PortfolioMark
    market_value: Decimal
    unrealized_pnl: Decimal
    allocation: Decimal


@dataclass(frozen=True, slots=True)
class SimulatedPortfolioSnapshot:
    """Read model for the local account, holdings, orders, and unique fills."""

    account: SimulatedAccount
    positions: tuple[SimulatedPosition, ...]
    orders: tuple[SimulatedOrder, ...]
    fills: tuple[SimulatedFill, ...]
    realized_pnl: Decimal | None = None
    realized_pnl_status: RealizedPnlStatus = RealizedPnlStatus.NOT_APPLICABLE_BUY_ONLY


@dataclass(frozen=True, slots=True)
class AccountPortfolio:
    """One account's identity paired with its portfolio snapshot."""

    account_id: int
    broker_account_id: str
    inv_type: str | None
    snapshot: SimulatedPortfolioSnapshot


@runtime_checkable
class SimulatedOrderRecorder(Protocol):
    """Atomic write boundary for app-owned local simulated orders and fills."""

    async def record_simulated_order(
        self,
        order: SimulatedOrder,
        fill: SimulatedFill | None,
    ) -> None:
        """Record one order and its optional unique buy fill exactly once."""
        ...


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _signed_notional(fill: SimulatedFill) -> Decimal:
    """Return cash consumed by one fill: a buy spends it, a sell returns it."""
    notional = Decimal(fill.quantity) * fill.price
    return -notional if fill.side is FillSide.SELL else notional


def _realized_pnl(fills: tuple[SimulatedFill, ...]) -> Decimal | None:
    """Book each sale against its ticker's average buy cost, or None if unsold."""
    realized = Decimal(0)
    sold_anything = False
    for ticker in sorted({fill.ticker for fill in fills}):
        ticker_fills = tuple(fill for fill in fills if fill.ticker == ticker)
        bought = tuple(fill for fill in ticker_fills if fill.side is FillSide.BUY)
        sold = tuple(fill for fill in ticker_fills if fill.side is FillSide.SELL)
        bought_quantity = sum(fill.quantity for fill in bought)
        if not sold or bought_quantity == 0:
            continue
        sold_anything = True
        bought_cost = sum(
            (Decimal(fill.quantity) * fill.price for fill in bought),
            start=Decimal(0),
        )
        average_cost = bought_cost / Decimal(bought_quantity)
        realized += sum(
            (Decimal(fill.quantity) * (fill.price - average_cost) for fill in sold),
            start=Decimal(0),
        )
    return _money(realized) if sold_anything else None


def ensure_fill_is_affordable(
    opening_cash: Decimal,
    existing_fills: tuple[SimulatedFill, ...],
    candidate: SimulatedFill,
) -> None:
    """Reject a new unique fill before any process-local ledger mutation."""
    committed = sum(
        (Decimal(fill.quantity) * fill.price for fill in existing_fills), start=Decimal(0)
    )
    required = Decimal(candidate.quantity) * candidate.price
    available = opening_cash - committed
    if required > available:
        raise InsufficientSimulatedCashError(available=available, required=required)


def completed_buy_records(
    ticker: str,
    reference_price: Decimal,
    value: CompletedBuyWrite,
) -> tuple[SimulatedOrder, SimulatedFill]:
    """Map the shared completed-buy contract to local immutable records."""
    return (
        SimulatedOrder(
            order_id=value.broker_order_id,
            ticker=ticker,
            quantity=value.quantity,
            reference_price=reference_price,
            status=SimulatedOrderStatus.FILLED,
            created_at=value.filled_at,
        ),
        SimulatedFill(
            fill_id=value.broker_fill_id,
            order_id=value.broker_order_id,
            ticker=ticker,
            quantity=value.quantity,
            price=value.price,
            filled_at=value.filled_at,
        ),
    )


def project_buy_only_portfolio(
    opening_cash: Decimal,
    orders: tuple[SimulatedOrder, ...],
    fills: tuple[SimulatedFill, ...],
    marks: tuple[PortfolioMark, ...],
) -> SimulatedPortfolioSnapshot:
    """Project unique buy fills into a deterministic local portfolio snapshot."""
    unique_fills_by_id: dict[str, SimulatedFill] = {}
    for fill in fills:
        _ = unique_fills_by_id.setdefault(fill.fill_id, fill)
    unique_fills = tuple(unique_fills_by_id.values())
    total_cost = sum(
        (_signed_notional(fill) for fill in unique_fills),
        start=Decimal(0),
    )
    if total_cost > opening_cash:
        raise InsufficientSimulatedCashError(available=opening_cash, required=total_cost)
    completed_marks: dict[str, PortfolioMark] = {}
    for mark in marks:
        match mark.source:
            case MarkSource.COMPLETED_RUN:
                current = completed_marks.get(mark.ticker)
                if current is None or mark.as_of > current.as_of:
                    completed_marks[mark.ticker] = mark
            case MarkSource.LATEST_FILL:
                continue
    positions_without_allocation: list[tuple[str, int, Decimal, PortfolioMark]] = []
    for ticker in sorted({fill.ticker for fill in unique_fills}):
        ticker_fills = tuple(fill for fill in unique_fills if fill.ticker == ticker)
        bought = tuple(fill for fill in ticker_fills if fill.side is FillSide.BUY)
        bought_quantity = sum(fill.quantity for fill in bought)
        sold_quantity = sum(
            fill.quantity for fill in ticker_fills if fill.side is FillSide.SELL
        )
        quantity = bought_quantity - sold_quantity
        if quantity <= 0:
            continue
        bought_cost = sum(
            (Decimal(fill.quantity) * fill.price for fill in bought),
            start=Decimal(0),
        )
        # 평균원가법 — 매도분이 자기 몫의 원가를 들고 나가므로 남은 수량은
        # 산 값 그대로 평가된다. 선입선출(FIFO)이 아닌 이유: T+5 회전이라
        # 같은 종목을 여러 번 나눠 담는 경우가 드물어 차이가 거의 없고,
        # 로트 단위 추적을 안 해도 되어 원장이 단순해진다.
        cost_basis = bought_cost * Decimal(quantity) / Decimal(bought_quantity)
        latest_fill = max(ticker_fills, key=lambda fill: fill.filled_at)
        mark = completed_marks.get(
            ticker,
            PortfolioMark(
                ticker=ticker,
                price=latest_fill.price,
                source=MarkSource.LATEST_FILL,
                as_of=latest_fill.filled_at,
            ),
        )
        positions_without_allocation.append((ticker, quantity, cost_basis, mark))

    current_cash = _money(opening_cash - total_cost)
    total_market_value = sum(
        (Decimal(quantity) * mark.price for _, quantity, _, mark in positions_without_allocation),
        start=Decimal(0),
    )
    equity = _money(current_cash + total_market_value)
    positions = tuple(
        SimulatedPosition(
            ticker=ticker,
            quantity=quantity,
            average_cost=_money(cost_basis / Decimal(quantity)),
            cost_basis=_money(cost_basis),
            mark=mark,
            market_value=_money(Decimal(quantity) * mark.price),
            unrealized_pnl=_money(Decimal(quantity) * mark.price - cost_basis),
            allocation=(Decimal(quantity) * mark.price / equity).quantize(
                _ALLOCATION,
                rounding=ROUND_HALF_UP,
            ),
        )
        for ticker, quantity, cost_basis, mark in positions_without_allocation
    )
    account = SimulatedAccount(
        opening_cash=_money(opening_cash),
        current_cash=current_cash,
        equity=equity,
        buying_power=current_cash,
    )
    realized = _realized_pnl(unique_fills)
    return SimulatedPortfolioSnapshot(
        account=account,
        positions=positions,
        orders=orders,
        fills=unique_fills,
        realized_pnl=realized,
        realized_pnl_status=(
            RealizedPnlStatus.NOT_APPLICABLE_BUY_ONLY
            if realized is None
            else RealizedPnlStatus.AVAILABLE
        ),
    )
