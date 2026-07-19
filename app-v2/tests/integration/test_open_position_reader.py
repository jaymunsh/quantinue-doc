"""Phase 1c: the exit job needs each open position's entry terms and fill date."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from quantinue.db.postgres import PostgresRunStore

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="disposable PostgreSQL URL not provided"
)

# 시그널 한 행을 넣는 완결된 문장. 조각을 f-string으로 합치면 정적 분석이
# SQL 조립으로 보고(S608) 실제로도 나중에 값이 섞여 들어갈 여지가 생긴다.
_INSERT_SIGNAL = """INSERT INTO tb_strategist_signals(
    trade_date,ticker,cycle_ts,inv_type,side,conviction,
    signal_consensus,summary,evidence,sizing_hint,decision_close,current_price,
    day_high,day_low,close_prev,volume,turnover,high_52w,low_52w)
VALUES (:day,:ticker,:cycle,'aggressive',:side,0.800,
    2,'fixture','[]','{}',100,100,100,100,100,0,0,100,100)
RETURNING id"""


async def _seed(database_url: str, suffix: str) -> int:
    """Seed one account holding OPENX, plus a fully closed OPENY that must not appear."""
    engine = create_async_engine(database_url)
    day = date(2043, 4, 6)
    async with engine.begin() as connection:
        account_id = await connection.scalar(
            text(
                """INSERT INTO tb_account(
                    broker_account_id,currency,cash,equity,buying_power,is_paper,
                    status,inv_type)
                VALUES (:bid,'USD',100000,100000,100000,TRUE,
                    'active','aggressive')
                RETURNING id"""
            ),
            {"bid": f"TEST-OPENREAD-{suffix}"},
        )
        held_ticker, closed_ticker = f"OX{suffix}", f"OY{suffix}"
        for ticker in (held_ticker, closed_ticker):
            _ = await connection.execute(
                text(
                    """INSERT INTO tb_universe(as_of_date,ticker,company_name,market_cap)
                    VALUES (:day,:ticker,'Reader',1) ON CONFLICT DO NOTHING"""
                ),
                {"day": day, "ticker": ticker},
            )
            _ = await connection.execute(
                text(
                    """INSERT INTO tb_daily_pick(
                        trade_date,ticker,universe_as_of,bucket,rank,sector,score)
                    VALUES (:day,:ticker,:day,'backfill',1,'test',1)
                    ON CONFLICT DO NOTHING"""
                ),
                {"day": day, "ticker": ticker},
            )
        held_signal = await connection.scalar(
            text(_INSERT_SIGNAL),
            {
                "day": day,
                "ticker": held_ticker,
                "cycle": datetime(2043, 4, 6, 14, tzinfo=UTC),
                "side": "buy",
            },
        )
        held_order = await connection.scalar(
            text(
                """INSERT INTO tb_order(
                    signal_id,account_id,ticker,quantity,entry_price,stop_price,
                    take_profit_price,status,idempotency_key,order_type)
                VALUES (:signal,:account,:ticker,3,100,85,120,'filled',
                    :key,'bracket')
                RETURNING id"""
            ),
            {
                "signal": held_signal,
                "account": account_id,
                "ticker": held_ticker,
                "key": f"reader-{suffix}-held",
            },
        )
        _ = await connection.execute(
            text(
                """INSERT INTO tb_fill(
                    order_id,side,quantity,price,filled_at,broker_fill_id)
                VALUES (:order,'buy',3,100,:at,:key)"""
            ),
            {
                "order": held_order,
                "at": datetime(2043, 4, 6, 14, tzinfo=UTC),
                "key": f"reader-{suffix}-held-fill",
            },
        )
        closed_signal = await connection.scalar(
            text(_INSERT_SIGNAL),
            {
                "day": day,
                "ticker": closed_ticker,
                "cycle": datetime(2043, 4, 6, 15, tzinfo=UTC),
                "side": "buy",
            },
        )
        closed_order = await connection.scalar(
            text(
                """INSERT INTO tb_order(
                    signal_id,account_id,ticker,quantity,entry_price,stop_price,
                    take_profit_price,status,idempotency_key,order_type)
                VALUES (:signal,:account,:ticker,1,100,85,120,'filled',
                    :key,'bracket')
                RETURNING id"""
            ),
            {
                "signal": closed_signal,
                "account": account_id,
                "ticker": closed_ticker,
                "key": f"reader-{suffix}-closed",
            },
        )
        exit_signal = await connection.scalar(
            text(_INSERT_SIGNAL),
            {
                "day": day,
                "ticker": closed_ticker,
                "cycle": datetime(2043, 4, 6, 16, tzinfo=UTC),
                "side": "sell",
            },
        )
        _ = await connection.execute(
            text(
                """INSERT INTO tb_order(
                    signal_id,account_id,ticker,quantity,entry_price,status,
                    idempotency_key,order_type,closes_order_id)
                VALUES (:signal,:account,:ticker,1,130,'filled',
                    :key,'close',:closes)"""
            ),
            {
                "signal": exit_signal,
                "account": account_id,
                "closes": closed_order,
                "ticker": closed_ticker,
                "key": f"reader-{suffix}-closed-close",
            },
        )
    await engine.dispose()
    return int(account_id or 0)


@pytest.mark.anyio
async def test_reader_returns_entry_terms_and_the_fill_date() -> None:
    """The exit job needs stop/take to evaluate the bracket and filled_on for time."""
    # Given
    assert DATABASE_URL is not None
    account_id = await _seed(DATABASE_URL, "a")
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()

    # When
    positions = await store.domain.open_positions()

    # Then
    mine = [item for item in positions if item.account_id == account_id]
    assert len(mine) == 1
    held = mine[0]
    assert held.ticker == "OXa"
    assert held.quantity == 3
    assert held.entry_price == Decimal("100.00")
    assert held.stop_price == Decimal("85.00")
    assert held.take_profit_price == Decimal("120.00")
    # 시간 청산의 기준은 주문 생성일이 아니라 체결일이다
    assert held.filled_on == date(2043, 4, 6)
    await store.close()


@pytest.mark.anyio
async def test_reader_agrees_with_the_open_position_count() -> None:
    """두 판정이 갈리면 한도와 청산이 서로 다른 세계를 본다."""
    # Given
    assert DATABASE_URL is not None
    account_id = await _seed(DATABASE_URL, "b")
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()

    # When
    positions = await store.domain.open_positions()
    state = await store.domain.account_risk_state(account_id)

    # Then
    assert state is not None
    mine = [item for item in positions if item.account_id == account_id]
    assert len(mine) == state.open_position_count
    await store.close()
