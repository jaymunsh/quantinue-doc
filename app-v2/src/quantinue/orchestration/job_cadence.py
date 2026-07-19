"""Whether a background job is due, decided from its own success history.

잡의 주기를 "달력의 어느 요일"이 아니라 **마지막 성공으로부터 며칠**로 정의한다.
요일 고정(예: 유니버스는 월요일)은 그날 앱이 꺼져 있으면 한 주를 통째로
건너뛰지만, 경과일 기준은 화요일에 뒤늦게라도 돈다. 데이터가 하루 늦는 것과
일주일 비는 것은 손해의 크기가 다르다.

슬롯 멱등(``slots.slot_of``)은 분 단위 사이클용이라 여기 쓰지 않는다 — 잡의
최소 단위는 하루이고, 하루 안의 중복 실행은 ``tb_job_run``의 PK가 막는다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date


def is_job_due(*, last_success: date | None, as_of: date, interval_days: int) -> bool:
    """Return whether the job should run on ``as_of``."""
    if interval_days <= 0:
        msg = "interval_days must be a positive number of days"
        raise ValueError(msg)
    if last_success is None:
        # 한 번도 안 돈 잡은 첫 기회에 돈다 — 신규 배포가 주기를 기다리지 않도록.
        return True
    if last_success >= as_of:
        # 미래 날짜의 성공 기록은 "아직 안 돌았다"가 아니다. 시계 역행이나
        # 수동 백필로 생길 수 있고, 이걸 due로 읽으면 같은 날 두 번 돈다.
        return False
    return (as_of - last_success).days >= interval_days
