"""Read projections that let the control room report what the jobs did.

이 모듈이 따로 있는 이유는 방향이다. ``domain.py``의 나머지는 잡이 **판단하기
위해** 읽는 것들이고(후보·관측·계좌 상태), 여기 있는 것들은 사람이 **무슨 일이
있었는지 보기 위해** 읽는다. 둘을 섞으면 화면을 고치려다 판단 경로의 쿼리를
건드리게 된다.

관측용 읽기의 규칙 하나: **불완전한 것을 숨기지 않는다.** 프로덕션 경로는
크리틱을 통과한 것만 보면 되지만(``approved_buy_candidates``의 내부 조인),
관제실은 판단만 남기고 죽은 슬롯도 보여야 한다 — 그 사고가 안 보이면 화면이
"오늘은 조용했다"고 거짓말한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from textwrap import dedent
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from datetime import date
    from decimal import Decimal

    from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True, slots=True)
class JobRunRecord:
    """One job's slot in the ledger, as the control room reads it."""

    job_name: str
    slot_date: date
    status: str
    detail: str | None
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class OrderPlanRecord:
    """One allocation decision — a buy that happened, or one that did not."""

    ticker: str
    account_id: int | None
    trade_date: date
    decision: str
    skipped_reason: str | None
    quantity: int
    entry_price: Decimal | None


@dataclass(frozen=True, slots=True)
class AccountEquityPoint:
    """One account's mark-to-market equity on one trading day."""

    account_id: int
    trade_date: date
    equity: Decimal


@dataclass(frozen=True, slots=True)
class JudgementRecord:
    """One strategist judgement together with the critic's answer to it."""

    ticker: str
    inv_type: str
    side: str
    conviction: Decimal
    summary: str
    bull_case: str | None
    key_risk: str | None
    verdict_decision: str | None
    verdict_confidence: Decimal | None
    objection: str | None


async def latest_job_slot(engine: AsyncEngine) -> date | None:
    """Return the most recent slot the job runner touched, if it ever ran."""
    async with engine.begin() as connection:
        return await connection.scalar(text("SELECT max(slot_date) FROM tb_job_run"))


async def recent_job_slots(engine: AsyncEngine, *, limit: int) -> tuple[date, ...]:
    """Return the days the job runner touched, newest first.

    관제실이 하루만 볼 수 있으면 어제 무엇이 깨졌는지 물을 수 없다. 슬롯
    목록을 따로 읽는 이유는 잡이 하루도 안 돈 날은 아예 행이 없기 때문이다 —
    달력에서 뽑으면 빈 날이 선택지로 뜬다.
    """
    async with engine.begin() as connection:
        rows = (
            await connection.execute(
                text(
                    dedent(
                        """
                        SELECT DISTINCT slot_date FROM tb_job_run
                        ORDER BY slot_date DESC
                        LIMIT :limit
                        """
                    )
                ),
                {"limit": limit},
            )
        ).all()
    return tuple(row.slot_date for row in rows)


async def job_runs(engine: AsyncEngine, slot_date: date) -> tuple[JobRunRecord, ...]:
    """List one day's job chain in the order the runner actually executed it.

    ``started_at`` 정렬이 곧 등록 순서다 — 한 틱 안에서 순서대로 돌기 때문에.
    이름순으로 정렬하면 "유니버스 → 일봉 → … → 배분"이라는 계약이 화면에서
    사라지고, 어느 단계에서 체인이 끊겼는지 읽을 수 없게 된다.
    """
    async with engine.begin() as connection:
        rows = (
            await connection.execute(
                text(
                    dedent(
                        """
                        SELECT job_name, slot_date, status, detail, started_at, finished_at
                        FROM tb_job_run
                        WHERE slot_date = :slot_date
                        ORDER BY started_at, job_name
                        """
                    )
                ),
                {"slot_date": slot_date},
            )
        ).all()
    return tuple(
        JobRunRecord(
            job_name=row.job_name,
            slot_date=row.slot_date,
            status=row.status,
            detail=row.detail,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )
        for row in rows
    )


