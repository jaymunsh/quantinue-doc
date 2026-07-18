"""In-memory app-order exposure state and safe summary calculation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from quantinue.db.contracts import (
    AppOrderExposureStatus,
    AppOrderExposureSummary,
    DailyOrderReservation,
)

if TYPE_CHECKING:
    from collections.abc import ValuesView

ELIGIBLE_APP_ORDER_STATUSES = frozenset(
    {
        AppOrderExposureStatus.PLANNED,
        AppOrderExposureStatus.SUBMITTED,
        AppOrderExposureStatus.FILLED,
    }
)
TERMINAL_APP_ORDER_STATUSES = frozenset(
    {
        AppOrderExposureStatus.FILLED,
        AppOrderExposureStatus.FAILED,
        AppOrderExposureStatus.CANCELED,
    }
)


@dataclass(frozen=True, slots=True)
class AppOrderExposure:
    """One immutable in-memory order identity and its latest canonical status."""

    request: DailyOrderReservation
    status: AppOrderExposureStatus


def app_order_exposure_summary(
    orders: ValuesView[AppOrderExposure], account_id: int, cap: Decimal
) -> AppOrderExposureSummary:
    """Calculate one account's eligible app-owned reference exposure."""
    planned_or_reserved = sum(
        (
            order.request.reference_notional
            for order in orders
            if order.request.account_id == account_id
            and order.status in ELIGIBLE_APP_ORDER_STATUSES
        ),
        Decimal(0),
    )
    return AppOrderExposureSummary(
        account_id=account_id,
        cap=cap,
        planned_or_reserved=planned_or_reserved,
        remaining=max(Decimal(0), cap - planned_or_reserved),
    )
