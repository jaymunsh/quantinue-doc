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

from datetime import date  # noqa: TC003 - pydantic이 런타임에 해석한다
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from quantinue.db.control_room_reads import AccountEquityPoint
    from quantinue.db.domain_records import AccountHoldingRecord
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


class CurvePointView(BaseModel):
    """One day of this account's mark-to-market equity."""

    model_config = ConfigDict(frozen=True)

    trade_date: date
    equity: Decimal


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
    holdings: tuple[HoldingView, ...] = ()
    curve: tuple[CurvePointView, ...] = ()


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
    )


def my_account_view(
    account: UserAccount,
    holdings: tuple[AccountHoldingRecord, ...],
    curve: tuple[AccountEquityPoint, ...],
) -> MyAccountView:
    """Project one account's ledger rows into the page the owner reads."""
    ordered = tuple(sorted(curve, key=lambda point: point.trade_date))
    return MyAccountView(
        broker_account_id=account.broker_account_id,
        inv_type=account.inv_type,
        status=account.status,
        cash=account.cash,
        equity=account.equity,
        return_pct=_return_pct(ordered),
        baseline_date=ordered[0].trade_date if ordered else None,
        holdings=tuple(_holding_view(record) for record in holdings),
        curve=tuple(
            CurvePointView(trade_date=point.trade_date, equity=point.equity) for point in ordered
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