async def order_plans(engine: AsyncEngine, trade_date: date) -> tuple[OrderPlanRecord, ...]:
    """List one day's allocation decisions, the skipped ones included.

    건너뛴 행이 이 조회의 핵심이다. 산 것만 보여주면 "후보가 없었다"와
    "지갑이 막았다"가 화면에서 같아 보인다 — 완전히 다른 상황인데도.
    """
    async with engine.begin() as connection:
        rows = (
            await connection.execute(
                text(
                    dedent(
                        """
                        SELECT ticker, account_id, trade_date, decision,
                               skipped_reason, quantity, entry_price
                        FROM tb_order_plan
                        WHERE trade_date = :trade_date
                        ORDER BY account_id NULLS FIRST, decision, ticker
                        """
                    )
                ),
                {"trade_date": trade_date},
            )
        ).all()
    return tuple(
        OrderPlanRecord(
            ticker=row.ticker,
            account_id=row.account_id,
            trade_date=row.trade_date,
            decision=row.decision,
            skipped_reason=row.skipped_reason,
            quantity=row.quantity,
            entry_price=row.entry_price,
        )
        for row in rows
    )


async def account_equity_series(
    engine: AsyncEngine, *, days: int
) -> tuple[AccountEquityPoint, ...]:
    """Return the recent equity curve for every account, oldest point first.

    창을 오늘이 아니라 **원장의 마지막 날**에서 뒤로 잰다. 오늘 기준으로 자르면
    주말이나 앱이 꺼져 있던 구간이 그대로 빈 화면이 된다 — 곡선이 사라진 것과
    데이터가 없는 것을 구별할 수 없다.
    """
    async with engine.begin() as connection:
        rows = (
            await connection.execute(
                text(
                    dedent(
                        """
                        SELECT account_id, trade_date, equity
                        FROM tb_account_equity_daily
                        WHERE trade_date > (SELECT max(trade_date) FROM tb_account_equity_daily)
                                            - make_interval(days => :days)
                        ORDER BY account_id, trade_date
                        """
                    )
                ),
                {"days": days},
            )
        ).all()
    return tuple(
        AccountEquityPoint(
            account_id=row.account_id,
            trade_date=row.trade_date,
            equity=row.equity,
        )
        for row in rows
    )


async def judgements(engine: AsyncEngine, trade_date: date) -> tuple[JudgementRecord, ...]:
    """Pair one day's strategist judgements with the critic verdicts on them.

    LEFT JOIN인 것이 의도다(모듈 docstring 참조): 평결이 없는 판단은 크리틱
    전에 무언가 끊겼다는 사실이고, 그것이야말로 관제실이 보여야 할 사건이다.

    ``cycle_ts = 자정``은 **프로덕션 경로와 같은 필터다**(``approved_buy_candidates``
    참조 — 새 분석 잡의 계약). 이걸 빼면 화면이 배분이 소비하지 않은 판단까지
    세어, 잡 원장이 "22건 분석 · 8 승인"이라고 적은 날에 관제실은 "28건 중 8"을
    보여준다. 실제로 dev DB에서 그렇게 어긋났다 — 구 러너의 장중 행 하나와
    마이크로초가 밀린 과거 실험 행 다섯이 섞여 들어왔다. 관제실이 원장과 다른
    수를 말하면 둘 중 무엇을 믿을지 알 수 없게 된다.
    """
    async with engine.begin() as connection:
        rows = (
            await connection.execute(
                text(
                    dedent(
                        """
                        SELECT s.ticker, s.inv_type, s.side, s.conviction, s.summary,
                               s.bull_case, s.key_risk,
                               v.decision AS verdict_decision,
                               v.confidence AS verdict_confidence,
                               v.objection
                        FROM tb_strategist_signals AS s
                        LEFT JOIN tb_critic_verdict AS v ON v.signal_id = s.id
                        WHERE s.trade_date = :trade_date
                          AND s.cycle_ts = :cycle_ts
                        ORDER BY s.inv_type, s.conviction DESC, s.ticker
                        """
                    )
                ),
                {
                    "trade_date": trade_date,
                    "cycle_ts": datetime.combine(trade_date, time(), tzinfo=UTC),
                },
            )
        ).all()
    return tuple(
        JudgementRecord(
            ticker=row.ticker,
            inv_type=row.inv_type,
            side=row.side,
            conviction=row.conviction,
            summary=row.summary,
            bull_case=row.bull_case,
            key_risk=row.key_risk,
            verdict_decision=row.verdict_decision,
            verdict_confidence=row.verdict_confidence,
            objection=row.objection,
        )
        for row in rows
    )
