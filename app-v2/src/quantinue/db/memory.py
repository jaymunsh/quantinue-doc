"""Behavioral in-memory fake for the durable store.

런 생명주기(claim·attempt·checkpoint)의 페이크이기도 했지만 그 절반은 구
러너와 함께 죽었다. 남은 것은 잡·웹이 실제로 쓰는 표면의 페이크다 — 모의
포트폴리오·완료 체결·일일 주문 예약·노출 게이트.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from typing_extensions import override

from quantinue.db.contracts import (
    AppOrderExposureReservationOutcome,
    AppOrderExposureReservationResult,
    AppOrderExposureStatus,
    AppOrderExposureSummary,
    DailyOrderReservation,
)
from quantinue.db.memory_completed_fill import MemoryCompletedFillMixin

if TYPE_CHECKING:
    from datetime import date
from quantinue.db.memory_exposure import (
    TERMINAL_APP_ORDER_STATUSES,
    AppOrderExposure,
    app_order_exposure_summary,
)
from quantinue.db.simulated_portfolio import (
    SimulatedFill,
    SimulatedOrder,
    ensure_fill_is_affordable,
)

_DEFAULT_OPENING_CASH: Final = Decimal("1000000.00")


class InMemoryRunStore(MemoryCompletedFillMixin):
    """Mutable process-local fake with atomic claim and checkpoint semantics."""

    def __init__(self, opening_cash: Decimal = _DEFAULT_OPENING_CASH) -> None:
        """Create empty atomic fake state."""
        super().__init__()
        self._daily_orders: dict[tuple[int, date], set[str]] = {}
        self._simulated_orders: dict[str, SimulatedOrder] = {}
        self._opening_cash = opening_cash

    async def initialize(self) -> None:
        """No initialization is required."""

    async def close(self) -> None:
        """No external resources are owned."""

    @override
    async def record_simulated_order(
        self,
        order: SimulatedOrder,
        fill: SimulatedFill | None,
    ) -> None:
        """Atomically retain one local order and its optional unique fill."""
        async with self._lock:
            if fill is not None and fill.fill_id not in self._simulated_fills:
                ensure_fill_is_affordable(
                    self._opening_cash, tuple(self._simulated_fills.values()), fill
                )
            _ = self._simulated_orders.setdefault(order.order_id, order)
            if fill is not None:
                _ = self._simulated_fills.setdefault(fill.fill_id, fill)

    async def reserve_daily_new_order(
        self, request: DailyOrderReservation
    ) -> AppOrderExposureReservationResult:
        """Reserve one canonical identity under the daily and app-exposure caps."""
        async with self._lock:
            existing = self._app_order_exposures.get(request.idempotency_key)
            if existing is not None:
                if existing.request != request:
                    return AppOrderExposureReservationResult(
                        outcome=AppOrderExposureReservationOutcome.REJECTED,
                        summary=app_order_exposure_summary(
                            self._app_order_exposures.values(),
                            request.account_id,
                            request.max_app_order_exposure_usd,
                        ),
                    )
                return AppOrderExposureReservationResult(
                    outcome=AppOrderExposureReservationOutcome.REPLAYED,
                    summary=app_order_exposure_summary(
                        self._app_order_exposures.values(),
                        existing.request.account_id,
                        request.max_app_order_exposure_usd,
                    ),
                )
            identities = self._daily_orders.setdefault(
                (request.account_id, request.trade_date), set()
            )
            if len(identities) >= request.cap:
                return AppOrderExposureReservationResult(
                    outcome=AppOrderExposureReservationOutcome.REJECTED,
                    summary=app_order_exposure_summary(
                        self._app_order_exposures.values(),
                        request.account_id,
                        request.max_app_order_exposure_usd,
                    ),
                )
            summary = app_order_exposure_summary(
                self._app_order_exposures.values(),
                request.account_id,
                request.max_app_order_exposure_usd,
            )
            if summary.planned_or_reserved + request.reference_notional > summary.cap:
                return AppOrderExposureReservationResult(
                    outcome=AppOrderExposureReservationOutcome.REJECTED,
                    summary=summary,
                )
            identities.add(request.idempotency_key)
            self._app_order_exposures[request.idempotency_key] = AppOrderExposure(
                request=request,
                status=AppOrderExposureStatus.PLANNED,
            )
            return AppOrderExposureReservationResult(
                outcome=AppOrderExposureReservationOutcome.ACQUIRED,
                summary=app_order_exposure_summary(
                    self._app_order_exposures.values(),
                    request.account_id,
                    request.max_app_order_exposure_usd,
                ),
            )

    async def app_order_exposure_summary(
        self, account_id: int, cap: Decimal
    ) -> AppOrderExposureSummary:
        """Return app-owned eligible reference exposure for one account."""
        async with self._lock:
            return app_order_exposure_summary(self._app_order_exposures.values(), account_id, cap)

    async def reconcile_app_order_exposure(
        self, idempotency_key: str, status: AppOrderExposureStatus
    ) -> AppOrderExposureSummary | None:
        """Replace one canonical order's lifecycle state without adding exposure."""
        async with self._lock:
            existing = self._app_order_exposures.get(idempotency_key)
            if existing is None:
                return None
            updated = (
                existing
                if existing.status in TERMINAL_APP_ORDER_STATUSES
                else replace(existing, status=status)
            )
            self._app_order_exposures[idempotency_key] = updated
            return app_order_exposure_summary(
                self._app_order_exposures.values(),
                updated.request.account_id,
                updated.request.max_app_order_exposure_usd,
            )
