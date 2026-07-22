"""Shared proposal, critic, and soft-exit path for intraday rejudgement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from quantinue.core.market_calendar import NEW_YORK

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date, datetime
    from decimal import Decimal

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


@dataclass(frozen=True, slots=True)
class IntradayRejudgeEngine:
    """Run both investment personas, then execute approved sell reversals."""

    domain: IntradaySellDomain
    jobs: tuple[AnalysisJob, ...]
    exits: SoftSellExecutor

    async def run(self, *, now: datetime, prices: Mapping[str, Decimal]) -> int:
        """Refresh triggered tickers and close approved reversals in one tick."""
        mutable_prices = dict(prices)
        for job in self.jobs:
            _ = await job.run_intraday(now=now, prices=mutable_prices)
        as_of = now.astimezone(NEW_YORK).date()
        profiles = await self.domain.approved_sell_profiles(
            as_of, tuple(mutable_prices)
        )
        closed = await self.exits.run_soft_sells(
            as_of=as_of, prices=mutable_prices, profiles=profiles
        )
        return len(closed)
