"""The signed-in user's own account page — read-only, and honest about gaps.

유저 화면의 규칙은 관제실보다 엄하다. 관제실은 운영자가 보는 화면이라 빈
슬롯도 사실로 그리면 되지만, 이 화면은 **자기 돈**을 보는 자리다. 그래서
두 가지를 지킨다.

1. **총자산의 출처는 하나다.** 계좌 평가액은 원장이 이미 계산해 둔 값을
   그대로 쓴다(D8: 현금 + 보유수량 곱하기 종가). 화면이 보유를 다시 더해 총자산을
   만들면, 봉이 없는 종목 하나 때문에 화면과 원장이 다른 답을 말한다.
2. **원장이 답 못 하는 것은 비워 둔다.** 종가 없는 보유의 평가액, 기준일이
   하루뿐일 때의 수익률 — 둘 다 0으로 그리면 "가치가 없다"·"안 움직였다"로
   읽힌다. 모르는 것과 0은 다르다.

소유권은 여기 없다. ``account_for_user``의 WHERE 절이 갖는다 — 화면이 전부
읽고 거르는 구조였다면 필터를 빠뜨린 화면 하나가 남의 계좌를 보여준다.
"""

from __future__ import annotations

from datetime import date, datetime  # noqa: TC003 - pydantic이 런타임에 해석한다
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from quantinue.api.pipeline_presentation import equity_sparkline

if TYPE_CHECKING:
    from quantinue.db.control_room_reads import AccountEquityPoint
    from quantinue.db.domain_records import (
        AccountHoldingRecord,
        MacroSnapshot,
        TradeTimelineRecord,
    )
    from quantinue.db.users import UserAccount

_PERCENT = Decimal(100)
_CENT = Decimal("0.01")
_MIN_CURVE_POINTS = 2


class HoldingView(BaseModel):
    """One open position as the owner sees it."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    quantity: int
    entry_price: Decimal
    mark_price: Decimal | None
    mark_as_of: date | None
    market_value: Decimal | None
    unrealized_pnl: Decimal | None
    stop_price: Decimal | None
    take_profit_price: Decimal | None


class CurvePointView(BaseModel):
    """One day of this account's mark-to-market equity."""

    model_config = ConfigDict(frozen=True)

    trade_date: date
    equity: Decimal


class BenchmarkPoint(BaseModel):
    """One persisted SPY close used for an owner-visible comparison."""

    model_config = ConfigDict(frozen=True)

    price_date: date
    close: Decimal


class TimelineEntryView(BaseModel):
    """One fill with the judgement that caused it, as the owner reads it."""

    model_config = ConfigDict(frozen=True)

    fill_id: str
    ticker: str
    side: str
    quantity: int
    price: Decimal
    filled_at: datetime
    # 기계적 청산인가 모델 판단인가. 판단 없는 체결도 사실이므로 숨기지 않고
    # "규칙이 팔았다"로 읽히게 한다.
    is_mechanical: bool
    inv_type: str | None
    conviction: str | None
    summary: str | None
    bull_case: str | None
    key_risk: str | None
    verdict_decision: str | None
    objection: str | None


class RegimeView(BaseModel):
    """The market regime the ledger last observed, with its age visible."""

    model_config = ConfigDict(frozen=True)

    regime: str
    risk_score: str
    observed_at: datetime


class MyAccountView(BaseModel):
    """Everything the account page reports about one account."""

    model_config = ConfigDict(frozen=True)

    broker_account_id: str
    inv_type: str | None
    status: str
    cash: Decimal
    equity: Decimal
    return_pct: str | None = None
    baseline_date: date | None = None
    benchmark_return_pct: str | None = None
    excess_return_pct: str | None = None
    holdings: tuple[HoldingView, ...] = ()
    curve: tuple[CurvePointView, ...] = ()
    timeline: tuple[TimelineEntryView, ...] = ()
    regime: RegimeView | None = None
    # 곡선의 SVG 좌표. 기하를 템플릿에서 계산할 수 없어(0으로 나누기·점 하나)
    # 관제실과 **같은 함수**로 만든다.
    curve_points: str = ""


