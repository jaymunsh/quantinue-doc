from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal
from uuid import uuid4

import anyio
import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from quantinue.broker.reservations import (
    CompletedClaim,
    InFlightClaim,
    OwnerClaim,
    ReservationClaim,
)
from quantinue.core.config import DatabaseMode, Settings
from quantinue.core.contracts import OrderResult, PipelineContext, PipelineRequest, PipelineRun
from quantinue.db.contracts import (
    AppOrderExposureReservationOutcome,
    AppOrderExposureReservationResult,
    AppOrderExposureStatus,
    DailyOrderReservation,
)
from quantinue.db.domain_records import OrderReconciliation
from quantinue.db.order_reservations import PostgresOrderReservations
from quantinue.db.store import PostgresRunStore
from quantinue.llm.provider import DeterministicAnalyzer
from quantinue.main import create_app
from quantinue.orchestration.factory import build_roles
from quantinue.orchestration.pipeline import PipelineOrchestrator
from quantinue.orchestration.policy import load_pipeline_policy

if TYPE_CHECKING:
    from quantinue.broker.contracts import OrderPlan

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")
_INT_ADAPTER = TypeAdapter(int)


@pytest.mark.skipif(DATABASE_URL is None, reason="real PostgreSQL integration URL not configured")
@pytest.mark.parametrize("ticker", ["../NVDA", "NVDA\x00", "<B>", "삼성"])
def test_postgres_api_returns_422_before_persistence_for_untrusted_ticker(ticker: str) -> None:
    settings = Settings.model_validate(
        {"database_mode": DatabaseMode.POSTGRES, "database_url": DATABASE_URL}
    )
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/runs", json={"ticker": ticker})

    assert response.status_code == 422


def _result(order_id: str) -> OrderResult:
    return OrderResult(
        order_id=order_id,
        client_order_id="pg-reservation",
        status="filled",
        quantity=1,
        filled_avg_price=100.0,
    )


class _CountingRole:
    component: ClassVar[str] = "01"
    name: ClassVar[str] = "counting"

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context: PipelineContext) -> PipelineContext:
        self.calls += 1
        await anyio.sleep(0.01)
        return context.add_stage(self.component, self.name, "done")


class _FirstRole:
    component: ClassVar[str] = "01"
    name: ClassVar[str] = "first"

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context: PipelineContext) -> PipelineContext:
        self.calls += 1
        return replace(context, last_price=100.0).add_stage(self.component, self.name, "done")


class _InterruptRole:
    component: ClassVar[str] = "02"
    name: ClassVar[str] = "interrupt"

    def __init__(self) -> None:
        self.interrupted = False

    async def execute(self, context: PipelineContext) -> PipelineContext:
        if not self.interrupted:
            self.interrupted = True
            raise KeyboardInterrupt
        assert context.last_price == 100.0
        return context.add_stage(self.component, self.name, "resumed")


class _FailingRole:
    component: ClassVar[str] = "01"
    name: ClassVar[str] = "failure"

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context: PipelineContext) -> PipelineContext:
        self.calls += 1
        if self.calls > 1:
            return context.add_stage(self.component, self.name, "resumed")
        message = "persisted fixture failure"
        raise TimeoutError(message)


class _StatusBroker:
    def __init__(self, status: Literal["accepted", "rejected"]) -> None:
        self._status = status

    async def submit(self, plan: OrderPlan) -> OrderResult:
        return OrderResult(
            order_id=f"status-{plan.client_order_id}",
            client_order_id=plan.client_order_id,
            status=self._status,
            quantity=plan.quantity,
            filled_avg_price=0,
        )


@dataclass(frozen=True, slots=True)
class _AppExposureFixture:
    engine: AsyncEngine
    account_id: int
    ticker: str
    first_day: date
    second_day: date
    signal_ids: tuple[int, int, int]


