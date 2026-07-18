"""Production composition for delayed-review HTTP processing."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol, assert_never, runtime_checkable

from quantinue.db.reviews import PostgresReviewRepository
from quantinue.market_data.fixture import FixtureMarketData
from quantinue.market_data.http_source import HttpMarketData
from quantinue.market_data.models import MarketData
from quantinue.roles.role_11_reviewer.calendar import SystemClock
from quantinue.roles.role_11_reviewer.processor import ReviewProcessor


@dataclass(frozen=True, slots=True)
class HistoricalCloseUnavailableError(LookupError):
    """The configured market source has no candle for a required session."""

    ticker: str
    session_date: date


@dataclass(frozen=True, slots=True)
class MarketDataCloseProvider:
    """Adapt normalized market candles to role 11 historical closes."""

    market_data: FixtureMarketData | HttpMarketData

    async def close(self, ticker: str, session_date: date) -> Decimal:
        """Return the exact session close or fail with a typed outcome."""
        candles = await self.market_data.candles(
            ticker, f"review:{ticker}:{session_date.isoformat()}"
        )
        for candle in candles:
            if candle.opened_at.date() == session_date:
                return candle.close
        raise HistoricalCloseUnavailableError(ticker, session_date)


@dataclass(frozen=True, slots=True)
class FixtureHistoricalCloseProvider:
    """Deterministic offline closes available for every requested session."""

    async def close(self, ticker: str, session_date: date) -> Decimal:
        """Derive a stable positive close without wall-clock or network access."""
        del ticker
        return Decimal(100 + session_date.toordinal() % 20)


@runtime_checkable
class AsyncCloser(Protocol):
    """Optional owned-resource close capability."""

    async def aclose(self) -> None:
        """Close the owned resource."""
        ...


@dataclass(frozen=True, slots=True)
class ReviewRuntime:
    """Owned application-lifetime review dependencies."""

    repository: PostgresReviewRepository
    processor: ReviewProcessor
    market_data: MarketData

    @classmethod
    def build(
        cls, database_url: str, market_data: FixtureMarketData | HttpMarketData
    ) -> "ReviewRuntime":
        """Compose lazy dependencies without opening resources."""
        repository = PostgresReviewRepository(database_url)
        match market_data:
            case FixtureMarketData():
                prices = FixtureHistoricalCloseProvider()
            case HttpMarketData():
                prices = MarketDataCloseProvider(market_data)
            case unreachable:
                assert_never(unreachable)
        processor = ReviewProcessor(repository, prices, SystemClock())
        return cls(repository, processor, market_data)

    async def initialize(self) -> None:
        """Initialize the review repository."""
        await self.repository.initialize()

    async def close(self) -> None:
        """Close repository and an owned public HTTP source when present."""
        await self.repository.close()
        if isinstance(self.market_data, AsyncCloser):
            await self.market_data.aclose()
