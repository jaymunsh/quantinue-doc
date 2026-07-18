"""Deterministic offline market-data source used by default."""

from datetime import UTC, datetime
from decimal import Decimal

from quantinue.market_data.models import (
    Candle,
    MacroObservation,
    NewsItem,
    Provenance,
    SecSubmission,
    SecuritySnapshot,
    TickerNewsQuery,
)

_AT = datetime(2026, 7, 10, 20, tzinfo=UTC)


def _provenance(source: str, execution_id: str) -> Provenance:
    return Provenance(
        source=f"fixture:{source}",
        source_ref=f"fixture://{source}/nvda",
        observed_at=_AT,
        captured_at=_AT,
        confidence=1.0,
        execution_id=execution_id,
    )


class FixtureMarketData:
    """Stable no-network implementation of every public adapter seam."""

    async def screener(self, execution_id: str) -> tuple[SecuritySnapshot, ...]:
        """Return the stable NVDA universe row."""
        return (
            SecuritySnapshot(
                ticker="NVDA",
                name="NVIDIA Corporation",
                market_cap=Decimal(3500000000000),
                last_price=Decimal(150),
                volume=42_000_000,
                provenance=_provenance("nasdaq-screener", execution_id),
            ),
        )

    async def candles(self, ticker: str, execution_id: str) -> tuple[Candle, ...]:
        """Return one stable OHLCV candle."""
        return (
            Candle(
                ticker=ticker.upper(),
                opened_at=_AT,
                open=Decimal(149),
                high=Decimal(152),
                low=Decimal(148),
                close=Decimal(151),
                volume=42_000_000,
                provenance=_provenance("market-candles", execution_id),
            ),
        )

    async def macro(self, series: str, execution_id: str) -> tuple[MacroObservation, ...]:
        """Return one stable macro observation."""
        return (
            MacroObservation(
                series=series,
                observed_at=_AT,
                value=Decimal("4.25"),
                provenance=_provenance("macro-feed", execution_id),
            ),
        )

    async def sec_submissions(self, cik: str, execution_id: str) -> tuple[SecSubmission, ...]:
        """Return one stable SEC submission."""
        return (
            SecSubmission(
                cik=cik.zfill(10),
                company_name="NVIDIA CORP",
                accession_number="0001045810-26-000001",
                form="8-K",
                filed_at=_AT,
                primary_document="nvda-8k.htm",
                provenance=_provenance("sec-submissions", execution_id),
            ),
        )

    async def rss(self, execution_id: str) -> tuple[NewsItem, ...]:
        """Return one stable RSS item."""
        return (
            NewsItem(
                title="NVIDIA fixture update",
                snippet="Deterministic offline news snippet.",
                url="fixture://rss/nvda",
                published_at=_AT,
                provenance=_provenance("rss", execution_id),
            ),
        )

    async def ticker_news(self, query: TickerNewsQuery, execution_id: str) -> tuple[NewsItem, ...]:
        """Return the offline item through the ticker-aware contract."""
        del query
        return await self.rss(execution_id)