async def _seed_app_exposure_fixture(database_url: str) -> _AppExposureFixture:
    ticker = f"E{uuid4().hex[:8]}".upper()
    first_day = datetime.now(UTC).date()
    second_day = first_day + timedelta(days=1)
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        account_id = _INT_ADAPTER.validate_python(
            await connection.scalar(
                text(
                    """INSERT INTO tb_account(broker_account_id,cash,equity,buying_power)
                    VALUES (:broker,1000,1000,1000) RETURNING id"""
                ),
                {"broker": f"exposure-{uuid4().hex}"},
            )
        )
        signal_ids: list[int] = []
        for offset, trade_day in enumerate((first_day, second_day, second_day), start=1):
            _ = await connection.execute(
                text(
                    """INSERT INTO tb_universe(as_of_date,ticker,company_name,market_cap)
                    VALUES (:day,:ticker,'Exposure Test',1) ON CONFLICT DO NOTHING"""
                ),
                {"day": trade_day, "ticker": ticker},
            )
            _ = await connection.execute(
                text(
                    """INSERT INTO tb_daily_pick(
                    trade_date,ticker,universe_as_of,bucket,rank,sector,score
                    ) VALUES (:day,:ticker,:day,'backfill',1,'test',1) ON CONFLICT DO NOTHING"""
                ),
                {"day": trade_day, "ticker": ticker},
            )
            signal_ids.append(
                _INT_ADAPTER.validate_python(
                    await connection.scalar(
                        text(
                            """INSERT INTO tb_strategist_signals(
                            trade_date,ticker,cycle_ts,inv_type,side,conviction,signal_consensus,
                            summary,evidence,sizing_hint,decision_close,current_price,day_high,
                            day_low,close_prev,volume,turnover,high_52w,low_52w
                            ) VALUES (:day,:ticker,:cycle,'aggressive','buy',0.8,2,'exposure',
                            '{}','{}',100,100,101,99,99,1,100,120,80) RETURNING id"""
                        ),
                        {
                            "day": trade_day,
                            "ticker": ticker,
                            "cycle": datetime.now(UTC) + timedelta(seconds=offset),
                        },
                    )
                )
            )
    return _AppExposureFixture(
        engine=engine,
        account_id=account_id,
        ticker=ticker,
        first_day=first_day,
        second_day=second_day,
        signal_ids=(signal_ids[0], signal_ids[1], signal_ids[2]),
    )


def _app_reservation(
    fixture: _AppExposureFixture, signal_id: int, trade_date: date, entry_price: Decimal
) -> DailyOrderReservation:
    return DailyOrderReservation(
        account_id=fixture.account_id,
        trade_date=trade_date,
        signal_id=signal_id,
        idempotency_key=f"q-a{fixture.account_id}-s{signal_id}",
        ticker=fixture.ticker,
        quantity=1,
        entry_price=entry_price,
        stop_price=entry_price * Decimal("0.85"),
        take_profit_price=entry_price * Decimal("1.20"),
        cap=2,
        max_app_order_exposure_usd=Decimal("1000.00"),
    )


@pytest.mark.anyio
@pytest.mark.skipif(DATABASE_URL is None, reason="disposable PostgreSQL URL not provided")
async def test_postgres_atomic_claim_and_process_recreation_resume() -> None:
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    role = _CountingRole()
    orchestrator = PipelineOrchestrator((role,), store)
    request = PipelineRequest(ticker="PGCON", cycle_ts=datetime.now(UTC))
    results: list[PipelineRun] = []

    async def run_once() -> None:
        results.append(await orchestrator.run(request))

    async with anyio.create_task_group() as group:
        _ = group.start_soon(run_once)
        _ = group.start_soon(run_once)
    assert role.calls == 1
    assert results[0].run_id == results[1].run_id
    await store.close()

    first = _FirstRole()
    interrupted = _InterruptRole()
    resume_request = PipelineRequest(
        ticker="PGRESUME",
        cycle_ts=datetime.now(UTC) + timedelta(seconds=1),
    )
    before_restart = PostgresRunStore(DATABASE_URL)
    await before_restart.initialize()
    with pytest.raises(KeyboardInterrupt):
        _ = await PipelineOrchestrator((first, interrupted), before_restart).run(resume_request)
    await before_restart.close()

    after_restart = PostgresRunStore(DATABASE_URL)
    await after_restart.initialize()
    resumed = await PipelineOrchestrator((first, interrupted), after_restart).run(resume_request)
    assert first.calls == 1
    assert [stage.component for stage in resumed.stages] == ["01", "02"]
    attempts = await after_restart.list_attempts(resumed.run_id)
    assert [(item.component, item.attempt_no, item.status) for item in attempts] == [
        ("01", 1, "completed"),
        ("02", 1, "failed"),
        ("02", 2, "completed"),
    ]

    failure_request = PipelineRequest(
        ticker="PGFAIL",
        cycle_ts=datetime.now(UTC) + timedelta(seconds=2),
    )
    failing_role = _FailingRole()
    resume_policy = load_pipeline_policy(Path("config/pipeline.yaml")).model_copy(
        update={"role_max_retries": 0}
    )
    with pytest.raises(TimeoutError, match="persisted fixture failure"):
        _ = await PipelineOrchestrator((failing_role,), after_restart, policy=resume_policy).run(
            failure_request
        )
    failed = next(run for run in await after_restart.list_recent(100) if run.ticker == "PGFAIL")
    failed_attempts = await after_restart.list_attempts(failed.run_id)
    assert failed_attempts[0].status == "timed_out"
    assert failed_attempts[0].error_code == "ROLE_TIMEOUT"
    failed_run_id = failed.run_id
    resumed_failure = await PipelineOrchestrator(
        (failing_role,), after_restart, policy=resume_policy
    ).run(failure_request)
    assert resumed_failure.run_id == failed_run_id
    assert resumed_failure.status.value == "completed"
    await after_restart.close()


