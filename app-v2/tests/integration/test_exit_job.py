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
VALUES (:day,:ticker,:cycle,:inv_type,'buy',0.800,
    2,'fixture','[]','{}',100,100,100,100,100,0,0,100,100)
RETURNING id"""

_ENTRY_DAY = date(2026, 7, 6)


async def _seed_holding(
    database_url: str, suffix: str, inv_type: str = "aggressive"
) -> tuple[int, str]:
    """Seed one account holding 2 shares bought at 100 with an 85/120 bracket."""
    engine = create_async_engine(database_url)
    ticker = f"EXJ{suffix}"
    async with engine.begin() as connection:
        account_id = await connection.scalar(
            text(
                """INSERT INTO tb_account(
                    broker_account_id,currency,cash,equity,buying_power,is_paper,
                    status,inv_type)
                VALUES (:bid,'USD',100000,100000,100000,TRUE,'active',:inv_type)
                RETURNING id"""
            ),
            {"bid": f"TEST-EXITJOB-{suffix}", "inv_type": inv_type},
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
                "inv_type": inv_type,
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


async def _seed_sell_judgement(
    database_url: str, ticker: str, as_of: date, inv_type: str, decision: str
) -> None:
    """Record what the analysis job would have written: a judged sell, then a verdict."""
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        _ = await connection.execute(
            text(
                """INSERT INTO tb_daily_pick(
                    trade_date,ticker,universe_as_of,bucket,rank,sector,score)
                VALUES (:day,:ticker,:entry,'backfill',1,'test',1)
                ON CONFLICT DO NOTHING"""
            ),
            {"day": as_of, "ticker": ticker, "entry": _ENTRY_DAY},
        )
        signal_id = await connection.scalar(
            text(
                _INSERT_SIGNAL.replace("'buy'", "'sell'")
            ),
            {
                "day": as_of,
                "ticker": ticker,
                "cycle": datetime(2026, 7, 9, 12, tzinfo=UTC),
                "inv_type": inv_type,
            },
        )
        _ = await connection.execute(
            text(
                """INSERT INTO tb_critic_verdict(
                    signal_id,ticker,decision,category,objection,confidence,
                    decided_layer,verdict_source)
                VALUES (:signal,:ticker,:decision,'model_review','fixture',0.0,
                    'gate','fresh')"""
            ),
            {"signal": signal_id, "ticker": ticker, "decision": decision},
        )
    await engine.dispose()


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
                "inv_type": "aggressive",
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


@pytest.mark.anyio
async def test_the_close_signal_inherits_the_persona_that_opened_the_position() -> None:
    """청산 시그널의 성향은 진입을 결정한 그 성향이어야 한다.

    지금까지는 리터럴 'aggressive'가 박혀 있었다. 원장의 유일성 축이
    ``(ticker, cycle_ts, inv_type)``이라 이름이 틀리면 conservative 계좌의
    매수와 매도가 서로 다른 페르소나의 기록으로 갈라지고, role_11이 자기
    판단의 결말을 못 찾는다. 청산은 새 판단이 아니라 **끝난 논지의 마무리**다.
    """
    # Given: a conservative account holding a position it decided on as conservative.
    assert DATABASE_URL is not None
    account_id, ticker = await _seed_holding(DATABASE_URL, "persona", "conservative")
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    job = ExitJob(store=store, broker=MockBroker(), time_exit_bdays=10)

    # When
    closed = await job.run(as_of=date(2026, 7, 9), observations=_stopped_out(ticker))

    # Then
    assert len(closed) == 1
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        inv_type = await connection.scalar(
            text(
                """SELECT s.inv_type FROM tb_order o
                JOIN tb_strategist_signals s ON s.id = o.signal_id
                WHERE o.account_id = :account AND o.order_type = 'close'"""
            ),
            {"account": account_id},
        )
    await engine.dispose()
    assert inv_type == "conservative"
    await store.close()


@pytest.mark.anyio
async def test_an_approved_sell_judgement_closes_the_position() -> None:
    """3층 soft path 왕복 — 하드 이벤트 없이 **판단만으로** 팔리는 유일한 경로.

    이 연결이 없으면 07이 sell을 내도 원장에는 아무 일도 일어나지 않고,
    논지가 무너진 포지션이 시간 청산(10영업일)까지 방치된다.
    """
    # Given: 보유 + 오늘 그 종목에 승인된 aggressive 매도 판단
    assert DATABASE_URL is not None
    _, ticker = await _seed_holding(DATABASE_URL, "soft")
    as_of = date(2026, 7, 9)
    await _seed_sell_judgement(DATABASE_URL, ticker, as_of, "aggressive", "pass")
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    judged = await store.domain.approved_sell_profiles(as_of, (ticker,))
    job = ExitJob(store=store, broker=MockBroker(), time_exit_bdays=10)

    # When: 브래킷도 하드 이벤트도 없는 평범한 날
    closed = await job.run(
        as_of=as_of,
        observations={
            ticker: DailyObservation(
                day_range=DailyRange(low=Decimal("95.00"), high=Decimal("101.00")),
                last_price=Decimal("96.00"),
                sell_signal_profiles=judged.get(ticker, frozenset()),
            )
        },
    )

    # Then
    assert [decision.reason for decision in closed] == [ExitReason.THESIS_SOFT]
    assert closed[0].reference_price == Decimal("96.00")
    await store.close()


@pytest.mark.anyio
async def test_a_rejected_sell_judgement_does_not_reach_the_exit_rules() -> None:
    """매도는 되돌릴 수 없다 — 반박당한 판단으로 파는 것은 반박을 안 한 것보다 나쁘다."""
    # Given
    assert DATABASE_URL is not None
    _, ticker = await _seed_holding(DATABASE_URL, "rej")
    as_of = date(2026, 7, 9)
    await _seed_sell_judgement(DATABASE_URL, ticker, as_of, "aggressive", "reject")
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()

    # When
    judged = await store.domain.approved_sell_profiles(as_of, (ticker,))

    # Then
    assert judged == {}
    await store.close()


@pytest.mark.anyio
async def test_the_exit_jobs_own_sell_signals_are_not_read_back() -> None:
    """청산이 남기는 기계적 sell 시그널을 자기가 다시 읽으면 두 번 판다.

    크리틱 조인이 그 필터다 — 기계적 청산에는 평결 행이 없다.
    """
    # Given: 하드 이벤트로 한 번 팔린 뒤 원장에 남은 sell 시그널
    assert DATABASE_URL is not None
    _, ticker = await _seed_holding(DATABASE_URL, "mech")
    as_of = date(2026, 7, 9)
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    job = ExitJob(store=store, broker=MockBroker(), time_exit_bdays=10)
    closed = await job.run(
        as_of=as_of,
        observations={ticker: DailyObservation(last_price=Decimal(90), has_hard_event=True)},
    )
    assert len(closed) == 1

    # When
    judged = await store.domain.approved_sell_profiles(as_of, (ticker,))

    # Then
    assert judged == {}
    await store.close()
