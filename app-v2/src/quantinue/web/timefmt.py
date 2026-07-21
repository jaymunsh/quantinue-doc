"""Clock formatting for the screens — the operator reads Seoul time.

원장은 UTC로 적고 슬롯은 **뉴욕 날짜**로 센다. 둘 다 바꾸지 않는다 — 앞은
저장 규약이고 뒤는 도메인 사실(D4 정규장 전용)이라 화면 사정으로 흔들 것이
아니다. 바꾸는 것은 **읽는 자리**뿐이다: 이 앱을 켜고 끄는 사람이 서울에
있으므로, 사람이 시계를 읽는 곳에서는 KST로 적는다.

세 시간대가 한 화면에 필요한 이유가 있다:
  · **서울** — 운영자가 사는 시간. "지금 무슨 일이 벌어지나"의 기준.
  · **뉴욕** — 슬롯 날짜의 근거. 뉴욕 자정이 KST 13:00이라 그 시각에
    "오늘"이 바뀐다 — 이 관계를 모르면 일일 안내 도착 시각이 수수께끼가 된다.
  · **UTC** — 로그가 말하는 시간. 원장과 화면을 대조할 때 필요하다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from datetime import datetime

SEOUL = ZoneInfo("Asia/Seoul")
NEW_YORK = ZoneInfo("America/New_York")
UTC_ZONE = ZoneInfo("UTC")


def kst_time(value: datetime | None) -> str:
    """Return one moment as Seoul wall-clock time, seconds included."""
    if value is None:
        return "—"
    return value.astimezone(SEOUL).strftime("%H:%M:%S")


def kst_stamp(value: datetime | None) -> str:
    """Return one moment as a Seoul date and time, for cross-day reading."""
    if value is None:
        return "—"
    return value.astimezone(SEOUL).strftime("%m-%d %H:%M")


def register_filters(environment: object) -> None:
    """Expose the clock filters to every template."""
    filters = environment.filters  # type: ignore[attr-defined]
    filters["kst"] = kst_time
    filters["kst_stamp"] = kst_stamp