@pytest.mark.anyio
@pytest.mark.skipif(DATABASE_URL is None, reason="disposable PostgreSQL URL not provided")
async def test_postgres_order_reservation_single_owner_and_aba_guard() -> None:
    assert DATABASE_URL is not None
    first = PostgresOrderReservations(DATABASE_URL, stale_after_seconds=60)
    second = PostgresOrderReservations(DATABASE_URL, stale_after_seconds=60)
    await first.initialize()
    await second.initialize()
    claims: list[ReservationClaim] = []
    reservation_id = f"pg-reservation-{uuid4().hex}"

    async def claim(adapter: PostgresOrderReservations) -> None:
        claims.append(await adapter.claim(reservation_id))

    async with anyio.create_task_group() as group:
        _ = group.start_soon(claim, first)
        _ = group.start_soon(claim, second)
    owners = [item for item in claims if isinstance(item, OwnerClaim)]
    assert len(owners) == 1
    assert sum(isinstance(item, InFlightClaim) for item in claims) == 1
    assert await first.complete(reservation_id, owners[0].owner_token, _result("winner"))
    cached = await second.claim(reservation_id)
    assert isinstance(cached, CompletedClaim)
    assert cached.result.order_id == "winner"
    await first.close()
    await second.close()

    stale = PostgresOrderReservations(DATABASE_URL, stale_after_seconds=0)
    await stale.initialize()
    aba_id = f"pg-aba-{uuid4().hex}"
    old = await stale.claim(aba_id)
    new = await stale.claim(aba_id)
    assert isinstance(old, OwnerClaim)
    assert isinstance(new, OwnerClaim)
    assert old.owner_token != new.owner_token
    assert await stale.complete(aba_id, new.owner_token, _result("new"))
    assert not await stale.complete(aba_id, old.owner_token, _result("old"))
    assert not await stale.release(aba_id, old.owner_token)
    await stale.close()


@pytest.mark.anyio
@pytest.mark.skipif(DATABASE_URL is None, reason="disposable PostgreSQL URL not provided")
async def test_postgres_daily_order_cap_is_cross_process_atomic() -> None:
    assert DATABASE_URL is not None
    ticker = f"C{uuid4().hex[:8]}".upper()
    trade_date = datetime.now(UTC).date()
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        _ = await connection.execute(
            text(
                """INSERT INTO tb_universe(as_of_date,ticker,company_name,market_cap)
                VALUES (:day,:ticker,'Cap Test',1)"""
            ),
            {"day": trade_date, "ticker": ticker},
        )
        _ = await connection.execute(
            text(
                """INSERT INTO tb_daily_pick(
                trade_date,ticker,universe_as_of,bucket,rank,sector,score
                ) VALUES (:day,:ticker,:day,'backfill',1,'test',1)"""
            ),
            {"day": trade_date, "ticker": ticker},
        )
        account_id_int = _INT_ADAPTER.validate_python(
            await connection.scalar(
                text(
                    """INSERT INTO tb_account(broker_account_id,cash,equity,buying_power)
                    VALUES (:broker,1000,1000,1000) RETURNING id"""
                ),
                {"broker": f"cap-{uuid4().hex}"},
            )
        )
        signal_ids: list[int] = []
        for offset in (1, 2):
            signal_id_int = _INT_ADAPTER.validate_python(
                await connection.scalar(
                    text(
                        """INSERT INTO tb_strategist_signals(
                        trade_date,ticker,cycle_ts,inv_type,side,conviction,signal_consensus,
                        summary,evidence,sizing_hint,decision_close,current_price,day_high,
                        day_low,close_prev,volume,turnover,high_52w,low_52w
                        ) VALUES (:day,:ticker,:cycle,'aggressive','buy',0.8,2,'cap','{}','{}',
                        100,100,101,99,99,1,100,120,80) RETURNING id"""
                    ),
                    {
                        "day": trade_date,
                        "ticker": ticker,
                        "cycle": datetime.now(UTC) + timedelta(seconds=offset),
                    },
                )
            )
            signal_ids.append(signal_id_int)
    first = PostgresRunStore(DATABASE_URL)
    second = PostgresRunStore(DATABASE_URL)
    await first.initialize()
    await second.initialize()
    outcomes: list[bool] = []

    async def reserve(store: PostgresRunStore, signal_id: int) -> None:
        outcomes.append(
            (
                await store.reserve_daily_new_order(
                    DailyOrderReservation(
                        account_id=account_id_int,
                        trade_date=trade_date,
                        signal_id=signal_id,
                        idempotency_key=f"q-a{account_id_int}-s{signal_id}",
                        ticker=ticker,
                        quantity=1,
                        entry_price=Decimal("100.00"),
                        stop_price=Decimal("85.00"),
                        take_profit_price=Decimal("120.00"),
                        cap=1,
                    )
                )
            ).outcome
            is AppOrderExposureReservationOutcome.ACQUIRED
        )

    async with anyio.create_task_group() as group:
        _ = group.start_soon(reserve, first, signal_ids[0])
        _ = group.start_soon(reserve, second, signal_ids[1])
    assert sorted(outcomes) == [False, True]
    await first.close()
    await second.close()
    await engine.dispose()


