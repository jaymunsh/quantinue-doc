"""Typed market-data values and adapter contract."""

from decimal import Decimal
from enum import StrEnum, unique
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from quantinue.core.schemas import AwareDateTime


class BoundaryModel(BaseModel):
    """Immutable value parsed at an external-data boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class Provenance(BoundaryModel):
    """Origin, timing, confidence, and execution lineage."""

    source: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    observed_at: AwareDateTime
    captured_at: AwareDateTime
    confidence: float = Field(ge=0, le=1)
    execution_id: str = Field(min_length=1)


class SecuritySnapshot(BoundaryModel):
    """One equity row returned by the universe screener."""

    ticker: str = Field(pattern=r"^[A-Z0-9.-]{1,12}$")
    name: str = Field(min_length=1)
    market_cap: Decimal = Field(ge=0)
    last_price: Decimal = Field(ge=0)
    volume: int = Field(ge=0)
    provenance: Provenance


class Candle(BoundaryModel):
    """One normalized OHLCV observation."""

    ticker: str = Field(min_length=1, max_length=12)
    opened_at: AwareDateTime
    open: Decimal = Field(ge=0)
    high: Decimal = Field(ge=0)
    low: Decimal = Field(ge=0)
    close: Decimal = Field(ge=0)
    volume: int = Field(ge=0)
    provenance: Provenance


class MacroObservation(BoundaryModel):
    """One named macroeconomic series observation."""

    series: str = Field(min_length=1)
    observed_at: AwareDateTime
    value: Decimal
    provenance: Provenance


class SecSubmission(BoundaryModel):
    """One recent SEC filing from the submissions feed."""

    cik: str = Field(pattern=r"^\d{10}$")
    company_name: str = Field(min_length=1)
    accession_number: str = Field(min_length=1)
    form: str = Field(min_length=1)
    filed_at: AwareDateTime
    primary_document: str = Field(min_length=1)
    provenance: Provenance


class NewsItem(BoundaryModel):
    """RSS-safe title, snippet, and link without article crawling."""

    title: str = Field(min_length=1)
    snippet: str
    url: str = Field(min_length=1)
    guid: str | None = None
    published_at: AwareDateTime
    provenance: Provenance


@unique
class NewsMatchStatus(StrEnum):
    """Closed lifecycle status for one fetched news item."""

    FETCHED = "fetched"
    RELEVANT = "relevant"
    EXCLUDED = "excluded"
    SELECTED = "selected"


@unique
class NewsMatchReason(StrEnum):
    """Auditable deterministic reason contributing to selection state."""

    TICKER_TITLE = "ticker_title"
    TICKER_SNIPPET = "ticker_snippet"
    COMPANY_TITLE = "company_title"
    COMPANY_SNIPPET = "company_snippet"
    DUPLICATE = "duplicate"
    BELOW_MINIMUM_SCORE = "below_minimum_score"


class TickerNewsQuery(BoundaryModel):
    """Canonical company identity used to request ticker news."""

    ticker: str = Field(min_length=1, max_length=12)
    company_name: str = Field(min_length=1)


@runtime_checkable
class TickerNewsMarketData(Protocol):
    """Ticker-aware news transport implemented by public market data."""

    async def ticker_news(self, query: TickerNewsQuery, execution_id: str) -> tuple[NewsItem, ...]:
        """Return fetched ticker-search RSS items without selecting a representative."""
        ...


@runtime_checkable
class SecIdentityMarketData(Protocol):
    """Ticker-to-CIK lookup implemented by complete market-data adapters."""

    async def cik_for_ticker(self, ticker: str, execution_id: str) -> str:
        """Return one ten-digit SEC CIK for a canonical ticker."""
        ...


class MarketData(Protocol):
    """Common contract implemented by fixture and public sources."""

    async def screener(self, execution_id: str) -> tuple[SecuritySnapshot, ...]:  # noqa: D102
        ...

    async def candles(self, ticker: str, execution_id: str) -> tuple[Candle, ...]:  # noqa: D102
        ...

    async def macro(self, series: str, execution_id: str) -> tuple[MacroObservation, ...]:  # noqa: D102
        ...

    async def sec_submissions(self, cik: str, execution_id: str) -> tuple[SecSubmission, ...]:  # noqa: D102
        ...

    async def rss(self, execution_id: str) -> tuple[NewsItem, ...]:  # noqa: D102
        ...
