"""Phase 1c: the exit job turns a decision into a durable, idempotent close."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from quantinue.broker.bracket_trigger import DailyRange
from quantinue.broker.mock import MockBroker
from quantinue.db.postgres import PostgresRunStore
from quantinue.roles.exits import DailyObservation, ExitReason
from quantinue.roles.exits.job import ExitJob

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="disposable PostgreSQL URL not provided"
)

_INSERT_SIGNAL = """INSERT INTO tb_strategist_signals(
    trade_date,ticker,cycle_ts,inv_type,side,conviction,
    signal_consensus,summary,evidence,sizing_hint,decision_close,current_price,
    day_high,day_low,close_prev,volume,turnover,high_52w,low_52w)
VALUES (:day,:ticker,:cycle,'aggressive','buy',0.800,
    2,'fixture','[]','{}',100,100,100,100,100,0,0,100,100)
RETURNING id"""

_ENTRY_DAY = date(2026, 7, 6)


async def _seed_holding(database_url: str, suffix: str) -> tuple[int, str]:
    """Seed one account holding 2 shares bought at 100 with an 85/120 bracket."""
    engine = create_async_engine(database_url)
    ticker = f"EXJ{suffix}"
    async with engine.begin() as connection:
        account_id = await connection.scalar(
            text(
                """INSERT INTO tb_account(
                    broker_account_id,currency,cash,equity,buying_power,is_paper,
                    status,inv_type)
                VALUES (:bid,'USD',100000,100000,100000,TRUE,'active','aggressive')
                RETURNING id"""
            ),
            {"bid": f"TEST-EXITJOB-{suffix}"},
        )
        _ = await connection.execute(
            text(
                """INSERT INTO tb_universe(as_of_date,ticker,company_name,market_cap)
                VALUES (:day,:ticker,'Exit Job',1) ON CONFLICT DO NOTHING"""
            ),
            {"day": _ENTRY_DAY, "ticker": ticker},
        )
        _ = await connection.execute(
            text(
                """INSERT INTO tb_daily_pick(
                    trade_date,ticker,universe_as_of,bucket,rank,sector,score)
                VALUES (:day,:ticker,:day,'backfill',1,'test',1)
                ON CONFLICT DO NOTHING"""
            ),
            {"day": _ENTRY_DAY, "ticker": ticker},
        )
        signal_id = await connection.scalar(
            text(_INSERT_SIGNAL),
            {
                "day": _ENTRY_DAY,
                "ticker": ticker,
                "cycle": datetime(2026, 7, 6, 14, tzinfo=UTC),
            },
        )
        order_id = await connection.scalar(
            text(
                """INSERT INTO tb_order(
                    signal_id,account_id,ticker,quantity,entry_price,stop_price,
                    take_profit_price,status,idempotency_key,order_type)
                VALUES (:signal,:account,:ticker,2,100,85,120,'filled',:key,'bracket')
                RETURNING id"""
            ),
            {
                "signal": signal_id,
                "account": account_id,
                "ticker": ticker,
                "key": f"exitjob-{suffix}-buy",
            },
        )
        _ = await connection.execute(
            text(
                """INSERT INTO tb_fill(
                    order_id,side,quantity,price,filled_at,broker_fill_id)
                VALUES (:order,'buy',2,100,:at,:key)"""
            ),
            {
                "order": order_id,
                "at": datetime(2026, 7, 6, 14, tzinfo=UTC),
                "key": f"exitjob-{suffix}-buy-fill",
            },
        )
    await engine.dispose()
    return int(account_id or 0), ticker


def _stopped_out(ticker: str) -> dict[str, DailyObservation]:
    """A day whose low pierced the 85 stop."""
    return {
        ticker: DailyObservation(
            day_range=DailyRange(low=Decimal("80.00"), high=Decimal("101.00")),
            last_price=Decimal("82.00"),
        )
    }


@pytest.mark.anyio
async def test_a_stopped_out_position_is_closed_and_recorded() -> None:
    """A triggered stop must produce a sell signal, a close order, and a sell fill."""
    # Given
    assert DATABASE_URL is not None
    account_id, ticker = await _seed_holding(DATABASE_URL, "a")
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    job = ExitJob(store=store, broker=MockBroker(), time_exit_bdays=10)

    # When
    closed = await job.run(as_of=date(2026, 7, 9), observations=_stopped_out(ticker))

    # Then
    assert len(closed) == 1
    assert closed[0].reason is ExitReason.STOP
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        row = (
            await connection.execute(
                text(
                    """SELECT o.order_type, o.closes_order_id, o.status, o.entry_price,
                            s.side AS signal_side, f.side AS fill_side, f.price
                    FROM tb_order o
                    JOIN tb_strategist_signals s ON s.id = o.signal_id
                    JOIN tb_fill f ON f.order_id = o.id
                    WHERE o.account_id = :account AND o.order_type = 'close'"""
                ),
                {"account": account_id},
            )
        ).one()
        cash = await connection.scalar(
            text("SELECT cash FROM tb_account WHERE id = :aid"), {"aid": account_id}
        )
    await engine.dispose()

    # 청산은 자기 sell 시그널을 갖고, 닫는 매수를 가리키며, 손절가에 체결된다
    assert row.signal_side == "sell"
    assert row.closes_order_id is not None
    assert row.status == "filled"
    assert row.fill_side == "sell"
    assert Decimal(str(row.price)) == Decimal("85.00")
    # 2주 * 85 = 170이 현금으로 들어온다
    assert Decimal(str(cash)) == Decimal("100170.00")
    await store.close()


@pytest.mark.anyio
async def test_the_position_is_no_longer_open_after_the_job_runs() -> None:
    """청산 후에도 보유로 잡히면 한도가 영원히 묶인다."""
    # Given
    assert DATABASE_URL is not None
    account_id, ticker = await _seed_holding(DATABASE_URL, "b")
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    job = ExitJob(store=store, broker=MockBroker(), time_exit_bdays=10)

    # When
    _ = await job.run(as_of=date(2026, 7, 9), observations=_stopped_out(ticker))
    state = await store.domain.account_risk_state(account_id)

    # Then
    assert state is not None
    assert state.open_position_count == 0
    await store.close()


@pytest.mark.anyio
async def test_running_the_job_twice_does_not_sell_twice() -> None:
    """멱등: 재실행이 두 번째 청산이 되면 갖고 있지도 않은 주식을 판다."""
    # Given
    assert DATABASE_URL is not None
    account_id, ticker = await _seed_holding(DATABASE_URL, "c")
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    job = ExitJob(store=store, broker=MockBroker(), time_exit_bdays=10)

    # When
    first = await job.run(as_of=date(2026, 7, 9), observations=_stopped_out(ticker))
    second = await job.run(as_of=date(2026, 7, 10), observations=_stopped_out(ticker))

    # Then
    assert len(first) == 1
    assert second == ()
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        closes = await connection.scalar(
            text(
                """SELECT count(*) FROM tb_order
                WHERE account_id = :account AND order_type = 'close'"""
            ),
            {"account": account_id},
        )
    await engine.dispose()
    assert int(closes or 0) == 1
    await store.close()


@pytest.mark.anyio
async def test_a_quiet_day_closes_nothing() -> None:
    """정상 보유를 건드리지 않는다 — 이게 깨지면 전 포지션이 매일 청산된다."""
    # Given
    assert DATABASE_URL is not None
    _, ticker = await _seed_holding(DATABASE_URL, "d")
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    job = ExitJob(store=store, broker=MockBroker(), time_exit_bdays=10)

    # When
    closed = await job.run(
        as_of=date(2026, 7, 9),
        observations={
            ticker: DailyObservation(
                day_range=DailyRange(low=Decimal("95.00"), high=Decimal("110.00")),
                last_price=Decimal("105.00"),
            )
        },
    )

    # Then
    assert closed == ()
    await store.close()


@pytest.mark.anyio
async def test_a_position_without_an_observation_is_left_alone() -> None:
    """수집이 빠진 종목을 청산하면 관측 실패가 매도로 둔갑한다."""
    # Given
    assert DATABASE_URL is not None
    _, _ticker = await _seed_holding(DATABASE_URL, "e")
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    job = ExitJob(store=store, broker=MockBroker(), time_exit_bdays=10)

    # When
    closed = await job.run(as_of=date(2026, 7, 9), observations={})

    # Then
    assert closed == ()
    await store.close()


async def _seed_second_position(database_url: str, account_id: int, ticker: str) -> int:
    """Add a second open bracket for the same account and ticker."""
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        signal_id = await connection.scalar(
            text(_INSERT_SIGNAL),
            {
                "day": _ENTRY_DAY,
                "ticker": ticker,
                # 진입 시그널끼리는 cycle_ts로 갈린다 — 다른 날 산 두 번째 매수.
                "cycle": datetime(2026, 7, 6, 15, tzinfo=UTC),
            },
        )
        order_id = await connection.scalar(
            text(
                """INSERT INTO tb_order(
                    signal_id,account_id,ticker,quantity,entry_price,stop_price,
                    take_profit_price,status,idempotency_key,order_type)
                VALUES (:signal,:account,:ticker,3,100,85,120,'filled',:key,'bracket')
                RETURNING id"""
            ),
            {
                "signal": signal_id,
                "account": account_id,
                "ticker": ticker,
                "key": f"exitjob-{ticker}-buy2",
            },
        )
        _ = await connection.execute(
            text(
                """INSERT INTO tb_fill(
                    order_id,side,quantity,price,filled_at,broker_fill_id)
                VALUES (:order,'buy',3,100,:at,:key)"""
            ),
            {
                "order": order_id,
                "at": datetime(2026, 7, 6, 15, tzinfo=UTC),
                "key": f"exitjob-{ticker}-buy2-fill",
            },
        )
    await engine.dispose()
    return int(order_id or 0)


@pytest.mark.anyio
async def test_two_open_positions_in_one_ticker_both_close() -> None:
    """같은 계좌가 같은 종목을 두 번 사서 둘 다 열려 있을 수 있다.

    청산 시그널의 cycle_ts를 날짜로만 잡으면 두 포지션이 같은
    (ticker, cycle_ts, inv_type) 시그널 행을 공유하고, 두 번째 청산 주문이
    UNIQUE(account_id, signal_id)에 걸려 죽는다 — 한 포지션이 못 팔린 채 남는다.
    """
    # Given
    assert DATABASE_URL is not None
    account_id, ticker = await _seed_holding(DATABASE_URL, "dbl")
    _ = await _seed_second_position(DATABASE_URL, account_id, ticker)
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    job = ExitJob(store=store, broker=MockBroker(), time_exit_bdays=10)

    # When
    closed = await job.run(as_of=date(2026, 7, 9), observations=_stopped_out(ticker))

    # Then: 두 건 다 닫힌다
    assert len(closed) == 2
    remaining = [
        position
        for position in await store.domain.open_positions()
        if position.ticker == ticker
    ]
    assert remaining == []
    await store.close()