def _holding_view(record: AccountHoldingRecord) -> HoldingView:
    value = None if record.mark_price is None else record.mark_price * record.quantity
    cost = record.entry_price * record.quantity
    return HoldingView(
        ticker=record.ticker,
        quantity=record.quantity,
        entry_price=record.entry_price,
        mark_price=record.mark_price,
        mark_as_of=record.mark_as_of,
        market_value=value,
        unrealized_pnl=None if value is None else value - cost,
        stop_price=record.stop_price,
        take_profit_price=record.take_profit_price,
    )


def _timeline_view(record: TradeTimelineRecord) -> TimelineEntryView:
    return TimelineEntryView(
        fill_id=record.fill_id,
        ticker=record.ticker,
        side=record.side,
        quantity=record.quantity,
        price=record.price,
        filled_at=record.filled_at,
        is_mechanical=record.summary is None,
        inv_type=record.inv_type,
        conviction=None if record.conviction is None else str(record.conviction),
        summary=record.summary,
        bull_case=record.bull_case,
        key_risk=record.key_risk,
        verdict_decision=record.verdict_decision,
        objection=record.objection,
    )


def my_account_view(  # noqa: PLR0913 - each argument is one independent ledger axis
    account: UserAccount,
    holdings: tuple[AccountHoldingRecord, ...],
    curve: tuple[AccountEquityPoint, ...],
    timeline: tuple[TradeTimelineRecord, ...] = (),
    macro: MacroSnapshot | None = None,
    *,
    benchmark: tuple[BenchmarkPoint, ...] = (),
) -> MyAccountView:
    """Project one account's ledger rows into the page the owner reads."""
    ordered = tuple(sorted(curve, key=lambda point: point.trade_date))
    account_return = _return_pct(ordered)
    benchmark_return = _benchmark_return_pct(ordered, benchmark)
    return MyAccountView(
        broker_account_id=account.broker_account_id,
        inv_type=account.inv_type,
        status=account.status,
        cash=account.cash,
        equity=account.equity,
        return_pct=account_return,
        benchmark_return_pct=benchmark_return,
        excess_return_pct=(
            None
            if account_return is None or benchmark_return is None
            else str(
                (Decimal(account_return) - Decimal(benchmark_return)).quantize(
                    _CENT, rounding=ROUND_HALF_UP
                )
            )
        ),
        baseline_date=ordered[0].trade_date if ordered else None,
        holdings=tuple(_holding_view(record) for record in holdings),
        curve=tuple(
            CurvePointView(trade_date=point.trade_date, equity=point.equity) for point in ordered
        ),
        timeline=tuple(_timeline_view(record) for record in timeline),
        curve_points=equity_sparkline([point.equity for point in ordered]),
        regime=(
            None
            if macro is None
            else RegimeView(
                regime=macro.regime,
                risk_score=str(Decimal(str(macro.risk_score)).quantize(_CENT)),
                observed_at=macro.as_of,
            )
        ),
    )


def _return_pct(curve: tuple[AccountEquityPoint, ...]) -> str | None:
    """Measure against the first recorded day, or report nothing.

    기준을 "최초 자본"이 아니라 **원장의 첫 시가평가**로 잡는다. 계좌가 언제
    얼마로 시작했는지는 원장이 따로 들고 있지 않고, 가정해서 넣으면 그 순간
    수익률이 지어낸 숫자가 된다.
    """
    if len(curve) < _MIN_CURVE_POINTS:
        return None
    baseline = curve[0].equity
    if baseline == 0:
        # 0으로 시작한 계좌의 수익률은 정의되지 않는다 — 나누지 않는다.
        return None
    change = (curve[-1].equity - baseline) / baseline * _PERCENT
    return str(change.quantize(_CENT, rounding=ROUND_HALF_UP))


def _benchmark_return_pct(
    curve: tuple[AccountEquityPoint, ...], benchmark: tuple[BenchmarkPoint, ...]
) -> str | None:
    if len(curve) < _MIN_CURVE_POINTS:
        return None
    closes = {point.price_date: point.close for point in benchmark}
    first = closes.get(curve[0].trade_date)
    last = closes.get(curve[-1].trade_date)
    if first is None or last is None or first == 0:
        return None
    change = (last - first) / first * _PERCENT
    return str(change.quantize(_CENT, rounding=ROUND_HALF_UP))
