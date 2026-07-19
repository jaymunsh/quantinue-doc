"""Phase 4: the allocation job's input — critic-approved buys, best first."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from quantinue.db.postgres import PostgresRunStore

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="disposable PostgreSQL URL not provided"
)

_DAY = date(2026, 7, 20)
_MIDNIGHT = datetime.combine(_DAY, time(), tzinfo=UTC)


async def _seed_judged_signal(  # noqa: PLR0913 - 시나리오 축이 곧 인자다
    ticker: str,
    *,
    inv_type: str = "aggressive",
    side: str = "buy",
    conviction: str = "0.800",
    rank: int = 1,
    verdict: str | None = "pass",
    cycle_ts: datetime = _MIDNIGHT,
) -> None:
    """Record what the analysis job would have written: a signal, then a verdict."""
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        _ = await connection.execute(
            text(
                """INSERT INTO tb_universe(as_of_date,ticker,company_name,market_cap)
                VALUES (:day,:ticker,'Buy Candidates',1) ON CONFLICT DO NOTHING"""
            ),
            {"day": _DAY, "ticker": ticker},
        )
        _ = await connection.execute(
            text(
                """INSERT INTO tb_daily_pick(
                    trade_date,ticker,universe_as_of,bucket,rank,sector,score)
                VALUES (:day,:ticker,:day,'backfill',:rank,'test',1)
                ON CONFLICT DO NOTHING"""
            ),
            {"day": _DAY, "ticker": ticker, "rank": rank},
        )
        signal_id = await connection.scalar(
            text(
                """INSERT INTO tb_strategist_signals(
                    trade_date,ticker,cycle_ts,inv_type,side,conviction,
                    signal_consensus,summary,evidence,sizing_hint,decision_close,
                    current_price,day_high,day_low,close_prev,volume,turnover,
                    high_52w,low_52w)
                VALUES (:day,:ticker,:cycle,:inv_type,:side,:conviction,
                    2,'fixture','[]','{}',50,50,50,50,50,0,0,50,50)
                RETURNING id"""
            ),
            {
                "day": _DAY,
                "ticker": ticker,
                "cycle": cycle_ts,
                "inv_type": inv_type,
                "side": side,
                "conviction": conviction,
            },
        )
        if verdict is not None:
            _ = await connection.execute(
                text(
                    """INSERT INTO tb_critic_verdict(
                        signal_id,ticker,decision,category,objection,confidence,
                        decided_layer,verdict_source)
                    VALUES (:signal,:ticker,:decision,'model_review','fixture',
                        0.0,'gate','fresh')"""
                ),
                {"signal": signal_id, "ticker": ticker, "decision": verdict},
            )
    await engine.dispose()


@pytest.mark.anyio
async def test_only_todays_approved_buys_come_back_per_persona() -> None:
    """배분의 입력은 '크리틱을 통과한 오늘의 매수'뿐이다 — 기각·hold·sell·
    구 러너 행(장중 cycle_ts)이 섞이면 반박당한 판단으로 사게 된다."""
    assert DATABASE_URL is not None
    # Given — 승인 buy 둘(성향 각각), 기각 buy, 승인 sell, 구 러너꼴 buy
    await _seed_judged_signal("BCA", inv_type="aggressive", conviction="0.810")
    await _seed_judged_signal("BCC", inv_type="conservative", conviction="0.780")
    await _seed_judged_signal("BCR", verdict="reject")
    await _seed_judged_signal("BCS", side="sell")
    await _seed_judged_signal(
        "BCO", cycle_ts=datetime(2026, 7, 20, 14, 30, tzinfo=UTC)
    )

    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    try:
        # When
        candidates = await store.domain.approved_buy_candidates(_DAY)
    finally:
        await store.close()

    # Then
    aggressive = [c.ticker for c in candidates.get("aggressive", ())]
    assert "BCA" in aggressive
    assert {"BCR", "BCS", "BCO"}.isdisjoint(aggressive)
    conservative = [c.ticker for c in candidates.get("conservative", ())]
    assert conservative == ["BCC"]
    first = candidates["conservative"][0]
    assert first.conviction == Decimal("0.780")
    assert first.reference_price == Decimal(50)
    assert first.rank == 1


@pytest.mark.anyio
async def test_candidates_rank_by_conviction_then_screening_rank() -> None:
    """확신도 단독 정렬 — 스크리닝 점수를 다시 섞는 것은 결함 12의 반복이다.
    동률에서만 랭킹이 앞선 쪽이 먼저 온다."""
    assert DATABASE_URL is not None
    # Given — 확신도 동률 둘(랭크 7 vs 3), 그보다 높은 확신도 하나(랭크 9)
    await _seed_judged_signal("BTA", conviction="0.700", rank=7)
    await _seed_judged_signal("BTB", conviction="0.700", rank=3)
    await _seed_judged_signal("BTC", conviction="0.900", rank=9)

    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    try:
        # When
        ordered = [
            candidate.ticker
            for candidate in (await store.domain.approved_buy_candidates(_DAY))[
                "aggressive"
            ]
            if candidate.ticker.startswith("BT")
        ]
    finally:
        await store.close()

    # Then
    assert ordered == ["BTC", "BTB", "BTA"]
