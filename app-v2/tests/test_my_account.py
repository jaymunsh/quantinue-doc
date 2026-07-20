"""Phase W2-1: the user's own account page, and what it may not invent.

이 화면이 고정하는 것은 두 가지다. **화면의 숫자가 원장의 숫자와 같은가**
(§1-1이 정확히 그 대조를 안 해서 생긴 결함이다), 그리고 **원장이 답하지
못하는 것을 화면이 지어내지 않는가**.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from quantinue.api.my_account import my_account_view
from quantinue.db.control_room_reads import AccountEquityPoint
from quantinue.db.domain_records import AccountHoldingRecord, TradeTimelineRecord
from quantinue.db.users import UserAccount

_ACCOUNT = UserAccount(
    account_id=7,
    broker_account_id="DEMO-AGGRESSIVE-01",
    inv_type="aggressive",
    status="active",
    cash=Decimal("30734.62"),
    equity=Decimal("150000.00"),
)


def _holding(ticker: str, *, quantity: int, entry: str, mark: str | None) -> AccountHoldingRecord:
    return AccountHoldingRecord(
        ticker=ticker,
        quantity=quantity,
        entry_price=Decimal(entry),
        mark_price=None if mark is None else Decimal(mark),
        mark_as_of=None if mark is None else date(2026, 7, 20),
        stop_price=Decimal("85.00"),
        take_profit_price=Decimal("140.00"),
    )


def test_the_page_reports_the_ledger_numbers_unchanged() -> None:
    """화면이 원장을 다시 계산하지 않는다 — 총자산의 출처는 하나다."""
    # Given
    holdings = (_holding("NVDA", quantity=10, entry="100.00", mark="120.00"),)

    # When
    view = my_account_view(_ACCOUNT, holdings, ())

    # Then
    assert view.cash == Decimal("30734.62")
    assert view.equity == Decimal("150000.00")


def test_a_holding_shows_what_it_is_worth_now() -> None:
    # Given
    holdings = (_holding("NVDA", quantity=10, entry="100.00", mark="120.00"),)

    # When
    holding = my_account_view(_ACCOUNT, holdings, ()).holdings[0]

    # Then
    assert holding.market_value == Decimal("1200.00")
    assert holding.unrealized_pnl == Decimal("200.00")


def test_a_holding_without_a_close_price_is_not_valued() -> None:
    """종가가 없는 보유에 평가액을 지어내면 화면이 원장보다 많이 안다."""
    # Given
    holdings = (_holding("HALTED", quantity=10, entry="100.00", mark=None),)

    # When
    holding = my_account_view(_ACCOUNT, holdings, ()).holdings[0]

    # Then
    assert holding.market_value is None
    assert holding.unrealized_pnl is None


def test_the_return_is_measured_against_the_first_recorded_day() -> None:
    """수익률의 기준은 원장의 첫 시가평가다 — 최초 자본을 가정하지 않는다."""
    # Given
    curve = (
        AccountEquityPoint(account_id=7, trade_date=date(2026, 7, 18), equity=Decimal(100000)),
        AccountEquityPoint(account_id=7, trade_date=date(2026, 7, 20), equity=Decimal(110000)),
    )

    # When
    view = my_account_view(_ACCOUNT, (), curve)

    # Then
    assert view.return_pct == "10.00"


def test_a_single_day_of_history_yields_no_return() -> None:
    """하루치 기록으로 수익률을 적으면 0%가 "안 움직였다"로 읽힌다."""
    # Given
    curve = (
        AccountEquityPoint(account_id=7, trade_date=date(2026, 7, 20), equity=Decimal(100000)),
    )

    # When
    view = my_account_view(_ACCOUNT, (), curve)

    # Then
    assert view.return_pct is None


def _trade(ticker: str, *, side: str, summary: str | None) -> TradeTimelineRecord:
    return TradeTimelineRecord(
        fill_id=f"fill-{ticker}",
        ticker=ticker,
        side=side,
        quantity=10,
        price=Decimal("120.00"),
        filled_at=datetime(2026, 7, 20, 14, tzinfo=UTC),
        order_type="bracket" if side == "buy" else "close",
        inv_type="aggressive" if summary else None,
        conviction=Decimal("0.82") if summary else None,
        summary=summary,
        bull_case="상대강도 상위" if summary else None,
        key_risk="국면 반전" if summary else None,
        verdict_decision="approve" if summary else None,
        objection=None,
    )


def test_a_trade_carries_the_judgement_that_caused_it() -> None:
    """체결만 보여주면 "무엇을 샀나"에는 답하고 "왜 샀나"에는 못 답한다."""
    # Given
    trades = (_trade("NVDA", side="buy", summary="추세 초입"),)

    # When
    entry = my_account_view(_ACCOUNT, (), (), trades).timeline[0]

    # Then
    assert entry.summary == "추세 초입"
    assert entry.bull_case == "상대강도 상위"
    assert entry.verdict_decision == "approve"
    assert entry.is_mechanical is False


def test_a_mechanical_exit_is_marked_as_one() -> None:
    """브래킷·시간 청산은 모델 판단 없이 체결된다 — 그 사실을 숨기지 않는다."""
    # Given
    trades = (_trade("NVDA", side="sell", summary=None),)

    # When
    entry = my_account_view(_ACCOUNT, (), (), trades).timeline[0]

    # Then
    assert entry.is_mechanical is True
    assert entry.summary is None
