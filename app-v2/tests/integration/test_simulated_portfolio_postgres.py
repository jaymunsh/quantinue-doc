from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

import anyio
import pytest
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from quantinue.db.domain import PostgresDomainRepository
from quantinue.db.domain_records import AccountWrite, CompletedBuyWrite
from quantinue.db.postgres import PostgresRunStore
from quantinue.db.simulated_portfolio import (
    MarkSource,
    project_buy_only_portfolio,
)

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")
OPENING_CASH = Decimal("1000000.00")
NOW = datetime(2035, 1, 2, 14, tzinfo=UTC)
_INT = TypeAdapter(int)
_DECIMAL = TypeAdapter(Decimal)


async def _seed_reserved_order(database_url: str, account_id: int, identity: str) -> None:
    engine = create_async_engine(database_url)
    ticker = identity.upper()
    async with engine.begin() as connection:
        _ = await connection.execute(
            text(
                """INSERT INTO tb_universe(as_of_date,ticker,company_name,market_cap)
                VALUES (:day,:ticker,'Ledger Fixture',1) ON CONFLICT DO NOTHING"""
            ),
            {"day": NOW.date(), "ticker": ticker},
        )
        _ = await connection.execute(
            text(
                """INSERT INTO tb_daily_pick(
                trade_date,ticker,universe_as_of,bucket,rank,sector,score)
                VALUES (:day,:ticker,:day,'backfill',1,'test',1)
                ON CONFLICT DO NOTHING"""
            ),
            {"day": NOW.date(), "ticker": ticker},
        )
        signal_id = _INT.validate_python(
            await connection.scalar(
                text(
                    """INSERT INTO tb_strategist_signals(
                trade_date,ticker,cycle_ts,inv_type,side,conviction,signal_consensus,
                summary,evidence,sizing_hint,decision_close,current_price,day_high,
                day_low,close_prev,volume,turnover,high_52w,low_52w)
                VALUES (:day,:ticker,:cycle,'conservative','buy',0.8,2,'ledger',
                '[]','{}',100,100,100,100,100,0,0,100,100) RETURNING id"""
                ),
                {"day": NOW.date(), "ticker": ticker, "cycle": NOW},
            ),
        )
        _ = await connection.execute(
            text(
                """INSERT INTO tb_order(
                signal_id,account_id,ticker,quantity,entry_price,stop_price,
                take_profit_price,status,idempotency_key)
                VALUES (:signal,:account,:ticker,2,100,85,120,'planned',:identity)"""
            ),
            {
                "signal": signal_id,
                "account": account_id,
                "ticker": ticker,
                "identity": identity,
            },
        )
    await engine.dispose()


async def _complete_mark_run(database_url: str, ticker: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        _ = await connection.execute(
            text(
                """INSERT INTO pipeline_runs(
                run_id,idempotency_key,ticker,cycle_ts,status,payload)
                VALUES ('restart-mark-run','restart-mark-key',:ticker,:cycle,'completed','{}')"""
            ),
            {"ticker": ticker, "cycle": NOW},
        )
    await engine.dispose()


async def _seed_nonterminal_mark_candidates(database_url: str, ticker: str) -> None:
    engine = create_async_engine(database_url)
    candidates = (
        (NOW.replace(hour=13), Decimal("90.00"), "completed", "older"),
        (NOW.replace(hour=15), Decimal("150.00"), "failed", "failed"),
        (NOW.replace(hour=16), Decimal("200.00"), "running", "running"),
    )
    async with engine.begin() as connection:
        for cycle, price, status, suffix in candidates:
            _ = await connection.execute(
                text(
                    """INSERT INTO tb_strategist_signals(
                    trade_date,ticker,cycle_ts,inv_type,side,conviction,signal_consensus,
                    summary,evidence,sizing_hint,decision_close,current_price,day_high,
                    day_low,close_prev,volume,turnover,high_52w,low_52w)
                    VALUES (:day,:ticker,:cycle,'conservative','buy',0.8,2,'mark',
                    '[]','{}',:price,:price,:price,:price,:price,0,0,:price,:price)"""
                ),
                {"day": NOW.date(), "ticker": ticker, "cycle": cycle, "price": price},
            )
            _ = await connection.execute(
                text(
                    """INSERT INTO pipeline_runs(
                    run_id,idempotency_key,ticker,cycle_ts,status,payload)
                    VALUES (:run_id,:key,:ticker,:cycle,:status,'{}')"""
                ),
                {
                    "run_id": f"mark-{suffix}-run",
                    "key": f"mark-{suffix}-key",
                    "ticker": ticker,
                    "cycle": cycle,
                    "status": status,
                },
            )
    await engine.dispose()


def _buy(identity: str) -> CompletedBuyWrite:
    return CompletedBuyWrite(
        idempotency_key=identity,
        broker_order_id=f"broker-{identity}",
        broker_fill_id=f"fill-{identity}",
        quantity=2,
        price=Decimal("100.00"),
        filled_at=NOW,
    )


@pytest.mark.anyio
@pytest.mark.skipif(DATABASE_URL is None, reason="disposable PostgreSQL URL not provided")
async def test_account_initialization_is_concurrent_and_never_resets_mutated_cash() -> None:
    # Given
    assert DATABASE_URL is not None
    first = PostgresDomainRepository(DATABASE_URL)
    second = PostgresDomainRepository(DATABASE_URL)
    await first.initialize()
    await second.initialize()
    account = AccountWrite("init-only-local-simulated", OPENING_CASH, OPENING_CASH, OPENING_CASH)
    ids: list[int] = []

    async def initialize(repository: PostgresDomainRepository) -> None:
        ids.append(await repository.save_account(account))

    # When
    async with anyio.create_task_group() as group:
        _ = group.start_soon(initialize, first)
        _ = group.start_soon(initialize, second)
    _ = await _seed_reserved_order(DATABASE_URL, ids[0], "once")
    _ = await first.record_completed_buy(_buy("once"))
    replayed_id = await second.save_account(account)

    # Then
    engine = create_async_engine(DATABASE_URL)
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    """SELECT cash,equity,buying_power FROM tb_account
                    WHERE broker_account_id='init-only-local-simulated'"""
                )
            )
        ).one()
    assert ids == [ids[0], ids[0]]
    assert replayed_id == ids[0]
    assert tuple(row) == (Decimal("999800.00"), OPENING_CASH, Decimal("999800.00"))
    await engine.dispose()
    await first.close()
    await second.close()


