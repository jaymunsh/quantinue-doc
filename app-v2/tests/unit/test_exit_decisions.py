"""Phase 1c: which open positions must be closed today, and why."""

from datetime import date
from decimal import Decimal

import pytest

from quantinue.broker.bracket_trigger import DailyRange
from quantinue.core.market_calendar import NyseCalendar
from quantinue.roles.exits import (
    DailyObservation,
    ExitReason,
    OpenPosition,
    decide_exit,
)

_ENTRY = date(2026, 7, 6)


def _position(**overrides: object) -> OpenPosition:
    fields: dict[str, object] = {
        "order_id": 1,
        "signal_id": 1,
        "account_id": 1,
        "ticker": "NVDA",
        "quantity": 2,
        "entry_price": Decimal("100.00"),
        "stop_price": Decimal("85.00"),
        "take_profit_price": Decimal("120.00"),
        "filled_on": _ENTRY,
    }
    fields.update(overrides)
    return OpenPosition(**fields)  # pyright: ignore[reportArgumentType]


def _decide(
    *,
    day_range: DailyRange | None = None,
    as_of: date = date(2026, 7, 7),
    hard_event: bool = False,
    time_exit_bdays: int = 10,
) -> ExitReason | None:
    decision = decide_exit(
        _position(),
        DailyObservation(
            day_range=day_range or DailyRange(low=Decimal("95.00"), high=Decimal("110.00")),
            last_price=Decimal("105.00"),
            has_hard_event=hard_event,
        ),
        as_of=as_of,
        time_exit_bdays=time_exit_bdays,
        calendar=NyseCalendar(),
    )
    return None if decision is None else decision.reason


def test_a_quiet_day_inside_the_horizon_closes_nothing() -> None:
    # Given/When/Then
    assert _decide() is None


def test_the_stop_being_touched_closes_the_position() -> None:
    # Given/When/Then
    assert _decide(day_range=DailyRange(low=Decimal("84.00"), high=Decimal("101.00"))) is (
        ExitReason.STOP
    )


def test_reaching_the_target_closes_the_position() -> None:
    # Given/When/Then
    assert _decide(day_range=DailyRange(low=Decimal("99.00"), high=Decimal("121.00"))) is (
        ExitReason.TAKE_PROFIT
    )


def test_holding_past_the_time_horizon_closes_the_position() -> None:
    """exits.time_exit_bdays — 영업일 기준(M1 캘린더), 달력일이 아니다."""
    # Given: 2026-07-06 진입 + 10 영업일 = 2026-07-20
    # When/Then
    assert _decide(as_of=date(2026, 7, 20)) is ExitReason.TIME
    assert _decide(as_of=date(2026, 7, 17)) is None


def test_a_hard_event_closes_the_position_immediately() -> None:
    # Given/When/Then
    assert _decide(hard_event=True) is ExitReason.THESIS_BREAK


def test_a_triggered_bracket_outranks_a_hard_event_on_the_same_day() -> None:
    """브래킷은 거래소에 상주하며 장중에 발동한다 — 우리가 악재를 수집하기 전이다.

    실 브로커였다면 이미 체결됐을 일을 뒤늦은 논지 붕괴로 덮으면 시뮬과 실거래의
    결과가 갈린다.
    """
    # Given/When/Then
    assert _decide(
        day_range=DailyRange(low=Decimal("84.00"), high=Decimal("101.00")),
        hard_event=True,
    ) is ExitReason.STOP


def test_a_hard_event_outranks_the_time_horizon() -> None:
    """둘 다 해당하면 사유는 더 구체적인 쪽을 남긴다 — 학습 입력이 되기 때문."""
    # Given/When/Then
    assert _decide(as_of=date(2026, 7, 20), hard_event=True) is ExitReason.THESIS_BREAK


def test_a_position_without_a_daily_bar_is_left_alone() -> None:
    """시세를 못 받은 날 청산을 지어내면 안 된다 — 관측 부재는 신호가 아니다."""
    # Given/When
    decision = decide_exit(
        _position(),
        DailyObservation(),
        as_of=date(2026, 7, 7),
        time_exit_bdays=10,
        calendar=NyseCalendar(),
    )

    # Then
    assert decision is None


def test_a_hard_event_closes_even_without_a_daily_bar() -> None:
    """악재는 시세가 없어도 판단할 수 있다 — 기준가는 진입가로 대체한다."""
    # Given/When
    decision = decide_exit(
        _position(),
        DailyObservation(has_hard_event=True),
        as_of=date(2026, 7, 7),
        time_exit_bdays=10,
        calendar=NyseCalendar(),
    )

    # Then
    assert decision is not None
    assert decision.reason is ExitReason.THESIS_BREAK
    assert decision.reference_price == Decimal("100.00")


@pytest.mark.parametrize(
    ("reason", "day_range", "expected"),
    [
        (ExitReason.STOP, DailyRange(low=Decimal("84.00"), high=Decimal("101.00")), "85.00"),
        (
            ExitReason.TAKE_PROFIT,
            DailyRange(low=Decimal("99.00"), high=Decimal("121.00")),
            "120.00",
        ),
    ],
)
def test_a_triggered_leg_fills_at_its_own_price_not_the_last_trade(
    reason: ExitReason, day_range: DailyRange, expected: str
) -> None:
    """대기 주문은 자기 가격에 체결된다 — 종가로 찍으면 손익이 왜곡된다."""
    # Given/When
    decision = decide_exit(
        _position(),
        DailyObservation(day_range=day_range, last_price=Decimal("105.00")),
        as_of=date(2026, 7, 7),
        time_exit_bdays=10,
        calendar=NyseCalendar(),
    )

    # Then
    assert decision is not None
    assert decision.reason is reason
    assert decision.reference_price == Decimal(expected)
