"""Phase 3: the ranking runs inside the database, spending zero API calls."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from quantinue.db.domain_records import DailyBarWrite, DailyPickWrite
from quantinue.db.postgres import PostgresRunStore

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="disposable PostgreSQL URL not provided"
)

_SNAPSHOT = date(2026, 3, 2)
_SESSION = date(2027, 1, 8)


def _sessions(count: int) -> list[date]:
    """Consecutive calendar days — the ranking windows count rows, not weekdays."""
    return [_SESSION - timedelta(days=index) for index in range(count - 1, -1, -1)]


def _series(
    ticker: str, closes: list[Decimal], volume: int = 5_000_000
) -> tuple[DailyBarWrite, ...]:
    days = _sessions(len(closes))
    return tuple(
        DailyBarWrite(
            trade_date=day,
            ticker=ticker,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=volume,
            source="test",
        )
        for day, close in zip(days, closes, strict=True)
    )


async def _seed_universe(*tickers: str) -> None:
    engine = create_async_engine(DATABASE_URL or "")
    async with engine.begin() as connection:
        for ticker in tickers:
            _ = await connection.execute(
                text(
                    """INSERT INTO tb_universe(as_of_date,ticker,company_name,market_cap)
                    VALUES (:day,:ticker,'Ranking',1) ON CONFLICT DO NOTHING"""
                ),
                {"day": _SNAPSHOT, "ticker": ticker},
            )
    await engine.dispose()


@pytest.mark.anyio
async def test_the_ranking_prefers_the_stronger_twenty_day_move() -> None:
    """랭킹은 원장의 봉만으로 계산된다 — 외부 호출 0."""
    # Given: 같은 길이의 이력, 다른 20일 수익률.
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    await _seed_universe("RANKUP", "RANKFLAT")
    rising = [Decimal(50 + index) for index in range(80)]
    flat = [Decimal(50) for _ in range(80)]
    await store.domain.save_daily_bars(_series("RANKUP", rising) + _series("RANKFLAT", flat))

    # When
    ranked = await store.domain.rank_universe(
        _SESSION,
        _SNAPSHOT,
        min_price_usd=0,
        min_avg_dollar_vol=0,
        min_history_sessions=60,
    )

    # Then
    found = {candidate.ticker: candidate for candidate in ranked}
    assert found["RANKUP"].ret_20d_pct > found["RANKFLAT"].ret_20d_pct
    assert found["RANKUP"].ma20 > found["RANKUP"].ma50
    await store.close()


@pytest.mark.anyio
async def test_a_ticker_without_enough_history_never_reaches_the_ranking() -> None:
    """신규 상장은 짧은 이력만으로 52주 고가에 붙어 있어 돌파로 오인된다."""
    # Given: 세션 10개짜리 신규 상장.
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    await _seed_universe("RANKNEW")
    await store.domain.save_daily_bars(_series("RANKNEW", [Decimal(10 + i) for i in range(10)]))

    # When
    ranked = await store.domain.rank_universe(
        _SESSION,
        _SNAPSHOT,
        min_price_usd=0,
        min_avg_dollar_vol=0,
        min_history_sessions=60,
    )

    # Then: 걸러진 것은 "나쁘다"가 아니라 "볼 수 없다"이므로 아예 빠진다.
    assert "RANKNEW" not in {candidate.ticker for candidate in ranked}
    await store.close()


@pytest.mark.anyio
async def test_an_illiquid_ticker_never_reaches_the_ranking() -> None:
    """유동성 미달 종목은 살 수도 팔 수도 없다 — 후보에 둘 이유가 없다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    await _seed_universe("RANKTHIN")
    await store.domain.save_daily_bars(
        _series("RANKTHIN", [Decimal(50) for _ in range(80)], volume=1)
    )

    # When
    ranked = await store.domain.rank_universe(
        _SESSION,
        _SNAPSHOT,
        min_price_usd=0,
        min_avg_dollar_vol=20_000_000,
        min_history_sessions=60,
    )

    # Then
    assert "RANKTHIN" not in {candidate.ticker for candidate in ranked}
    await store.close()


