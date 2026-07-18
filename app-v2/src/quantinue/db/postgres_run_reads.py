"""Read-only run-store operations kept separate from claim mutation logic."""

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncEngine

from quantinue.core.contracts import PipelineRun, RunId
from quantinue.db import postgres_read
from quantinue.db.active_snapshot import ActivePipelineSnapshot
from quantinue.db.contracts import (
    AppOrderExposureReservationResult,
    AppOrderExposureStatus,
    AppOrderExposureSummary,
    DailyOrderReservation,
    PersistedAttempt,
)
from quantinue.db.postgres_portfolio import read_simulated_portfolio
from quantinue.db.simulated_portfolio import SimulatedPortfolioSnapshot


class PostgresRunReadMixin(ABC):
    """Provide terminal, active, attempt, and reservation read-boundary operations."""

    @property
    @abstractmethod
    def engine(self) -> AsyncEngine:
        """Return the concrete store's configured database engine."""
        raise NotImplementedError

    @property
    @abstractmethod
    def account_identity(self) -> str:
        """Return the app-owned local account identity used by portfolio reads."""
        raise NotImplementedError

    def _table(self, name: str) -> Table:
        del name
        raise NotImplementedError

    async def get_by_key(self, key: str) -> PipelineRun | None:
        """Return a terminal run, excluding in-progress state."""
        return await postgres_read.get_by_key(self.engine, self._table("pipeline_runs"), key)

    async def list_attempts(self, run_id: RunId) -> tuple[PersistedAttempt, ...]:
        """Return durable attempts in insertion order."""
        return await postgres_read.list_attempts(
            self.engine, self._table("pipeline_stage_attempts"), run_id
        )

    async def list_recent(self, limit: int = 20) -> tuple[PipelineRun, ...]:
        """Return recent terminal runs."""
        return await postgres_read.list_recent(self.engine, self._table("pipeline_runs"), limit)

    async def latest_cycle_ts(self) -> datetime | None:
        """Return the newest cycle timestamp not lost to failure."""
        return await postgres_read.latest_cycle_ts(self.engine, self._table("pipeline_runs"))

    async def list_active(self, limit: int = 20) -> tuple[ActivePipelineSnapshot, ...]:
        """Return current checkpoint snapshots without raw failure messages."""
        return await postgres_read.list_active(
            self.engine,
            self._table("pipeline_runs"),
            self._table("pipeline_stage_attempts"),
            limit,
        )

    async def simulated_portfolio(self, opening_cash: Decimal) -> SimulatedPortfolioSnapshot:
        """Return the durable simulated portfolio read model."""
        return await read_simulated_portfolio(
            self.engine,
            self._table("tb_account").metadata,
            opening_cash,
            self.account_identity,
        )

    async def reserve_daily_new_order(
        self, request: DailyOrderReservation
    ) -> AppOrderExposureReservationResult:
        """Atomically reserve a canonical planned order under both app limits."""
        return await postgres_read.reserve_daily_new_order(
            self.engine,
            self._table("tb_order"),
            self._table("tb_strategist_signals"),
            request,
        )

    async def app_order_exposure_summary(
        self, account_id: int, cap: Decimal
    ) -> AppOrderExposureSummary:
        """Read this account's app-owned eligible planned-order exposure."""
        return await postgres_read.app_order_exposure_summary(
            self.engine,
            self._table("tb_order"),
            account_id,
            cap,
        )

    async def reconcile_app_order_exposure(
        self, idempotency_key: str, status: AppOrderExposureStatus
    ) -> AppOrderExposureSummary | None:
        """Apply one terminal-safe app-order exposure state transition."""
        return await postgres_read.reconcile_app_order_exposure(
            self.engine,
            self._table("tb_order"),
            idempotency_key,
            status,
        )
