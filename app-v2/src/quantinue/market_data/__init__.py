"""Optional public market feeds with deterministic offline defaults."""

from quantinue.market_data.fixture import FixtureMarketData
from quantinue.market_data.http_client import (
    HTTP_CLIENT_POLICY,
    HttpClientPolicy,
    build_http_client,
    public_http_client,
)
from quantinue.market_data.http_source import (
    HttpMarketData,
    MarketDataEndpoints,
    fetch_fred_csv,
)
from quantinue.market_data.models import (
    Candle,
    MacroObservation,
    MarketData,
    NewsItem,
    Provenance,
    SecSubmission,
    SecuritySnapshot,
    TickerNewsQuery,
)

__all__ = [
    "HTTP_CLIENT_POLICY",
    "Candle",
    "FixtureMarketData",
    "HttpClientPolicy",
    "HttpMarketData",
    "MacroObservation",
    "MarketData",
    "MarketDataEndpoints",
    "NewsItem",
    "Provenance",
    "SecSubmission",
    "SecuritySnapshot",
    "TickerNewsQuery",
    "build_http_client",
    "fetch_fred_csv",
    "public_http_client",
]
