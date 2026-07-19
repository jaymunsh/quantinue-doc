"""Phase 1a: closing a position must not consume the day's new-buy budget."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from quantinue.db.contracts import (
    AppOrderExposureReservationOutcome,
    DailyOrderReservation,
)
from quantinue.db.postgres import PostgresRunStore

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")
TRADE_DAY = date(2042, 6, 10)

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="disposable PostgreSQL URL not provided"
)


async def _seed(database_url: str) -> tuple[int, int]:
    """Seed one account plus a buy signal and a sell signal on the same day."""
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        account_id = await connection.scalar(
            text(
                """INSERT INTO tb_account(
                    broker_account_id,currency,cash,equity,buying_power,is_paper,
                    status,inv_type)
                VALUES ('TEST-CAPCLOSE','USD',100000,100000,100000,TRUE,
                    'active','aggressive')
                RETURNING id"""
            )
        )
        _ = await connection.execute(
            text(
                """INSERT INTO tb_universe(as_of_date,ticker,company_name,market_cap)
                VALUES (:day,'CAPA','Cap Close',1) ON CONFLICT DO NOTHING"""
            ),
            {"day": TRADE_DAY},
        )
        _ = await connection.execute(
            text(
                """INSERT INTO tb_daily_pick(
                    trade_date,ticker,universe_as_of,bucket,rank,sector,score)
                VALUES (:day,'CAPA',:day,'backfill',1,'test',1)
                ON CONFLICT DO NOTHING"""
            ),
            {"day": TRADE_DAY},
        )
        signals: list[int] = []
        for index, side in enumerate(("sell", "buy")):
            signals.append(
                await connection.scalar(
                    text(
                        """INSERT INTO tb_strategist_signals(
                            trade_date,ticker,cycle_ts,inv_type,side,conviction,
                            signal_consensus,summary,evidence,sizing_hint,
                            decision_close,current_price,day_high,day_low,
                            close_prev,volume,turnover,high_52w,low_52w)
                        VALUES (:day,'CAPA',:cycle,'aggressive',:side,0.800,
                            2,'fixture','[]','{}',100,100,100,100,100,0,0,100,100)
                        RETURNING id"""
                    ),
                    {
                        "day": TRADE_DAY,
                        "cycle": datetime(2042, 6, 10, 14 + index, tzinfo=UTC),
                        "side": side,
                    },
                )
            )
        # 전날 픽을 먼저 — tb_strategist_signals가 (trade_date,ticker)로
        # tb_daily_pick을 참조하므로 시그널보다 앞서야 한다
        _ = await connection.execute(
            text(
                """INSERT INTO tb_universe(as_of_date,ticker,company_name,market_cap)
                VALUES (:day,'CAPA','Cap Close',1) ON CONFLICT DO NOTHING"""
            ),
            {"day": date(2042, 6, 9)},
        )
        _ = await connection.execute(
            text(
                """INSERT INTO tb_daily_pick(
                    trade_date,ticker,universe_as_of,bucket,rank,sector,score)
                VALUES (:day,'CAPA',:day,'backfill',1,'test',1)
                ON CONFLICT DO NOTHING"""
            ),
            {"day": date(2042, 6, 9)},
        )
        # 닫을 대상이 되는, 이미 체결된 매수 (전날 산 것이라 오늘 캡과 무관하다)
        prior_signal = await connection.scalar(
            text(
                """INSERT INTO tb_strategist_signals(
                    trade_date,ticker,cycle_ts,inv_type,side,conviction,
                    signal_consensus,summary,evidence,sizing_hint,
                    decision_close,current_price,day_high,day_low,
                    close_prev,volume,turnover,high_52w,low_52w)
                VALUES (:day,'CAPA',:cycle,'aggressive','buy',0.800,
                    2,'fixture','[]','{}',100,100,100,100,100,0,0,100,100)
                RETURNING id"""
            ),
            {
                "day": date(2042, 6, 9),
                "cycle": datetime(2042, 6, 9, 14, tzinfo=UTC),
            },
        )
        prior_order_id = await connection.scalar(
            text(
                """INSERT INTO tb_order(
                    signal_id,account_id,ticker,quantity,entry_price,stop_price,
                    take_profit_price,status,idempotency_key,order_type)
                VALUES (:signal,:account,'CAPA',2,100,85,120,'filled',
                    'cap-close-prior','bracket')
                RETURNING id"""
            ),
            {"signal": prior_signal, "account": account_id},
        )
        # 오늘의 청산 주문 — 이게 오늘의 신규 매수 한 칸을 먹으면 안 된다
        _ = await connection.execute(
            text(
                """INSERT INTO tb_order(
                    signal_id,account_id,ticker,quantity,entry_price,status,
                    idempotency_key,order_type,closes_order_id)
                VALUES (:signal,:account,'CAPA',2,130,'filled',
                    'cap-close-today','close',:closes)"""
            ),
            {
                "signal": signals[0],
                "account": account_id,
                "closes": prior_order_id,
            },
        )
    await engine.dispose()
    return int(account_id or 0), int(signals[1])


@pytest.mark.anyio
async def test_a_close_does_not_consume_the_daily_new_buy_cap() -> None:
    """With cap=1 and one close already booked today, a buy must still fit."""
    # Given
    assert DATABASE_URL is not None
    account_id, buy_signal = await _seed(DATABASE_URL)
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()

    # When
    outcome = await store.reserve_daily_new_order(
        DailyOrderReservation(
            signal_id=buy_signal,
            account_id=account_id,
            ticker="CAPA",
            quantity=1,
            entry_price=Decimal("100.00"),
            stop_price=Decimal("85.00"),
            take_profit_price=Decimal("120.00"),
            idempotency_key="cap-close-newbuy",
            trade_date=TRADE_DAY,
            cap=1,
            max_app_order_exposure_usd=Decimal("100000.00"),
        )
    )

    # Then
    assert outcome.outcome is AppOrderExposureReservationOutcome.ACQUIRED
    await store.close()