@pytest.mark.anyio
@pytest.mark.skipif(DATABASE_URL is None, reason="disposable PostgreSQL URL not provided")
async def test_postgres_app_exposure_is_account_wide_across_dates_and_terminal_safe() -> None:
    # Given: one account has independent eligible signals on two trade dates.
    assert DATABASE_URL is not None
    fixture = await _seed_app_exposure_fixture(DATABASE_URL)
    first = PostgresRunStore(DATABASE_URL)
    second = PostgresRunStore(DATABASE_URL)
    await first.initialize()
    await second.initialize()

    outcomes: list[tuple[DailyOrderReservation, AppOrderExposureReservationResult]] = []

    async def reserve(store: PostgresRunStore, request: DailyOrderReservation) -> None:
        outcomes.append((request, await store.reserve_daily_new_order(request)))

    # When: two separate stores race on distinct dates for 600 USD each.
    first_request = _app_reservation(
        fixture, fixture.signal_ids[0], fixture.first_day, Decimal("600.00")
    )
    second_request = _app_reservation(
        fixture, fixture.signal_ids[1], fixture.second_day, Decimal("600.00")
    )
    async with anyio.create_task_group() as group:
        _ = group.start_soon(reserve, first, first_request)
        _ = group.start_soon(reserve, second, second_request)

    # Then: one reservation wins; replay is free and the remaining boundary is exact.
    assert sorted(item[1].outcome for item in outcomes) == [
        AppOrderExposureReservationOutcome.ACQUIRED,
        AppOrderExposureReservationOutcome.REJECTED,
    ]
    winner = next(
        request
        for request, outcome in outcomes
        if outcome.outcome is AppOrderExposureReservationOutcome.ACQUIRED
    )
    replay = await first.reserve_daily_new_order(winner)
    assert replay.outcome is AppOrderExposureReservationOutcome.REPLAYED
    assert replay.summary.planned_or_reserved == Decimal("600.00")
    collision = await first.reserve_daily_new_order(
        DailyOrderReservation(
            account_id=winner.account_id,
            trade_date=winner.trade_date,
            signal_id=winner.signal_id,
            idempotency_key=winner.idempotency_key,
            ticker=winner.ticker,
            quantity=winner.quantity,
            entry_price=Decimal("500.00"),
            stop_price=Decimal("425.00"),
            take_profit_price=Decimal("600.00"),
            cap=winner.cap,
            max_app_order_exposure_usd=winner.max_app_order_exposure_usd,
        )
    )
    assert collision.outcome is AppOrderExposureReservationOutcome.REJECTED
    assert collision.summary.planned_or_reserved == Decimal("600.00")
    final_cent = await first.reserve_daily_new_order(
        _app_reservation(fixture, fixture.signal_ids[2], fixture.second_day, Decimal("400.00"))
    )
    assert final_cent.outcome is AppOrderExposureReservationOutcome.ACQUIRED
    assert final_cent.summary.planned_or_reserved == Decimal("1000.00")

    submitted_id = await first.domain.reconcile_order(
        OrderReconciliation(
            idempotency_key=winner.idempotency_key,
            status="submitted",
            broker_order_id=f"submitted-{uuid4().hex}",
        )
    )
    failed_id = await first.domain.reconcile_order(
        OrderReconciliation(
            idempotency_key=f"q-a{fixture.account_id}-s{fixture.signal_ids[2]}",
            status="failed",
            broker_order_id=f"failed-{uuid4().hex}",
        )
    )
    assert submitted_id > 0
    assert failed_id > 0

    filled = await first.reconcile_app_order_exposure(
        winner.idempotency_key, AppOrderExposureStatus.FILLED
    )
    stale_failure = await first.reconcile_app_order_exposure(
        winner.idempotency_key, AppOrderExposureStatus.FAILED
    )
    assert filled is None
    assert stale_failure is None
    terminal_summary = await first.app_order_exposure_summary(
        fixture.account_id, Decimal("1000.00")
    )
    assert terminal_summary.planned_or_reserved == Decimal("600.00")

    released = await first.reconcile_app_order_exposure(
        f"q-a{fixture.account_id}-s{fixture.signal_ids[2]}", AppOrderExposureStatus.CANCELED
    )
    assert released is None
    released_summary = await first.app_order_exposure_summary(
        fixture.account_id, Decimal("1000.00")
    )
    assert released_summary.planned_or_reserved == Decimal("600.00")

    await first.close()
    await second.close()
    await fixture.engine.dispose()


