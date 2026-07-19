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
    business_days_held,
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


def _decide(  # noqa: PLR0913 - 각 인자가 판정에 들어가는 관측 하나다
    *,
    day_range: DailyRange | None = None,
    as_of: date = date(2026, 7, 7),
    hard_event: bool = False,
    time_exit_bdays: int = 10,
    sell_signals: frozenset[str] = frozenset(),
    last_price: Decimal | None = Decimal("105.00"),
    inv_type: str = "aggressive",
) -> ExitReason | None:
    decision = decide_exit(
        _position(inv_type=inv_type),
        DailyObservation(
            day_range=day_range or DailyRange(low=Decimal("95.00"), high=Decimal("110.00")),
            last_price=last_price,
            has_hard_event=hard_event,
            sell_signal_profiles=sell_signals,
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


def test_a_date_beyond_the_calendar_never_forces_a_time_exit() -> None:
    """거래소 캘린더는 유한하다 — 셀 수 없는 보유 기간으로 팔면 안 된다.

    XNYS 캘린더는 현재 2027년까지만 안다. 그 밖의 날짜에서 예외가 나면 청산
    잡 전체가 죽고, 반대로 큰 값을 돌려주면 전 포지션이 한꺼번에 잘린다.
    """
    # Given/When
    held = business_days_held(date(2044, 3, 7), date(2044, 3, 20), calendar=NyseCalendar())

    # Then
    assert held == 0


def test_an_approved_sell_signal_closes_the_position() -> None:
    """3층 soft path — 논지가 무너졌다는 **판단**이 청산으로 이어지는 지점.

    이 연결이 없으면 07이 sell을 내도 아무 일도 일어나지 않는다. 하드 이벤트가
    없는 논지 붕괴(경쟁사 신제품·모멘텀 소멸)는 영원히 시간 청산까지 기다린다.
    """
    # Given/When/Then
    assert _decide(sell_signals=frozenset({"aggressive"})) is ExitReason.THESIS_SOFT


def test_another_personas_sell_does_not_touch_this_position() -> None:
    """판단은 성향별이고 포지션도 성향별이다 — 공격형 계좌를 안전형 판단으로
    팔면 그 계좌는 자기가 동의한 적 없는 매도를 당한다."""
    # Given/When/Then
    assert _decide(inv_type="aggressive", sell_signals=frozenset({"conservative"})) is None


def test_the_bracket_still_wins_over_a_sell_signal() -> None:
    """보호 주문은 장중에 이미 발동했다 — 뒤늦게 안 판단으로 덮으면 시뮬과
    실거래의 결과가 갈린다(D5 우선순위 유지)."""
    # Given/When/Then
    assert _decide(
        day_range=DailyRange(low=Decimal("84.00"), high=Decimal("101.00")),
        sell_signals=frozenset({"aggressive"}),
    ) is ExitReason.STOP


def test_a_hard_event_outranks_a_sell_signal() -> None:
    """상장폐지는 사실이고 sell 시그널은 판단이다 — 사유의 정보량이 다르다."""
    # Given/When/Then
    assert _decide(hard_event=True, sell_signals=frozenset({"aggressive"})) is (
        ExitReason.THESIS_BREAK
    )


def test_a_sell_signal_outranks_the_time_exit() -> None:
    """둘 다 해당할 때 "10일 지나서 팔았다"고 적으면 T+5 학습이 원인을 못 배운다."""
    # Given/When/Then
    assert _decide(
        as_of=date(2026, 7, 20), sell_signals=frozenset({"aggressive"})
    ) is ExitReason.THESIS_SOFT


def test_a_sell_signal_without_a_price_closes_nothing() -> None:
    """시세를 못 받은 날은 아무것도 하지 않는다 — 하드 이벤트와 달리 soft
    판단에는 진입가로 대체할 근거가 없다."""
    # Given/When/Then
    assert _decide(last_price=None, sell_signals=frozenset({"aggressive"})) is None
