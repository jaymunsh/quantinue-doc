"""공시 채점 원장 — 07이 투표할 표가 앉고, 판단이 그 표를 계보로 가리킨다."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from quantinue.db.domain_records import (
    DailyPickWrite,
    DisclosureSignalWrite,
    StrategistSignalWrite,
)
from quantinue.db.postgres import PostgresRunStore
from quantinue.roles.role_01_universe_screener.contracts import (
    UniverseMember,
    UniverseScreenerOutput,
)

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="disposable PostgreSQL URL not provided"
)

_DAY = date(2026, 7, 15)
_CYCLE = datetime.combine(_DAY, time(), tzinfo=UTC)


def _pick(ticker: str) -> DailyPickWrite:
    return DailyPickWrite(
        trade_date=_DAY,
        ticker=ticker,
        universe_as_of=_DAY,
        bucket="trend_leader",
        rank=1,
        sector="Technology",
        score=Decimal("0.900"),
    )


async def _seed_pick(store: PostgresRunStore, ticker: str) -> None:
    """픽은 유니버스 행을 요구한다 — 채점의 FK 사슬이 거기서 시작한다."""
    await store.domain.save_universe(
        UniverseScreenerOutput(
            run_id=f"universe:{_DAY.isoformat()}",
            generated_at=_CYCLE,
            members=(
                UniverseMember(
                    as_of_date=_DAY,
                    ticker=ticker,
                    company_name=f"{ticker} Inc",
                    market_cap=1_000_000,
                    evidence_ids=(f"universe:{_DAY.isoformat()}:{ticker}",),
                ),
            ),
        )
    )
    await store.domain.save_daily_picks((_pick(ticker),))


def _signal(ticker: str, score: float) -> DisclosureSignalWrite:
    return DisclosureSignalWrite(
        ticker=ticker,
        cycle_ts=_CYCLE,
        trade_date=_DAY,
        has_signal=True,
        sentiment_score=score,
        disclosure_count=2,
    )


@pytest.mark.anyio
async def test_a_scored_filing_is_readable_as_the_vote_for_that_slot() -> None:
    """채점 잡이 쓰고 분석 잡이 읽는 한 바퀴 — 이게 끊기면 표가 사라진다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    await _seed_pick(store, "DSIGONE")

    # When
    await store.domain.save_disclosure_signal(_signal("DSIGONE", 0.82))
    scores = await store.domain.disclosure_scores(_DAY)

    # Then
    assert scores["DSIGONE"] == pytest.approx(0.82)
    await store.close()


@pytest.mark.anyio
async def test_rescoring_the_same_slot_does_not_duplicate_the_vote() -> None:
    """한 슬롯 = 한 표. 재실행이 같은 종목에 두 표를 만들면 투표가 조작된다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    await _seed_pick(store, "DSIGDUP")

    # When
    await store.domain.save_disclosure_signal(_signal("DSIGDUP", 0.40))
    await store.domain.save_disclosure_signal(_signal("DSIGDUP", 0.90))
    scores = await store.domain.disclosure_scores(_DAY)

    # Then: 먼저 쓴 표가 이긴다 — 멱등 가드가 역사를 덮어쓰지 않는다.
    assert scores["DSIGDUP"] == pytest.approx(0.40)
    await store.close()


@pytest.mark.anyio
async def test_a_judgement_can_point_at_the_disclosure_row_it_voted_on() -> None:
    """계보 FK가 실제로 성립하는지 — 스키마가 이 참조를 강제한다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    await _seed_pick(store, "DSIGLIN")
    await store.domain.save_disclosure_signal(_signal("DSIGLIN", 0.61))

    # When
    signal_id = await store.domain.save_signal(
        StrategistSignalWrite(
            run_id="lineage-run",
            trade_date=_DAY,
            ticker="DSIGLIN",
            cycle_ts=_CYCLE,
            side="buy",
            conviction=Decimal("0.700"),
            summary="lineage smoke",
            decision_close=Decimal("100.00"),
            evidence=("lineage-run:DSIGLIN",),
            inv_type="aggressive",
            disclosure_score=Decimal("0.610"),
            src_disclosure_at=_CYCLE,
        )
    )

    # Then: 계보가 **원장에 앉아야** 한다. 인자로 받고 조용히 버려도 저장은
    # 성공하므로, 돌아온 id만 보면 유령을 통과시킨다.
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        stored = await connection.scalar(
            text("SELECT src_disclosure_at FROM tb_strategist_signals WHERE id = :id"),
            {"id": signal_id},
        )
    await engine.dispose()
    assert stored == _CYCLE
    await store.close()
