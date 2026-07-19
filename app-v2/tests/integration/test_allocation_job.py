"""Phase 4: the allocation job — which N of today's approved buys each account takes."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from quantinue.broker.mock import MockBroker
from quantinue.core.market_calendar import NyseCalendar
from quantinue.db.postgres import PostgresRunStore
from quantinue.orchestration.policy import (
    AllocationConfig,
    GatesConfig,
    ProfileConfig,
)
from quantinue.roles.allocation.job import AllocationJob

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="disposable PostgreSQL URL not provided"
)

# 테스트마다 다른 거래일을 쓴다 — 후보 풀이 (trade_date) 단위라서, 날을
# 공유하면 모든 테스트 계좌가 서로의 후보를 산다(실제로 그렇게 오염됐다).
_HAPPY_DAY = date(2026, 7, 20)
_SEQ_DAY = date(2026, 7, 21)
_LOSS_DAY = date(2026, 7, 22)
_IDEM_DAY = date(2026, 7, 23)
_HELD_DAY = date(2026, 7, 24)


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(), tzinfo=UTC)


async def _seed_account(suffix: str, *, cash: int, inv_type: str = "aggressive") -> int:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        account_id = await connection.scalar(
            text(
                """INSERT INTO tb_account(
                    broker_account_id,currency,cash,equity,buying_power,is_paper,
                    status,inv_type)
                VALUES (:bid,'USD',:cash,:cash,:cash,TRUE,'active',:inv_type)
                RETURNING id"""
            ),
            {"bid": f"TEST-ALLOC-{suffix}", "cash": cash, "inv_type": inv_type},
        )
    await engine.dispose()
    return int(account_id or 0)


async def _seed_candidate(
    ticker: str,
    day: date,
    *,
    inv_type: str = "aggressive",
    conviction: str = "0.800",
    price: int = 50,
) -> int:
    """One critic-approved buy — what the analysis job leaves for allocation."""
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        _ = await connection.execute(
            text(
                """INSERT INTO tb_universe(as_of_date,ticker,company_name,market_cap)
                VALUES (:day,:ticker,'Allocation',1) ON CONFLICT DO NOTHING"""
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
        signal_id = await connection.scalar(
            text(
                """INSERT INTO tb_strategist_signals(
                    trade_date,ticker,cycle_ts,inv_type,side,conviction,
                    signal_consensus,summary,evidence,sizing_hint,decision_close,
                    current_price,day_high,day_low,close_prev,volume,turnover,
                    high_52w,low_52w)
                VALUES (:day,:ticker,:cycle,:inv_type,'buy',:conviction,
                    2,'fixture','[]','{}',:price,:price,:price,:price,:price,0,0,
                    :price,:price)
                RETURNING id"""
            ),
            {
                "day": day,
                "ticker": ticker,
                "cycle": _midnight(day),
                "inv_type": inv_type,
                "conviction": conviction,
                "price": price,
            },
        )
        _ = await connection.execute(
            text(
                """INSERT INTO tb_critic_verdict(
                    signal_id,ticker,decision,category,objection,confidence,
                    decided_layer,verdict_source)
                VALUES (:signal,:ticker,'pass','model_review','fixture',0.0,
                    'gate','fresh')"""
            ),
            {"signal": signal_id, "ticker": ticker},
        )
    await engine.dispose()
    return int(signal_id or 0)


def _job(store: PostgresRunStore, **profile_overrides: object) -> AllocationJob:
    profile = ProfileConfig.model_validate(
        {
            "buy_threshold": 0.65,
            "max_positions": 10,
            "max_weight": 0.20,
            "daily_loss_limit": 0.04,
            "min_cash_ratio": 0.10,
            **profile_overrides,
        }
    )
    return AllocationJob(
        store=store,
        broker=MockBroker(),
        profiles={"aggressive": profile},
        gates=GatesConfig(),
        allocation=AllocationConfig(),
        calendar=NyseCalendar(),
    )


async def _read_rows(query: str, **params: object) -> list[tuple]:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        rows = (await connection.execute(text(query), params)).all()
    await engine.dispose()
    return [tuple(row) for row in rows]


@pytest.mark.anyio
async def test_approved_buys_become_filled_orders_and_cash_moves() -> None:
    """행복 경로: 승인 후보 2 → 브래킷 주문 2 체결, 현금 차감, 스냅샷 기록.

    수량은 리스크 예산 min(equity*4%/15%, equity*20%)/가격 — 기존 사이징 승계.
    equity 100k · 가격 50 → min(26.6k, 20k)/50 = 400주 · 20k씩.
    """
    account_id = await _seed_account("HAPPY", cash=100_000)
    _ = await _seed_candidate("ALH1", _HAPPY_DAY, conviction="0.900")
    _ = await _seed_candidate("ALH2", _HAPPY_DAY, conviction="0.800")

    store = PostgresRunStore(DATABASE_URL or "")
    await store.initialize()
    try:
        detail = await _job(store).run(as_of=_HAPPY_DAY)
    finally:
        await store.close()

    orders = await _read_rows(
        """SELECT ticker, quantity, status, order_type FROM tb_order
           WHERE account_id=:account ORDER BY ticker""",
        account=account_id,
    )
    assert orders == [
        ("ALH1", 400, "filled", "bracket"),
        ("ALH2", 400, "filled", "bracket"),
    ]
    cash = await _read_rows(
        "SELECT cash FROM tb_account WHERE id=:account", account=account_id
    )
    assert cash[0][0] == Decimal("60000.00")
    snapshot = await _read_rows(
        """SELECT equity FROM tb_account_equity_daily
           WHERE account_id=:account AND trade_date=:day""",
        account=account_id,
        day=_HAPPY_DAY,
    )
    assert snapshot[0][0] == Decimal("100000.00")
    plans = await _read_rows(
        """SELECT ticker, decision FROM tb_order_plan
           WHERE account_id=:account ORDER BY ticker""",
        account=account_id,
    )
    assert plans == [("ALH1", "planned"), ("ALH2", "planned")]
    assert "2 bought" in detail


@pytest.mark.anyio
async def test_cash_depletes_sequentially_not_from_the_opening_balance() -> None:
    """순차 갱신이 이 잡의 새 부분이다 — 후보마다 계좌를 다시 읽지 않으면
    모든 후보가 첫 현금 기준으로 통과해 잔고보다 많이 산다.

    cash 30k → 회당 6k(equity 20% 캡). 30→24→18→12→6에서 다음 매수는
    현금 바닥(10% = 3k)을 뚫으므로 min_cash로 멈춘다: 4건 체결 + 1건 보류.
    """
    account_id = await _seed_account("SEQ", cash=30_000)
    for index, conviction in enumerate(("0.900", "0.850", "0.800", "0.750", "0.700")):
        _ = await _seed_candidate(f"ALS{index}", _SEQ_DAY, conviction=conviction)

    store = PostgresRunStore(DATABASE_URL or "")
    await store.initialize()
    try:
        _ = await _job(store).run(as_of=_SEQ_DAY)
    finally:
        await store.close()

    orders = await _read_rows(
        "SELECT count(*) FROM tb_order WHERE account_id=:account", account=account_id
    )
    assert orders[0][0] == 4
    skipped = await _read_rows(
        """SELECT ticker, skipped_reason FROM tb_order_plan
           WHERE account_id=:account AND decision='skipped'""",
        account=account_id,
    )
    assert skipped == [("ALS4", "min_cash")]


@pytest.mark.anyio
async def test_a_breached_daily_loss_limit_blocks_every_new_buy() -> None:
    """당일 시작 equity 대비 한도를 넘긴 계좌는 그날 신규 매수가 없다.

    스냅샷은 첫 기록이 이긴다 — 잡이 다시 돌아도 아침 값을 덮지 않아야
    '당일 시작'이라는 말이 참으로 남는다.
    """
    account_id = await _seed_account("LOSS", cash=30_000)
    _ = await _seed_candidate("ALL1", _LOSS_DAY, conviction="0.900")
    engine = create_async_engine(DATABASE_URL or "")
    async with engine.begin() as connection:
        _ = await connection.execute(
            text(
                """INSERT INTO tb_account_equity_daily(account_id,trade_date,equity)
                VALUES (:account,:day,40000)"""
            ),
            {"account": account_id, "day": _LOSS_DAY},
        )
    await engine.dispose()

    store = PostgresRunStore(DATABASE_URL or "")
    await store.initialize()
    try:
        _ = await _job(store).run(as_of=_LOSS_DAY)
    finally:
        await store.close()

    orders = await _read_rows(
        "SELECT count(*) FROM tb_order WHERE account_id=:account", account=account_id
    )
    assert orders[0][0] == 0
    skipped = await _read_rows(
        """SELECT skipped_reason FROM tb_order_plan
           WHERE account_id=:account AND ticker='ALL1'""",
        account=account_id,
    )
    assert skipped == [("daily_loss_limit",)]
    snapshot = await _read_rows(
        """SELECT equity FROM tb_account_equity_daily
           WHERE account_id=:account AND trade_date=:day""",
        account=account_id,
        day=_LOSS_DAY,
    )
    assert snapshot[0][0] == Decimal(40000)


@pytest.mark.anyio
async def test_rerunning_the_job_buys_nothing_twice() -> None:
    """재실행 멱등 — 이미 산 종목은 has_position 게이트가 막고, 주문·체결·
    현금 어느 것도 두 번 움직이지 않는다."""
    account_id = await _seed_account("IDEM", cash=100_000)
    _ = await _seed_candidate("ALI1", _IDEM_DAY, conviction="0.900")

    store = PostgresRunStore(DATABASE_URL or "")
    await store.initialize()
    try:
        _ = await _job(store).run(as_of=_IDEM_DAY)
        _ = await _job(store).run(as_of=_IDEM_DAY)
    finally:
        await store.close()

    orders = await _read_rows(
        "SELECT count(*) FROM tb_order WHERE account_id=:account", account=account_id
    )
    assert orders[0][0] == 1
    cash = await _read_rows(
        "SELECT cash FROM tb_account WHERE id=:account", account=account_id
    )
    assert cash[0][0] == Decimal("80000.00")


@pytest.mark.anyio
async def test_an_already_held_ticker_is_not_bought_again() -> None:
    """이미 든 종목의 재승인은 매수가 아니다 — 물타기는 이 시스템의 정책에 없다."""
    account_id = await _seed_account("HELD", cash=100_000)
    held_signal = await _seed_candidate("ALD1", _HELD_DAY, conviction="0.900")
    _ = await _seed_candidate("ALD2", _HELD_DAY, conviction="0.800")
    engine = create_async_engine(DATABASE_URL or "")
    async with engine.begin() as connection:
        order_id = await connection.scalar(
            text(
                """INSERT INTO tb_order(
                    signal_id,account_id,ticker,quantity,entry_price,stop_price,
                    take_profit_price,status,idempotency_key,order_type)
                VALUES (:signal,:account,'ALD1',10,50,42.50,60,'filled',:key,
                    'bracket')
                RETURNING id"""
            ),
            {"signal": held_signal, "account": account_id, "key": "alloc-held-buy"},
        )
        _ = await connection.execute(
            text(
                """INSERT INTO tb_fill(order_id,side,quantity,price,filled_at,
                    broker_fill_id)
                VALUES (:order,'buy',10,50,:at,'alloc-held-fill')"""
            ),
            {"order": order_id, "at": datetime(2026, 7, 17, 15, tzinfo=UTC)},
        )
    await engine.dispose()

    store = PostgresRunStore(DATABASE_URL or "")
    await store.initialize()
    try:
        _ = await _job(store).run(as_of=_HELD_DAY)
    finally:
        await store.close()

    plans = await _read_rows(
        """SELECT ticker, decision, skipped_reason FROM tb_order_plan
           WHERE account_id=:account ORDER BY ticker""",
        account=account_id,
    )
    assert plans == [
        ("ALD1", "skipped", "existing_position"),
        ("ALD2", "planned", None),
    ]