@pytest.mark.anyio
async def test_saving_the_scope_replaces_yesterdays_ranks_rather_than_merging() -> None:
    """순위는 집합 전체에 대한 상대값이라, 남은 어제 행이 "오늘의 상위 N"을 거짓으로 만든다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    await _seed_universe("SCOPEA", "SCOPEB")

    def _pick(ticker: str, rank: int) -> DailyPickWrite:
        return DailyPickWrite(
            trade_date=_SESSION,
            ticker=ticker,
            universe_as_of=_SNAPSHOT,
            bucket="trend_leader",
            rank=rank,
            sector="미분류",
            score=Decimal("0.5"),
        )

    await store.domain.save_daily_picks((_pick("SCOPEA", 1), _pick("SCOPEB", 2)))

    # When: 오늘 다시 돌았고 SCOPEB는 범위에서 빠졌다.
    await store.domain.save_daily_picks((_pick("SCOPEB", 1),))

    # Then
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT ticker, rank FROM tb_daily_pick"
                    " WHERE trade_date = :day ORDER BY rank"
                ),
                {"day": _SESSION},
            )
        ).all()
    await engine.dispose()
    assert [(row.ticker, row.rank) for row in rows] == [("SCOPEB", 1)]
    await store.close()


@pytest.mark.anyio
async def test_the_scope_may_grow_past_the_old_fifty_row_ceiling() -> None:
    """보유가 많아 51번째 행이 필요한 날, 상한이 남아 있으면 청산이 막힌다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    tickers = [f"WIDE{index:03d}" for index in range(60)]
    await _seed_universe(*tickers)

    # When
    await store.domain.save_daily_picks(
        tuple(
            DailyPickWrite(
                trade_date=_SESSION,
                ticker=ticker,
                universe_as_of=_SNAPSHOT,
                bucket="backfill",
                rank=index,
                sector="미분류",
                score=Decimal("0.1"),
            )
            for index, ticker in enumerate(tickers, start=1)
        )
    )

    # Then
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        widest = await connection.scalar(
            text("SELECT max(rank) FROM tb_daily_pick WHERE trade_date = :day"),
            {"day": _SESSION},
        )
    await engine.dispose()
    assert widest == 60
    await store.close()


@pytest.mark.anyio
async def test_a_referenced_pick_survives_the_scope_replacement() -> None:
    """구 러너와 공존하는 동안(D6) 같은 날짜에 둘 다 쓴다.

    범위를 통째로 지우면 이미 판단이 매달린 행까지 지우려다 FK에 걸려
    스크리닝 잡이 그날 통째로 실패한다. 주말에는 두 경로의 trade_date가
    갈려서 드러나지 않지만 평일에는 매일 터진다.
    """
    # Given: SCOPEKEEP에는 이미 시그널이 달렸고, SCOPEDROP은 아무도 안 본다.
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    await _seed_universe("SCOPEKEEP", "SCOPEDROP")

    def _pick(ticker: str, rank: int, score: str = "0.5") -> DailyPickWrite:
        return DailyPickWrite(
            trade_date=_SESSION,
            ticker=ticker,
            universe_as_of=_SNAPSHOT,
            bucket="trend_leader",
            rank=rank,
            sector="미분류",
            score=Decimal(score),
        )

    await store.domain.save_daily_picks((_pick("SCOPEKEEP", 1), _pick("SCOPEDROP", 2)))
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        _ = await connection.execute(
            text(
                """INSERT INTO tb_strategist_signals(
                    trade_date,ticker,cycle_ts,inv_type,side,conviction,signal_consensus,
                    summary,evidence,sizing_hint,decision_close,current_price,
                    day_high,day_low,close_prev,volume,turnover,high_52w,low_52w)
                VALUES (:day,'SCOPEKEEP',:cycle,'aggressive','buy',0.8,2,
                    'fixture','[]','{}',100,100,100,100,100,0,0,100,100)"""
            ),
            {"day": _SESSION, "cycle": datetime(2027, 1, 8, 14, tzinfo=UTC)},
        )

    # When: 오늘 범위가 완전히 바뀌었다.
    await store.domain.save_daily_picks((_pick("SCOPEKEEP", 1, "0.9"),))

    # Then: 참조된 행은 살아남아 갱신되고, 참조 없는 행만 사라진다.
    async with engine.begin() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT ticker, rank, score FROM tb_daily_pick"
                    " WHERE trade_date = :day ORDER BY rank"
                ),
                {"day": _SESSION},
            )
        ).all()
    await engine.dispose()
    assert [(row.ticker, row.rank) for row in rows] == [("SCOPEKEEP", 1)]
    assert Decimal(str(rows[0].score)) == Decimal("0.9")
    await store.close()


