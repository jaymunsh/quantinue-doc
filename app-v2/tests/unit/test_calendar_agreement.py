"""Phase 5: the two trading calendars must not drift apart.

The system carries two: `core/market_calendar.NyseCalendar` (exchange_calendars
XNYS), used by every job, and `role_11_reviewer/calendar` (hand-rolled holiday
rules), used by the T+5 review path. The redesign expected the second to die
with the old runner — it did not, because `api/reviews` and `api/review_runtime`
call it directly.

통합은 미뤘다: 리뷰 날짜 산술을 갈아끼우는 일이고, 리뷰 경로는 방금 러너에서
떼어낸 참이라 같은 세션에서 두 번 흔들 이유가 없다. 대신 **어긋나는 순간을
잡는 테스트**를 둔다. 중복 자체보다 위험한 것은 두 캘린더가 서로 다른 날을
휴장이라고 말하기 시작하는 것이다 — 그때 잡은 돌고 리뷰는 안 도는(또는 그
반대의) 날이 생기고, 원인을 찾을 단서가 없다.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from quantinue.core.market_calendar import NyseCalendar
from quantinue.roles.role_11_reviewer.calendar import UsEquityTradingCalendar

# 비교 창은 XNYS 데이터가 실제로 덮는 구간 안이어야 한다 — 라이브러리의
# 마지막 세션을 넘겨 물으면 DateOutOfBounds가 난다(청산 잡이 예전에 같은
# 경계에서 예외를 맞았고, 그때 business_days_held를 0 반환으로 고쳤다).
def _days(start: date, end: date) -> list[date]:
    span = (end - start).days
    return [start + timedelta(days=offset) for offset in range(span + 1)]


@pytest.mark.parametrize(
    ("start", "end"),
    [(date(2026, 1, 1), date(2026, 12, 31)), (date(2027, 1, 1), date(2027, 6, 30))],
)
def test_both_calendars_agree_on_every_session_in_range(start: date, end: date) -> None:
    """한쪽만 휴장이라고 말하는 날이 생기면 잡과 리뷰가 다른 달력을 산다."""
    # Given
    exchange = NyseCalendar()
    review = UsEquityTradingCalendar()
    holidays = review.holidays(start.year)

    # When
    disagreements = [
        day
        for day in _days(start, end)
        # 주말은 양쪽 다 자명하게 휴장이라 비교 대상이 아니다.
        if day.weekday() < 5  # noqa: PLR2004 - 토·일 제외
        and exchange.is_trading_day(day) is (day in holidays)
    ]

    # Then
    assert disagreements == []
