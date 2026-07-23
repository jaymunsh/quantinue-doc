"""Shared proposal, critic, and soft-exit path for intraday rejudgement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from quantinue.core.market_calendar import NEW_YORK

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date, datetime
    from decimal import Decimal

    from quantinue.orchestration.work_lease import WorkLease
    from quantinue.roles.analysis.job import AnalysisJob
    from quantinue.roles.exits import ExitDecision


class IntradaySellDomain(Protocol):
    """Ledger reads used after refreshed judgements are persisted."""

    async def approved_sell_profiles(
        self, as_of: date, tickers: tuple[str, ...]
    ) -> Mapping[str, frozenset[str]]:
        """Return personas whose sell survived the critic."""
        ...


class SoftSellExecutor(Protocol):
    """Durable execution seam for critic-approved intraday sells."""

    async def run_soft_sells(
        self,
        *,
        as_of: date,
        prices: Mapping[str, Decimal],
        profiles: Mapping[str, frozenset[str]],
    ) -> tuple[ExitDecision, ...]:
        """Close the matching persona holdings and return durable decisions."""
        ...


class IntradayBuyExecutor(Protocol):
    """Existing allocation contract exposed at an intraday timestamp."""

    async def run_intraday(
        self, *, now: datetime, prices: Mapping[str, Decimal]
    ) -> str:
        """Size and execute the newest approved buys, idempotently."""
        ...


class IntradayPartialFailureError(RuntimeError):
    """Raised when any persona leaves ticker work incomplete."""


@dataclass(frozen=True, slots=True)
class IntradayRejudgeEngine:
    """Run both investment personas, then execute approved sell reversals."""

    domain: IntradaySellDomain
    jobs: tuple[AnalysisJob, ...]
    exits: SoftSellExecutor
    allocation: IntradayBuyExecutor | None = None

    async def run(
        self,
        *,
        now: datetime,
        prices: Mapping[str, Decimal],
        lease: WorkLease | None = None,
    ) -> int:
        """Refresh triggered tickers and close approved reversals in one tick."""
        mutable_prices = dict(prices)
        skipped = 0
        for job in self.jobs:
            skipped += (
                await job.run_intraday(now=now, prices=mutable_prices, lease=lease)
            ).skipped
        if skipped:
            message = f"intraday rejudgement incomplete: skipped={skipped}"
            raise IntradayPartialFailureError(message)
        as_of = now.astimezone(NEW_YORK).date()
        profiles = await self.domain.approved_sell_profiles(
            as_of, tuple(mutable_prices)
        )
        if lease is not None:
            await lease.renew()
        closed = await self.exits.run_soft_sells(
            as_of=as_of, prices=mutable_prices, profiles=profiles
        )
        if self.allocation is not None:
            if lease is not None:
                await lease.renew()
            _ = await self.allocation.run_intraday(now=now, prices=mutable_prices)
        return len(closed)