@pytest.mark.anyio
async def test_the_prompt_indicators_agree_with_the_ranking_indicators() -> None:
    """두 질의가 같은 창 계산을 쓴다는 것을 원장 수준에서 고정한다.

    스크리닝 점수와 프롬프트 지표가 갈리면 "왜 이 점수인가"를 모델에게
    설명할 수 없다. 계산 정의를 한 곳에 두는(``_WINDOW_INDICATORS_SQL``)
    이유이고, 복사됐는지 여부를 이 테스트가 감시한다.
    """
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    await _seed_universe("AGREEA")
    closes = [Decimal(100) + Decimal(index) for index in range(60)]
    await store.domain.save_daily_bars(_series("AGREEA", closes))
    await store.domain.save_daily_picks(
        (
            DailyPickWrite(
                trade_date=_SESSION,
                ticker="AGREEA",
                universe_as_of=_SNAPSHOT,
                bucket="trend_leader",
                rank=1,
                sector="test",
                score=Decimal("0.5"),
            ),
        )
    )

    # When
    ranked = await store.domain.rank_universe(
        _SESSION,
        _SNAPSHOT,
        min_price_usd=1,
        min_avg_dollar_vol=0,
        min_history_sessions=1,
    )
    indicators = await store.domain.pick_indicators(_SESSION, _SESSION)

    # Then
    from_ranking = next(item for item in ranked if item.ticker == "AGREEA")
    assert indicators["AGREEA"] == from_ranking
    await store.close()


@pytest.mark.anyio
async def test_a_holding_the_ranking_filtered_out_still_gets_its_indicators() -> None:
    """탈락한 보유야말로 매도 판단이 필요한 종목이다 — 유동성 문턱을 여기서
    또 걸면 팔지 말지 정해야 할 종목만 지표 없이 판단대에 오른다."""
    # Given: 거래대금이 랭킹 문턱에 한참 못 미치는 보유
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    await _seed_universe("AGREETHIN")
    closes = [Decimal(10) for _ in range(60)]
    await store.domain.save_daily_bars(_series("AGREETHIN", closes, volume=10))
    await store.domain.save_daily_picks(
        (
            DailyPickWrite(
                trade_date=_SESSION,
                ticker="AGREETHIN",
                universe_as_of=_SNAPSHOT,
                bucket="backfill",
                rank=1,
                sector="test",
                score=Decimal(0),
            ),
        )
    )

    # When
    ranked = await store.domain.rank_universe(
        _SESSION,
        _SNAPSHOT,
        min_price_usd=5,
        min_avg_dollar_vol=20_000_000,
        min_history_sessions=60,
    )
    indicators = await store.domain.pick_indicators(_SESSION, _SESSION)

    # Then
    assert all(item.ticker != "AGREETHIN" for item in ranked)
    assert "AGREETHIN" in indicators
    await store.close()
