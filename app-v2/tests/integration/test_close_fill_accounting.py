"""Phase 1a: a close order's fill must credit the local account, not debit it."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from quantinue.core.contracts import PipelineContext, PipelineRequest
from quantinue.core.ontology import FillSide
from quantinue.db.domain_records import CompletedFillWrite
from quantinue.db.postgres import PostgresRunStore

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")
OPENING_CASH = Decimal("1000000.00")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="disposable PostgreSQL URL not provided"
)


async def _seed_stage_parent(database_url: str, ticker: str, cycle: datetime) -> None:
    """Create the FK parents that a stage-08 signal insert requires."""
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        _ = await connection.execute(
            text(
                """INSERT INTO tb_universe(as_of_date,ticker,company_name,market_cap)
                VALUES (:day,:ticker,'Close Fill Accounting',1) ON CONFLICT DO NOTHING"""
            ),
            {"day": cycle.date(), "ticker": ticker},
        )
        _ = await connection.execute(
            text(
                """INSERT INTO tb_daily_pick(
                trade_date,ticker,universe_as_of,bucket,rank,sector,score)
                VALUES (:day,:ticker,:day,'backfill',1,'test',1)
                ON CONFLICT DO NOTHING"""
            ),
            {"day": cycle.date(), "ticker": ticker},
        )
    await engine.dispose()


async def _signal_for(
    store: PostgresRunStore, ticker: str, cycle: datetime, side: str
) -> tuple[int, int]:
    """Persist one strategist signal through stage 08, returning (signal, account)."""
    await _seed_stage_parent(DATABASE_URL or "", ticker, cycle)
    context = replace(
        PipelineContext(request=PipelineRequest(ticker=ticker, cycle_ts=cycle)),
        last_price=100.0,
        side=side,
        conviction=0.8,
        inv_type="aggressive",
        critic_approved=True,
    )
    result = await store.stage_completed("08", context, context)
    assert result.signal_id is not None
    assert result.account_id is not None
    return result.signal_id, result.account_id


@pytest.mark.anyio
async def test_close_fill_credits_cash_instead_of_debiting_it() -> None:
    """Selling 2 shares at 130 must return 260 to cash, not take another 260."""
    # Given: one filled buy of 2 @ 100 has already debited the account
    assert DATABASE_URL is not None
    # 전용 계좌를 쓴다 — 기본 계좌(quantinue-local-simulated)는 다른 통합
    # 테스트들이 현금 잔고를 정확한 값으로 단언하는 공용 자원이라, 여기서
    # 매수·매도를 태우면 그 테스트들이 실행 순서에 따라 깨진다.
    store = PostgresRunStore(
        DATABASE_URL,
        OPENING_CASH,
        account_identity="quantinue-close-fill-simulated",
    )
    await store.initialize()
    cycle = datetime(2040, 3, 5, 14, tzinfo=UTC)
    buy_signal, account_id = await _signal_for(store, "CLOSEA", cycle, "buy")
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        buy_order_id = await connection.scalar(
            text(
                """INSERT INTO tb_order(
                    signal_id,account_id,ticker,quantity,entry_price,stop_price,
                    take_profit_price,status,idempotency_key,order_type)
                VALUES (:signal,:account,'CLOSEA',2,100,85,120,'planned',
                    'close-acct-buy','bracket')
                RETURNING id"""
            ),
            {"signal": buy_signal, "account": account_id},
        )
    _ = await store.domain.record_completed_fill(
        CompletedFillWrite(
            idempotency_key="close-acct-buy",
            broker_order_id="close-acct-buy-order",
            broker_fill_id="close-acct-buy-fill",
            quantity=2,
            price=Decimal("100.00"),
            filled_at=cycle,
        )
    )
    async with engine.begin() as connection:
        cash_after_buy = await connection.scalar(
            text("SELECT cash FROM tb_account WHERE id = :aid"), {"aid": account_id}
        )

    # When: a close order for the same position fills at 130
    close_cycle = datetime(2040, 3, 6, 14, tzinfo=UTC)
    sell_signal, _ = await _signal_for(store, "CLOSEA", close_cycle, "sell")
    async with engine.begin() as connection:
        _ = await connection.execute(
            text(
                """INSERT INTO tb_order(
                    signal_id,account_id,ticker,quantity,entry_price,status,
                    idempotency_key,order_type,closes_order_id)
                VALUES (:signal,:account,'CLOSEA',2,130,'planned',
                    'close-acct-sell','close',:closes)"""
            ),
            {"signal": sell_signal, "account": account_id, "closes": buy_order_id},
        )
    _ = await store.domain.record_completed_fill(
        CompletedFillWrite(
            idempotency_key="close-acct-sell",
            broker_order_id="close-acct-sell-order",
            broker_fill_id="close-acct-sell-fill",
            quantity=2,
            price=Decimal("130.00"),
            filled_at=close_cycle,
            side=FillSide.SELL,
        )
    )

    # Then: cash grew by the sale proceeds and the fill row records the direction
    async with engine.begin() as connection:
        cash_after_close = await connection.scalar(
            text("SELECT cash FROM tb_account WHERE id = :aid"), {"aid": account_id}
        )
        fill_side = await connection.scalar(
            text("SELECT side FROM tb_fill WHERE broker_fill_id = 'close-acct-sell-fill'")
        )
    await engine.dispose()
    await store.close()
    assert Decimal(str(cash_after_close)) == Decimal(str(cash_after_buy)) + Decimal("260.00")
    assert fill_side == "sell"
