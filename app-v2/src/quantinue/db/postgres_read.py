"""Read and order-reservation operations shared by the PostgreSQL run store."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncEngine

from quantinue.core.contracts import PipelineRun, RunId
from quantinue.db.active_snapshot import ActivePipelineSnapshot
from quantinue.db.contracts import (
    AppOrderExposureReservationResult,
    AppOrderExposureStatus,
    AppOrderExposureSummary,
    DailyOrderReservation,
    PersistedAttempt,
)
from quantinue.db.postgres_query import (
    active_run_snapshots,
    latest_useful_cycle_ts,
    persisted_attempts,
    recent_terminal_runs,
    reserve_daily_order,
    terminal_run_by_key,
)
from quantinue.db.postgres_query import (
    app_order_exposure_summary as query_app_order_exposure_summary,
)
from quantinue.db.postgres_query import (
    reconcile_app_order_exposure as query_reconcile_app_order_exposure,
)


async def get_by_key(engine: AsyncEngine, runs: Table, key: str) -> PipelineRun | None:
    """Return a terminal run, excluding in-progress state."""
    return await terminal_run_by_key(engine, runs, key)


async def list_attempts(
    engine: AsyncEngine, attempts: Table, run_id: RunId
) -> tuple[PersistedAttempt, ...]:
    """Return durable attempts in insertion order."""
    return await persisted_attempts(engine, attempts, run_id)


async def list_recent(engine: AsyncEngine, runs: Table, limit: int = 20) -> tuple[PipelineRun, ...]:
    """Return recent terminal runs."""
    return await recent_terminal_runs(engine, runs, limit)


async def latest_cycle_ts(engine: AsyncEngine, runs: Table) -> datetime | None:
    """Return the newest cycle timestamp not lost to failure."""
    return await latest_useful_cycle_ts(engine, runs)


async def list_active(
    engine: AsyncEngine, runs: Table, attempts: Table, limit: int = 20
) -> tuple[ActivePipelineSnapshot, ...]:
    """Return current safe snapshots derived from checkpoint contexts."""
    return await active_run_snapshots(engine, runs, attempts, limit)


async def reserve_daily_new_order(
    engine: AsyncEngine, orders: Table, signals: Table, request: DailyOrderReservation
) -> AppOrderExposureReservationResult:
    """Reserve one app-owned planned order under both durable limits."""
    async with engine.begin() as connection:
        return await reserve_daily_order(
            connection,
            orders,
            signals,
            request,
        )


async def app_order_exposure_summary(
    engine: AsyncEngine, orders: Table, account_id: int, cap: Decimal
) -> AppOrderExposureSummary:
    """Read one account's app-owned eligible planned-order exposure."""
    async with engine.connect() as connection:
        return await query_app_order_exposure_summary(connection, orders, account_id, cap)


async def reconcile_app_order_exposure(
    engine: AsyncEngine,
    orders: Table,
    idempotency_key: str,
    status: AppOrderExposureStatus,
) -> AppOrderExposureSummary | None:
    """Apply one terminal-safe canonical exposure lifecycle update."""
    async with engine.begin() as connection:
        return await query_reconcile_app_order_exposure(
            connection,
            orders,
            idempotency_key,
            status,
        )
