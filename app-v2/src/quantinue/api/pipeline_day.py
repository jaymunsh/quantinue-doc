"""Assemble one pipeline day for the control room from the job ledger.

읽기 넷을 한 화면 모델로 모으는 얇은 층이다. 순수 투영(``pipeline_presentation``)
과 가른 이유는 저쪽이 DB를 몰라야 하기 때문이고, ``main.py``와 가른 이유는
create_app이 이미 충분히 길기 때문이다.

기준 날짜는 **원장의 마지막 잡 슬롯**이다. 오늘 날짜로 잡으면 주말이나 앱이
꺼져 있던 다음 날 화면이 통째로 비어, "아무 일도 없었다"와 "아직 안 돌았다"가
구별되지 않는다. 원장이 마지막으로 무언가 한 날을 보여주는 편이 정직하다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from quantinue.api.pipeline_presentation import (
    AllocationView,
    ChainView,
    LlmSpendView,
    PipelineDayView,
    WatchActivityView,
    allocation_view,
    chain_view,
    equity_curve_views,
    exit_event_views,
    profile_judgement_views,
)


async def _llm_spend(
    reads: object, slot: date, limit_usd: float
) -> LlmSpendView | None:
    """Read the slot day's spend, or None when this store keeps no ledger.

    메모리 스토어에는 tb_llm_usage가 없다. 그때 0달러를 그리면 "예산이
    지켜지고 있다"는 거짓 신호가 되므로 카드 자체를 만들지 않는다.
    """
    reader = getattr(reads, "llm_spend_on", None)
    if reader is None:
        return None
    return LlmSpendView(spent_usd=await reader(slot), limit_usd=Decimal(str(limit_usd)))


async def _watch_activity(reads: object, slot: date) -> WatchActivityView | None:
    reader = getattr(reads, "watch_activity", None)
    if reader is None:
        return None
    activity = await reader(slot)
    if activity is None:
        return None
    return WatchActivityView(
        latest_at=activity.latest_at,
        signal_count=activity.signal_count,
        ticker_count=activity.ticker_count,
    )

if TYPE_CHECKING:
    from datetime import date

    from quantinue.db.control_room_reads import (
        AccountEquityPoint,
        AccountOverviewRecord,
        ExitEventRecord,
        JobRunRecord,
        JudgementRecord,
        OrderPlanRecord,
    )

# 표시용 창이지 판단 문턱이 아니다 — 곡선을 며칠치 그릴지는 어떤 매매 결정에도
# 들어가지 않으므로 config가 아니라 여기 산다.
DEFAULT_CURVE_DAYS = 30
# 슬롯 선택지의 길이. 잡이 하루 한 번이므로 2주치면 "지난주에 뭐가 깨졌나"를
# 덮는다. 더 길게 만들 이유가 생기면 늘리면 되는 표시용 값이다.
DEFAULT_SLOT_HISTORY = 14


class ControlRoomReads(Protocol):
    """The job-ledger reads the control room needs, and nothing more."""

    async def latest_job_slot(self) -> date | None:
        """Return the newest slot the job runner touched."""
        ...

    async def recent_job_slots(self, *, limit: int) -> tuple[date, ...]:
        """Return the days the job runner touched, newest first."""
        ...

    async def job_runs(self, slot_date: date) -> tuple[JobRunRecord, ...]:
        """Return one day's job chain."""
        ...

    async def order_plans(self, trade_date: date) -> tuple[OrderPlanRecord, ...]:
        """Return one day's allocation decisions."""
        ...

    async def account_equity_series(self, *, days: int) -> tuple[AccountEquityPoint, ...]:
        """Return the recent equity curve for every account."""
        ...

    async def judgements(self, trade_date: date) -> tuple[JudgementRecord, ...]:
        """Return one day's judgements paired with their verdicts."""
        ...

    async def account_overviews(self) -> tuple[AccountOverviewRecord, ...]:
        """Return every account with its standing — not scoped to a slot.

        계좌는 잡이 안 돈 날에도 존재한다. 슬롯에 묶어 읽으면 잡이 하루
        쉰 날 계좌가 화면에서 사라진다.
        """
        ...

    async def exit_events(self, trade_date: date) -> tuple[ExitEventRecord, ...]:
        """Return completed closes for one slot day."""
        ...


def empty_pipeline_day() -> PipelineDayView:
    """Return the view for an installation whose jobs have never run.

    메모리 스토어에는 잡 원장이 없다. 그때 화면을 500으로 죽이는 대신 빈
    관제실을 보여준다 — 잡을 아직 안 켠 것도 정상 상태이기 때문이다.
    """
    return PipelineDayView(
        chain=ChainView(slot_date=None),
        allocation=AllocationView(),
    )


async def build_pipeline_day(
    reads: ControlRoomReads,
    *,
    slot_date: date | None = None,
    curve_days: int = DEFAULT_CURVE_DAYS,
    slot_history: int = DEFAULT_SLOT_HISTORY,
    llm_limit_usd: float = 0.0,
) -> PipelineDayView:
    """Project one job slot — the latest one unless the caller asks for another.

    요청받은 슬롯이 원장에 없으면 최신 슬롯으로 되돌아간다. 없는 날을 빈
    화면으로 그리면 "그날 아무 일도 없었다"로 읽히는데, 실제로는 잡이 그날
    아예 안 돈 것이다 — 슬롯 목록에 없다는 사실 자체가 답이다.
    """
    slots = await reads.recent_job_slots(limit=slot_history)
    selected = slot_date if slot_date in slots else await reads.latest_job_slot()
    if selected is None:
        return empty_pipeline_day()
    return PipelineDayView(
        chain=chain_view(selected, await reads.job_runs(selected)),
        allocation=allocation_view(await reads.order_plans(selected)),
        curves=equity_curve_views(await reads.account_equity_series(days=curve_days)),
        profiles=profile_judgement_views(await reads.judgements(selected)),
        slots=slots,
        llm=await _llm_spend(reads, selected, llm_limit_usd),
        watch=await _watch_activity(reads, selected),
        exits=exit_event_views(await reads.exit_events(selected)),
    )
