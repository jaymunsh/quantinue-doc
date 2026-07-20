"""PostgreSQL repository using canonical operational tables.

Phase 5까지는 런 생명주기(advisory lock claim·stage checkpoint·terminal
publish)의 소유자이기도 했다 — 그 절반은 구 11단계 러너와 함께 죽었고, 남은
것은 잡과 웹이 실제로 부르는 표면이다: 계좌 부트스트랩 · 모의 포트폴리오
읽기 · 일일 주문 예약(노출 게이트) · ``.domain``(도메인 저장소) ·
``.order_reservations``(브로커 멱등 축).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Final

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from quantinue.db import postgres_query
from quantinue.db.domain import PostgresDomainRepository
from quantinue.db.order_reservations import PostgresOrderReservations

if TYPE_CHECKING:
    from sqlalchemy import Table

    from quantinue.db.contracts import (
        AppOrderExposureReservationResult,
        AppOrderExposureStatus,
        AppOrderExposureSummary,
        DailyOrderReservation,
    )
    from quantinue.db.domain_records import CompletedFillWrite

_METADATA = MetaData()
_DEFAULT_OPENING_CASH: Final = Decimal("1000000.00")

# 살아 있는 읽기·예약 경로가 반사하는 테이블만 남았다. pipeline_runs 계열은
# 구 러너의 것이라 여기서 빠졌다 — 테이블 자체는 역사(그리고 리뷰의 레거시
# 조인)를 위해 DB에 남지만, 이 스토어는 더 이상 그것을 모른다.
_TABLES: Final = (
    "tb_order",
    "tb_account",
    "tb_fill",
    "tb_strategist_signals",
    "tb_daily_bar",
)


class PostgresRunStore:
    """Durable store for the simulated account, order budget, and domain ledger."""

    def __init__(
        self,
        database_url: str,
    ) -> None:
        """Create a tuned async engine without opening a connection."""
        self._engine: AsyncEngine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
        self.order_reservations = PostgresOrderReservations(database_url)
        self.domain = PostgresDomainRepository(database_url)

    async def initialize(self) -> None:
        """Reflect canonical tables created by schema bootstrap."""
        async with self._engine.begin() as connection:
            await connection.run_sync(_METADATA.reflect, only=_TABLES)
        await self.order_reservations.initialize()
        await self.domain.initialize()

    async def close(self) -> None:
        """Dispose every connection pool this store owns."""
        await self.order_reservations.close()
        await self.domain.close()
        await self._engine.dispose()

    @property
    def engine(self) -> AsyncEngine:
        """Return the engine used by safe read-boundary operations."""
        return self._engine

    async def record_completed_fill(self, value: CompletedFillWrite) -> int:
        """Apply the shared completed-fill contract through atomic accounting."""
        return await self.domain.record_completed_fill(value)

    async def reserve_daily_new_order(
        self, request: DailyOrderReservation
    ) -> AppOrderExposureReservationResult:
        """Atomically reserve a canonical planned order under both app limits."""
        async with self._engine.begin() as connection:
            return await postgres_query.reserve_daily_order(
                connection,
                self._table("tb_order"),
                self._table("tb_strategist_signals"),
                request,
            )

    async def app_order_exposure_summary(
        self, account_id: int, cap: Decimal
    ) -> AppOrderExposureSummary:
        """Read this account's app-owned eligible planned-order exposure."""
        async with self._engine.begin() as connection:
            return await postgres_query.app_order_exposure_summary(
                connection,
                self._table("tb_order"),
                account_id,
                cap,
            )

    async def reconcile_app_order_exposure(
        self, idempotency_key: str, status: AppOrderExposureStatus
    ) -> AppOrderExposureSummary | None:
        """Apply one terminal-safe app-order exposure state transition."""
        async with self._engine.begin() as connection:
            return await postgres_query.reconcile_app_order_exposure(
                connection,
                self._table("tb_order"),
                idempotency_key,
                status,
            )

    def _table(self, name: str) -> Table:
        return _METADATA.tables[name]
