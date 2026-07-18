from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from quantinue.core.contracts import PipelineContext, PipelineRequest
from quantinue.db.contracts import (
    AppOrderExposureReservationOutcome,
    DailyOrderReservation,
)
from quantinue.db.domain_records import CompletedBuyWrite
from quantinue.db.memory import InMemoryRunStore
from quantinue.db.postgres import PostgresRunStore

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")
OPENING_CASH = Decimal("1000000.00")


async def _seed_stage_parent(database_url: str, ticker: str, cycle: datetime) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        _ = await connection.execute(
            text(
                """INSERT INTO tb_universe(as_of_date,ticker,company_name,market_cap)
                VALUES (:day,:ticker,'Stage Lifecycle',1) ON CONFLICT DO NOTHING"""
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


@pytest.mark.anyio
@pytest.mark.skipif(DATABASE_URL is None, reason="disposable PostgreSQL URL not provided")
async def test_component_08_second_distinct_run_does_not_reset_debited_account() -> None:
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    first_cycle = datetime(2036, 1, 2, 14, tzinfo=UTC)
    await _seed_stage_parent(DATABASE_URL, "STAGEA", first_cycle)
    first_context = replace(
        PipelineContext(request=PipelineRequest(ticker="STAGEA", cycle_ts=first_cycle)),
        last_price=100.0,
        side="buy",
        conviction=0.8,
        critic_approved=True,
    )
    first_result = await store.stage_completed("08", first_context, first_context)
    assert first_result.account_id is not None
    assert first_result.signal_id is not None
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        _ = await connection.execute(
            text(
                """INSERT INTO tb_order(
                signal_id,account_id,ticker,quantity,entry_price,stop_price,
                take_profit_price,status,idempotency_key)
                VALUES (:signal,:account,'STAGEA',2,100,85,120,'planned','stage-08-fill')"""
            ),
            {"signal": first_result.signal_id, "account": first_result.account_id},
        )
    await engine.dispose()
    _ = await store.domain.record_completed_buy(
        CompletedBuyWrite(
            idempotency_key="stage-08-fill",
            broker_order_id="stage-08-broker-order",
            broker_fill_id="stage-08-broker-fill",
            quantity=2,
            price=Decimal("100.00"),
            filled_at=first_cycle,
        )
    )
    debited = await store.simulated_portfolio(OPENING_CASH)

    # When
    second_cycle = datetime(2036, 1, 3, 14, tzinfo=UTC)
    await _seed_stage_parent(DATABASE_URL, "STAGEB", second_cycle)
    second_context = replace(
        PipelineContext(request=PipelineRequest(ticker="STAGEB", cycle_ts=second_cycle)),
        last_price=120.0,
        side="buy",
        conviction=0.7,
        critic_approved=True,
    )
    second_result = await store.stage_completed("08", second_context, second_context)
    after_second_stage = await store.simulated_portfolio(OPENING_CASH)

    # Then
    assert second_result.account_id == first_result.account_id
    assert after_second_stage.account.current_cash == debited.account.current_cash
    await store.close()


@pytest.mark.anyio
@pytest.mark.skipif(DATABASE_URL is None, reason="disposable PostgreSQL URL not provided")
async def test_memory_and_postgres_public_store_contracts_produce_exact_snapshot_parity() -> None:
    # Given
    assert DATABASE_URL is not None
    cycle = datetime(2037, 2, 3, 14, tzinfo=UTC)
    request = PipelineRequest(ticker="PARITY", cycle_ts=cycle)
    postgres = PostgresRunStore(
        DATABASE_URL,
        OPENING_CASH,
        account_identity="quantinue-parity-simulated",
    )
    memory = InMemoryRunStore(OPENING_CASH)
    await postgres.initialize()
    await memory.initialize()
    await _seed_stage_parent(DATABASE_URL, request.ticker, cycle)
    postgres_claim = await postgres.claim("parity-postgres-run", request)
    memory_claim = await memory.claim("parity-memory-run", request)
    assert postgres_claim.context is not None
    assert memory_claim.context is not None
    postgres_context = replace(
        postgres_claim.context,
        last_price=125.0,
        side="buy",
        conviction=0.8,
        critic_approved=True,
    )
    postgres_context = await postgres.stage_completed(
        "08", postgres_claim.context, postgres_context
    )
    assert postgres_context.account_id is not None
    assert postgres_context.signal_id is not None
    postgres_attempt = await postgres.start_attempt("parity-postgres-run", "08", cycle)
    await postgres.complete_stage("parity-postgres-run", postgres_context, postgres_attempt)
    await postgres.finish_run("parity-postgres-run", postgres_context.to_run())
    memory_context = replace(memory_claim.context, last_price=125.0)
    memory_attempt = await memory.start_attempt("parity-memory-run", "08", cycle)
    await memory.complete_stage("parity-memory-run", memory_context, memory_attempt)
    await memory.finish_run("parity-memory-run", memory_context.to_run())
    reservation = DailyOrderReservation(
        account_id=postgres_context.account_id,
        trade_date=cycle.date(),
        signal_id=postgres_context.signal_id,
        idempotency_key="parity-completed-buy",
        ticker=request.ticker,
        quantity=2,
        entry_price=Decimal("100.00"),
        stop_price=Decimal("85.00"),
        take_profit_price=Decimal("120.00"),
        cap=5,
    )
    postgres_reservation = await postgres.reserve_daily_new_order(reservation)
    memory_reservation = await memory.reserve_daily_new_order(reservation)
    assert postgres_reservation.outcome is AppOrderExposureReservationOutcome.ACQUIRED
    assert memory_reservation.outcome is AppOrderExposureReservationOutcome.ACQUIRED
    completed_buy = CompletedBuyWrite(
        idempotency_key=reservation.idempotency_key,
        broker_order_id="parity-broker-order",
        broker_fill_id="parity-broker-fill",
        quantity=reservation.quantity,
        price=reservation.entry_price,
        filled_at=cycle,
    )

    # When
    _ = await postgres.record_completed_buy(completed_buy)
    _ = await memory.record_completed_buy(completed_buy)
    postgres_snapshot = await postgres.simulated_portfolio(OPENING_CASH)
    memory_snapshot = await memory.simulated_portfolio(OPENING_CASH)

    # Then
    assert postgres_snapshot == memory_snapshot
    await postgres.close()
    await memory.close()
