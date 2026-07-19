"""Job-shaped control room views — what today's pipeline actually did.

구 관제실은 "런 하나가 11단계를 어디까지 갔나"를 그렸다. 잡 기반에서는 그
질문이 성립하지 않는다 — 잡은 서로 독립이고 하나가 죽어도 나머지는 돈다.
그래서 화면의 질문도 바뀐다: **오늘 어떤 잡이 돌았고, 체인이 어디서
끊겼고, 그 결과 무엇을 샀고 왜 못 샀나.**

여기 있는 것은 전부 순수 함수다. 원장 레코드를 받아 화면 모델을 만들 뿐
DB를 모른다 — 화면 규칙(끊긴 지점 판정·스킵 사유 순위·변화율)을 DB 없이
테스트로 고정할 수 있어야 하기 때문이다.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime  # noqa: TC003 - pydantic이 런타임에 해석한다
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from quantinue.core.ontology import Decision

if TYPE_CHECKING:
    from quantinue.db.control_room_reads import (
        AccountEquityPoint,
        JobRunRecord,
        JudgementRecord,
        OrderPlanRecord,
    )

_RUNNING = "running"
_SUCCEEDED = "succeeded"
_FAILED = "failed"
_PLANNED = "planned"
_SKIPPED = "skipped"
_PERCENT = Decimal(100)
_CENT = Decimal("0.01")


class JobRunView(BaseModel):
    """One job's slot as the control room shows it."""

    model_config = ConfigDict(frozen=True)

    job_name: str
    status: str
    detail: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None = Field(default=None, ge=0)


class ChainView(BaseModel):
    """One day's job chain, in the order the runner executed it."""

    model_config = ConfigDict(frozen=True)

    slot_date: date | None
    jobs: tuple[JobRunView, ...] = ()
    succeeded: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    running: int = Field(default=0, ge=0)
    broke_at: str | None = None


class SkipReasonView(BaseModel):
    """How often one allocation gate blocked a buy today."""

    model_config = ConfigDict(frozen=True)

    reason: str
    count: int = Field(gt=0)


class OrderPlanView(BaseModel):
    """One allocation decision, bought or blocked."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    account_id: int | None
    decision: str
    skipped_reason: str | None
    quantity: int = Field(ge=0)
    entry_price: Decimal | None


class AllocationView(BaseModel):
    """The day's allocation outcome with the reasons it stopped buying."""

    model_config = ConfigDict(frozen=True)

    bought: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    reasons: tuple[SkipReasonView, ...] = ()
    plans: tuple[OrderPlanView, ...] = ()


class EquityPointView(BaseModel):
    """One account's equity on one day."""

    model_config = ConfigDict(frozen=True)

    trade_date: date
    equity: Decimal


class AccountCurveView(BaseModel):
    """One account's recent equity curve and its move over the window."""

    model_config = ConfigDict(frozen=True)

    account_id: int
    points: tuple[EquityPointView, ...]
    opening_equity: Decimal
    latest_equity: Decimal
    change_pct: Decimal


class JudgementView(BaseModel):
    """One strategist judgement together with the critic's answer."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    side: str
    conviction: Decimal
    summary: str
    bull_case: str | None
    key_risk: str | None
    verdict_decision: str | None
    verdict_confidence: Decimal | None
    objection: str | None
    approved: bool


class ProfileJudgementsView(BaseModel):
    """One investment profile's judgements and how many survived the critic."""

    model_config = ConfigDict(frozen=True)

    inv_type: str
    total: int = Field(default=0, ge=0)
    approved: int = Field(default=0, ge=0)
    unjudged: int = Field(default=0, ge=0)
    judgements: tuple[JudgementView, ...] = ()


class PipelineDayView(BaseModel):
    """Everything the control room reports about one pipeline day."""

    model_config = ConfigDict(frozen=True)

    chain: ChainView
    allocation: AllocationView
    curves: tuple[AccountCurveView, ...] = ()
    profiles: tuple[ProfileJudgementsView, ...] = ()
    slots: tuple[date, ...] = ()


def _duration_ms(record: JobRunRecord) -> int | None:
    # 끝나지 않은 잡에 소요시간을 지어내지 않는다. 0이나 "지금까지 경과"를
    # 넣으면 화면에서 끝난 잡과 구별되지 않는다.
    if record.finished_at is None:
        return None
    elapsed = record.finished_at - record.started_at
    return max(int(elapsed.total_seconds() * 1000), 0)


def chain_view(slot_date: date | None, records: tuple[JobRunRecord, ...]) -> ChainView:
    """Project one day's job ledger rows into the chain panel.

    ``broke_at``은 **처음** 실패한 잡이다. 마지막이 아니라 처음인 이유는
    의존성이다 — 수집이 깨지면 그 뒤 판단 잡들도 줄줄이 실패하는데, 그때
    범인은 마지막에 실패한 잡이 아니라 체인을 끊은 첫 잡이다.
    """
    jobs = tuple(
        JobRunView(
            job_name=record.job_name,
            status=record.status,
            detail=record.detail,
            started_at=record.started_at,
            finished_at=record.finished_at,
            duration_ms=_duration_ms(record),
        )
        for record in records
    )
    statuses = Counter(job.status for job in jobs)
    broke_at = next((job.job_name for job in jobs if job.status == _FAILED), None)
    return ChainView(
        slot_date=slot_date,
        jobs=jobs,
        succeeded=statuses[_SUCCEEDED],
        failed=statuses[_FAILED],
        running=statuses[_RUNNING],
        broke_at=broke_at,
    )