@pytest.mark.anyio
@pytest.mark.skipif(DATABASE_URL is None, reason="disposable PostgreSQL URL not provided")
async def test_unique_buy_fill_debits_once_and_survives_store_reopen() -> None:
    # Given
    assert DATABASE_URL is not None
    repository = PostgresDomainRepository(DATABASE_URL)
    await repository.initialize()
    account_id = await repository.save_account(
        AccountWrite("quantinue-local-simulated", OPENING_CASH, OPENING_CASH, OPENING_CASH)
    )
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    baseline = await store.simulated_portfolio(OPENING_CASH)
    await store.close()
    await _seed_reserved_order(DATABASE_URL, account_id, "restart")

    # When
    concurrent = PostgresDomainRepository(DATABASE_URL)
    await concurrent.initialize()
    fill_ids: list[int] = []

    async def record_once(candidate: PostgresDomainRepository) -> None:
        fill_ids.append(await candidate.record_completed_buy(_buy("restart")))

    async with anyio.create_task_group() as group:
        _ = group.start_soon(record_once, repository)
        _ = group.start_soon(record_once, concurrent)
    await _complete_mark_run(DATABASE_URL, "RESTART")
    await _seed_nonterminal_mark_candidates(DATABASE_URL, "RESTART")
    before_reopen = PostgresRunStore(DATABASE_URL)
    await before_reopen.initialize()
    before = await before_reopen.simulated_portfolio(OPENING_CASH)
    await before_reopen.close()
    reopened = PostgresRunStore(DATABASE_URL)
    await reopened.initialize()
    after = await reopened.simulated_portfolio(OPENING_CASH)

    # Then
    assert fill_ids == [fill_ids[0], fill_ids[0]]
    assert after == before
    assert after.account.current_cash == baseline.account.current_cash - Decimal("200.00")
    persisted_position = next(
        position for position in after.positions if position.ticker == "RESTART"
    )
    memory_projection = project_buy_only_portfolio(
        OPENING_CASH,
        after.orders,
        after.fills,
        tuple(position.mark for position in after.positions),
    )
    assert after == memory_projection
    assert persisted_position.mark.source is MarkSource.COMPLETED_RUN
    assert persisted_position.mark.price == Decimal("100.00")
    assert persisted_position.mark.as_of == NOW
    assert sum(fill.fill_id == "fill-restart" for fill in after.fills) == 1
    await reopened.close()
    await concurrent.close()
    await repository.close()


@pytest.mark.anyio
@pytest.mark.skipif(DATABASE_URL is None, reason="disposable PostgreSQL URL not provided")
async def test_insufficient_cash_rolls_back_fill_and_account_debit() -> None:
    # Given
    assert DATABASE_URL is not None
    repository = PostgresDomainRepository(DATABASE_URL)
    await repository.initialize()
    account_id = await repository.save_account(
        AccountWrite(
            "insufficient-local-simulated",
            Decimal("100.00"),
            Decimal("100.00"),
            Decimal("100.00"),
        )
    )
    await _seed_reserved_order(DATABASE_URL, account_id, "insufficient")

    # When / Then
    with pytest.raises(ValueError, match="insufficient simulated cash"):
        _ = await repository.record_completed_buy(_buy("insufficient"))
    engine = create_async_engine(DATABASE_URL)
    async with engine.connect() as connection:
        fill_count = _INT.validate_python(
            await connection.scalar(
                text("SELECT count(*) FROM tb_fill WHERE broker_fill_id='fill-insufficient'")
            )
        )
        cash = _DECIMAL.validate_python(
            await connection.scalar(
                text("SELECT cash FROM tb_account WHERE id=:account"),
                {"account": account_id},
            )
        )
    assert fill_count == 0
    assert cash == Decimal("100.00")
    await engine.dispose()
    await repository.close()
