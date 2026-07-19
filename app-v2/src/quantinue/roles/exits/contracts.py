"""Pure exit rules — the three-layer close described in the redesign §4.

한 포지션이 우리 손을 떠나는 경로는 셋뿐이다:

1. **브래킷** — 손절·익절 선이 그날의 고저에 닿았다. 실 브로커였다면 거래소가
   장중에 처리했을 일을, 로컬 시뮬에서는 일봉으로 근사한다(D1·D5).
2. **논지 붕괴** — 상장폐지·거래정지 같은 하드 이벤트. 가격과 무관하게 즉시.
3. **시간** — ``exits.time_exit_bdays`` 영업일이 지나도록 논지가 실현되지 않았다.
   T+5 지평의 전략이 무기한 보유로 흘러가는 걸 막는 마지막 빗장이다.

전부 순수 함수다. DB도 브로커도 모른다 — 그래야 "왜 팔았는가"를 픽스처로
전부 재현할 수 있고, 청산 잡은 이 판정을 집행하기만 하면 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import TYPE_CHECKING

from exchange_calendars.errors import DateOutOfBounds

from quantinue.broker.bracket_trigger import BracketLeg, evaluate_bracket

if TYPE_CHECKING:
    from datetime import date
    from decimal import Decimal

    from quantinue.broker.bracket_trigger import DailyRange
    from quantinue.core.market_calendar import NyseCalendar


@unique
class ExitReason(StrEnum):
    """Why a position was closed — persisted, so it becomes learning input."""

    STOP = "stop"
    TAKE_PROFIT = "take_profit"
    TIME = "time"
    THESIS_BREAK = "thesis_break"
    # 하드 이벤트(사실)와 이름을 가르는 이유: 같은 "논지 붕괴"라도 SEC 폼이
    # 판정한 것과 모델이 판단한 것은 신뢰도가 다르고, role_11이 T+5에 채점할 때
    # 둘을 섞으면 "우리 판단이 옳았나"를 물을 수 없다.
    THESIS_SOFT = "thesis_soft"


@dataclass(frozen=True, slots=True)
class OpenPosition:
    """One filled entry that nothing has closed yet.

    ``filled_on``이 시간 청산의 기준이다. 주문 생성일이 아니라 **체결일**인
    이유: 체결되지 않은 계획은 보유가 아니라서 보유 기간을 세면 안 된다.
    """

    order_id: int
    signal_id: int
    account_id: int
    ticker: str
    quantity: int
    entry_price: Decimal
    stop_price: Decimal | None
    take_profit_price: Decimal | None
    filled_on: date
    # 진입을 결정한 성향. 청산은 새 판단이 아니라 **끝난 논지의 마무리**이므로
    # 매수와 같은 페르소나로 기록돼야 role_11이 자기 판단의 결말을 찾을 수 있다.
    # 계좌가 아니라 진입 시그널에서 가져오는 이유: tb_account.inv_type은
    # nullable이라 없을 수 있는데, 진입 시그널의 inv_type은 NOT NULL이다 —
    # 즉 추측하지 않고도 항상 답이 있다.
    inv_type: str = "aggressive"


@dataclass(frozen=True, slots=True)
class DailyObservation:
    """Everything today told us about one ticker.

    셋을 한 덩어리로 묶은 이유: 이들은 늘 같은 시점·같은 종목의 관측이라
    따로 넘기면 서로 다른 날의 값이 섞여 들어갈 수 있다. 전부 Optional인 것도
    의도다 — 수집이 실패한 날을 "값이 없다"로 정직하게 표현해야 한다.
    """

    day_range: DailyRange | None = None
    last_price: Decimal | None = None
    has_hard_event: bool = False
    # 오늘 이 종목에 **승인된 매도 판단**을 낸 성향들. 종목이 아니라 성향별인
    # 이유는 포지션도 성향별이기 때문이다 — 공격형 계좌를 안전형의 판단으로
    # 팔면 그 계좌는 동의한 적 없는 매도를 당한다.
    sell_signal_profiles: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ExitDecision:
    """A position to close today, with the reason and the price it exits at."""

    position: OpenPosition
    reason: ExitReason
    reference_price: Decimal


def business_days_held(
    filled_on: date, as_of: date, *, calendar: NyseCalendar
) -> int:
    """Count exchange sessions elapsed since the fill.

    달력일이 아니라 영업일이다. 달력일로 세면 주말·휴장이 낀 보유가 실제보다
    오래 산 것처럼 보여 정상 포지션이 일찍 잘린다. M1 캘린더를 쓰는 이유.

    거래소 캘린더는 유한한 구간만 안다. 그 밖의 날짜면 **0을 돌려준다** —
    보유 기간을 셀 수 없다는 뜻이고, 셀 수 없을 때 시간 청산을 발동시키면
    모르는 것을 근거로 파는 셈이 된다. 같은 이유로 role_09의 갭 가드도
    범위 밖에서는 측정을 포기한다.
    """
    if as_of <= filled_on:
        return 0
    held = 0
    cursor = filled_on
    try:
        while cursor < as_of:
            cursor = calendar.add_business_days(cursor, 1)
            if cursor <= as_of:
                held += 1
    except (ValueError, DateOutOfBounds):
        return 0
    return held


def decide_exit(
    position: OpenPosition,
    observation: DailyObservation,
    *,
    as_of: date,
    time_exit_bdays: int,
    calendar: NyseCalendar,
) -> ExitDecision | None:
    """Return the exit this position earned today, or None to keep holding.

    **우선순위: 브래킷 > 하드 이벤트 > 판단(soft) > 시간.**

    브래킷이 가장 앞인 이유는 시간 순서다 — 보호 주문은 거래소에 상주하며
    장중에 발동하는데, 우리가 악재를 수집하는 건 그 뒤다. 실 브로커였다면 이미
    체결됐을 일을 뒤늦게 알게 된 악재로 덮으면 시뮬과 실거래의 결과가 갈리고,
    나중에 페이퍼로 전환할 때(로드맵 R1) 성과가 설명 없이 어긋난다.

    논지 붕괴가 시간보다 앞인 이유는 사유의 정보량이다. 둘 다 해당할 때
    "10일 지나서 팔았다"고 기록하면 T+5 학습이 실제 원인을 못 배운다.
    """
    if observation.day_range is not None:
        leg = _triggered_leg(position, observation.day_range)
        if leg is not None:
            return ExitDecision(
                position=position,
                reason=(
                    ExitReason.STOP if leg is BracketLeg.STOP else ExitReason.TAKE_PROFIT
                ),
                # 대기 주문은 자기 가격에 체결된다. 종가로 찍으면 손익이 왜곡된다.
                # (갭으로 그 가격을 건너뛴 경우의 슬리피지는 로드맵 R7.)
                reference_price=_leg_price(position, leg),
            )
    if observation.has_hard_event:
        # 악재는 시세 없이도 판단된다. 기준가가 없으면 진입가로 대체한다 —
        # 지어낸 시세보다 "판단 시점에 아는 마지막 진실"이 낫다.
        return ExitDecision(
            position=position,
            reason=ExitReason.THESIS_BREAK,
            reference_price=observation.last_price or position.entry_price,
        )
    if observation.last_price is None:
        # 시세를 못 받은 날은 아무것도 하지 않는다 — 관측 부재는 신호가 아니다.
        # 아래 두 갈래(soft 판단·시간)는 하드 이벤트와 달리 진입가로 대체할
        # 근거가 없다: 판단은 그날 종가를 보고 내려졌고, 시간 청산은 "지금
        # 얼마인지"를 모르면 손익을 기록할 수 없다.
        return None
    if position.inv_type in observation.sell_signal_profiles:
        # 3층 soft path — 07의 매도 판단이 크리틱을 통과한 경우다. 시간 청산보다
        # 앞에 두는 이유는 사유의 정보량이다: 둘 다 해당할 때 "10일 지나서
        # 팔았다"고 기록하면 T+5 학습이 실제 원인을 못 배운다.
        return ExitDecision(
            position=position,
            reason=ExitReason.THESIS_SOFT,
            reference_price=observation.last_price,
        )
    if business_days_held(position.filled_on, as_of, calendar=calendar) >= time_exit_bdays:
        return ExitDecision(
            position=position,
            reason=ExitReason.TIME,
            reference_price=observation.last_price,
        )
    return None


def _triggered_leg(position: OpenPosition, day_range: DailyRange) -> BracketLeg | None:
    """Evaluate the bracket only when the position actually carries both legs."""
    if position.stop_price is None or position.take_profit_price is None:
        return None
    return evaluate_bracket(
        day_range,
        stop=position.stop_price,
        take_profit=position.take_profit_price,
    )


def _leg_price(position: OpenPosition, leg: BracketLeg) -> Decimal:
    """Return the resting price of the leg that fired."""
    if leg is BracketLeg.STOP:
        return position.stop_price or position.entry_price
    return position.take_profit_price or position.entry_price