def allocation_view(records: tuple[OrderPlanRecord, ...]) -> AllocationView:
    """Project the day's allocation decisions, ranking the blocks by frequency.

    스킵 사유를 빈도순으로 세우는 것은 문턱 조정의 입력이기 때문이다 —
    "무엇이 실제로 막고 있나"에 답하려면 목록이 아니라 순위가 필요하다.
    """
    plans = tuple(
        OrderPlanView(
            ticker=record.ticker,
            account_id=record.account_id,
            decision=record.decision,
            skipped_reason=record.skipped_reason,
            quantity=record.quantity,
            entry_price=record.entry_price,
        )
        for record in records
    )
    counted = Counter(
        plan.skipped_reason
        for plan in plans
        if plan.decision == _SKIPPED and plan.skipped_reason is not None
    )
    return AllocationView(
        bought=sum(1 for plan in plans if plan.decision == _PLANNED),
        skipped=sum(1 for plan in plans if plan.decision == _SKIPPED),
        reasons=tuple(
            SkipReasonView(reason=reason, count=count) for reason, count in counted.most_common()
        ),
        plans=plans,
    )


def equity_curve_views(points: tuple[AccountEquityPoint, ...]) -> tuple[AccountCurveView, ...]:
    """Group equity points into one curve per account, oldest point first."""
    grouped: dict[int, list[AccountEquityPoint]] = defaultdict(list)
    for point in points:
        grouped[point.account_id].append(point)
    curves: list[AccountCurveView] = []
    for account_id in sorted(grouped):
        series = sorted(grouped[account_id], key=lambda item: item.trade_date)
        opening = series[0].equity
        latest = series[-1].equity
        # 시작점이 0이면 변화율이 정의되지 않는다. 계좌 자본이 0인 상태는
        # 실제로 있을 수 있으므로(전액 손실) 나눗셈을 막고 0으로 보고한다.
        change = (
            ((latest - opening) / opening * _PERCENT).quantize(_CENT, rounding=ROUND_HALF_UP)
            if opening > 0
            else Decimal("0.00")
        )
        curves.append(
            AccountCurveView(
                account_id=account_id,
                points=tuple(
                    EquityPointView(trade_date=item.trade_date, equity=item.equity)
                    for item in series
                ),
                opening_equity=opening,
                latest_equity=latest,
                change_pct=change,
            )
        )
    return tuple(curves)


def sparkline_points(curve: AccountCurveView, *, width: int = 160, height: int = 32) -> str:
    """Return SVG polyline coordinates for one equity curve, or "" if unplottable.

    기하를 템플릿이 아니라 여기서 계산하는 이유는 테스트다 — 평평한 곡선의
    0으로 나누기나 점 하나짜리 계좌를 Jinja 안에서 고정할 방법이 없다.
    """
    values = [point.equity for point in curve.points]
    if len(values) < 2:  # noqa: PLR2004 - 점 하나로는 선분이 성립하지 않는다
        return ""
    low = min(values)
    span = max(values) - low
    step = Decimal(width) / Decimal(len(values) - 1)
    coordinates: list[str] = []
    for index, value in enumerate(values):
        # 완전히 평평한 구간은 중앙선으로 그린다. span이 0이면 비율이 정의되지
        # 않는데, 그때 바닥(0)에 붙이면 "전액 손실"처럼 보인다.
        ratio = Decimal("0.5") if span == 0 else (value - low) / span
        y_position = Decimal(height) - ratio * Decimal(height)
        coordinates.append(f"{index * step:.1f},{y_position:.1f}")
    return " ".join(coordinates)


def profile_judgement_views(
    records: tuple[JudgementRecord, ...],
) -> tuple[ProfileJudgementsView, ...]:
    """Split the day's judgements by investment profile, with approval counts.

    성향별로 가르는 이유는 그것이 이 시스템의 주장이기 때문이다 — 같은 증거로
    공격형과 안전형이 실제로 다르게 판단한다는 것. 합쳐서 보여주면 그 격차가
    화면에서 사라진다.

    승인 판정은 프로덕션 경로와 같은 어휘를 쓴다(``Decision.PASS``). 평결이
    없는 판단은 승인도 기각도 아니다 — 크리틱이 하지 않은 일을 뒤집어씌우면
    승인율 통계가 조용히 틀어진다.
    """
    grouped: dict[str, list[JudgementRecord]] = defaultdict(list)
    for record in records:
        grouped[record.inv_type].append(record)
    views: list[ProfileJudgementsView] = []
    for inv_type in sorted(grouped):
        items = grouped[inv_type]
        judgements = tuple(
            JudgementView(
                ticker=item.ticker,
                side=item.side,
                conviction=item.conviction,
                summary=item.summary,
                bull_case=item.bull_case,
                key_risk=item.key_risk,
                verdict_decision=item.verdict_decision,
                verdict_confidence=item.verdict_confidence,
                objection=item.objection,
                approved=item.verdict_decision == Decision.PASS,
            )
            for item in items
        )
        views.append(
            ProfileJudgementsView(
                inv_type=inv_type,
                total=len(judgements),
                approved=sum(1 for item in judgements if item.approved),
                unjudged=sum(1 for item in judgements if item.verdict_decision is None),
                judgements=judgements,
            )
        )
    return tuple(views)