@pytest.mark.anyio
@pytest.mark.skipif(DATABASE_URL is None, reason="disposable PostgreSQL URL not provided")
async def test_postgres_domain_reconciliation_keeps_broker_ids_after_role10_status_update() -> None:
    # Given: Role 09 has reserved a durable order for a non-special account identity.
    assert DATABASE_URL is not None
    fixture = await _seed_app_exposure_fixture(DATABASE_URL)
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    request = _app_reservation(
        fixture,
        fixture.signal_ids[0],
        fixture.first_day,
        Decimal("100.00"),
    )
    reserved = await store.reserve_daily_new_order(request)
    assert reserved.outcome is AppOrderExposureReservationOutcome.ACQUIRED

    # When: Role 10 updates exposure state before the domain lifecycle persists broker fields.
    _ = await store.reconcile_app_order_exposure(
        request.idempotency_key,
        AppOrderExposureStatus.FILLED,
    )
    broker_order_id = f"broker-{uuid4().hex}"
    parent_order_id = f"parent-{uuid4().hex}"
    stop_leg_order_id = f"stop-{uuid4().hex}"
    take_profit_leg_order_id = f"take-profit-{uuid4().hex}"
    _ = await store.domain.reconcile_order(
        OrderReconciliation(
            idempotency_key=request.idempotency_key,
            status="filled",
            broker_order_id=broker_order_id,
            parent_order_id=parent_order_id,
            stop_leg_order_id=stop_leg_order_id,
            take_profit_leg_order_id=take_profit_leg_order_id,
        )
    )

    # Then: the canonical terminal order retains every broker-provided identifier.
    async with fixture.engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        """SELECT broker_order_id, parent_order_id, stop_leg_order_id,
                    take_profit_leg_order_id FROM tb_order WHERE idempotency_key=:key"""
                    ),
                    {"key": request.idempotency_key},
                )
            )
            .mappings()
            .one()
        )
    assert row["broker_order_id"] == broker_order_id
    assert row["parent_order_id"] == parent_order_id
    assert row["stop_leg_order_id"] == stop_leg_order_id
    assert row["take_profit_leg_order_id"] == take_profit_leg_order_id

    await store.close()
    await fixture.engine.dispose()


@pytest.mark.anyio
@pytest.mark.skipif(DATABASE_URL is None, reason="disposable PostgreSQL URL not provided")
@pytest.mark.parametrize(
    ("provider_status", "canonical_status", "cycle_ts"),
    [
        ("accepted", "submitted", datetime(2035, 1, 2, 13, 0, tzinfo=UTC)),
        ("rejected", "failed", datetime(2035, 1, 3, 13, 0, tzinfo=UTC)),
    ],
)
async def test_postgres_pipeline_persists_canonical_provider_status_once(
    provider_status: Literal["accepted", "rejected"],
    canonical_status: Literal["submitted", "failed"],
    cycle_ts: datetime,
) -> None:
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    broker = _StatusBroker(provider_status)
    orchestrator = PipelineOrchestrator(
        build_roles(DeterministicAnalyzer(), broker, store=store),
        store,
    )
    run = await orchestrator.run(PipelineRequest(ticker="NVDA", cycle_ts=cycle_ts))

    assert run.order is not None
    assert run.order.status == canonical_status
    await store.close()
