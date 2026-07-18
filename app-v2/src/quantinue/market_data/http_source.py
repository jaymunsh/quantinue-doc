"""No-key public HTTP market-data adapters."""

import csv
from collections.abc import Awaitable, Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from io import StringIO
from typing import Final, Protocol, cast, runtime_checkable
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

import httpx2
from anyio.to_thread import run_sync
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from quantinue.core.errors import HttpFailureError, TransientFailureError, ValidationFailureError
from quantinue.market_data.models import (
    Candle,
    MacroObservation,
    NewsItem,
    Provenance,
    SecSubmission,
    SecuritySnapshot,
    TickerNewsQuery,
)


@dataclass(frozen=True, slots=True)
class MarketDataEndpoints:
    """Configurable no-key public feed endpoints."""

    screener_url: str
    candles_url: str
    macro_url: str
    sec_url: str
    rss_url: str
    ticker_news_url: str = "https://news.google.com/rss/search"

    @classmethod
    def defaults(cls) -> "MarketDataEndpoints":
        """Return public endpoints that do not require secrets."""
        return cls(
            screener_url="https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=2000",
            candles_url=(
                "https://api.nasdaq.com/api/quote/{ticker}/historical"
                "?assetclass=stocks&fromdate={fromdate}&todate={todate}&limit=5000"
            ),
            macro_url="https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}",
            sec_url="https://data.sec.gov/submissions/CIK{cik}.json",
            rss_url="https://www.sec.gov/news/pressreleases.rss",
        )


class _Boundary(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _NasdaqRow(_Boundary):
    symbol: str
    name: str
    marketCap: Decimal = Decimal(0)  # noqa: N815 - upstream spelling
    lastsale: str = "$0"
    volume: int = 0

    @field_validator("marketCap", "volume", mode="before")
    @classmethod
    def normalize_grouped_number(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.replace(",", "").replace("$", "").strip()
            return normalized if normalized and normalized != "N/A" else 0
        return value

    @field_validator("lastsale", mode="before")
    @classmethod
    def normalize_last_sale(cls, value: object) -> str:
        if not isinstance(value, str) or value.strip() == "N/A":
            return "$0"
        return value


class _NasdaqTable(_Boundary):
    rows: tuple[_NasdaqRow, ...] = ()


class _NasdaqData(_Boundary):
    rows: tuple[_NasdaqRow, ...] = ()
    table: _NasdaqTable | None = None

    @property
    def securities(self) -> tuple[_NasdaqRow, ...]:
        nested_rows = self.table.rows if self.table is not None else ()
        return self.rows or nested_rows


class _NasdaqResponse(_Boundary):
    data: _NasdaqData


class _CandleRow(_Boundary):
    datetime: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class _NasdaqCandleRow(_Boundary):
    date: datetime
    close: Decimal
    volume: int
    open: Decimal
    high: Decimal
    low: Decimal

    @field_validator("close", "open", "high", "low", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> object:
        return value.replace("$", "").replace(",", "").strip() if isinstance(value, str) else value

    @field_validator("volume", mode="before")
    @classmethod
    def normalize_volume(cls, value: object) -> object:
        return value.replace(",", "").strip() if isinstance(value, str) else value

    @field_validator("date", mode="before")
    @classmethod
    def normalize_date(cls, value: object) -> object:
        return _date(value) if isinstance(value, str) else value

    def normalized(self) -> _CandleRow:
        return _CandleRow(
            datetime=self.date.isoformat(),
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
        )


class _NasdaqTradesTable(_Boundary):
    rows: tuple[_NasdaqCandleRow, ...]


class _NasdaqHistoricalData(_Boundary):
    tradesTable: _NasdaqTradesTable  # noqa: N815 - upstream spelling


class _NasdaqStatus(_Boundary):
    rCode: int  # noqa: N815 - upstream spelling


class _NasdaqStatusResponse(_Boundary):
    status: _NasdaqStatus


class _NasdaqHistoricalResponse(_NasdaqStatusResponse):
    data: _NasdaqHistoricalData | None


NASDAQ_SUCCESS_CODE: Final = 200
CANDLE_FIELD: Final = "nasdaq_candles"


class _MacroRow(_Boundary):
    date: datetime
    value: Decimal

    @field_validator("date", mode="before")
    @classmethod
    def normalize_date(cls, value: object) -> object:
        return _date(value) if isinstance(value, str) else value


class _MacroResponse(_Boundary):
    observations: tuple[_MacroRow, ...]


MACRO_FIELD: Final = "fred_macro"
MACRO_LOOKBACK_DAYS: Final = 30
FRED_TIMEOUT_SECONDS: Final = 12.0
FRED_HOST: Final = "fred.stlouisfed.org"
HTTP_ERROR_MIN: Final = 400
FredFetcher = Callable[[str], Awaitable[bytes]]


class _Closable(Protocol):
    def close(self) -> None: ...


@runtime_checkable
class _FredResponse(Protocol):
    status: int

    def read(self) -> bytes: ...

    def close(self) -> None: ...


def _download_fred_csv(url: str) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != FRED_HOST:
        raise ValidationFailureError(MACRO_FIELD, "invalid provider URL")
    request = Request(url, headers={"User-Agent": "quantinue/0.1"})  # noqa: S310
    try:
        opened = cast(
            "_Closable",
            urlopen(request, timeout=FRED_TIMEOUT_SECONDS),  # noqa: S310
        )
        with closing(opened):
            if not isinstance(opened, _FredResponse):
                raise ValidationFailureError(MACRO_FIELD, "invalid provider response")
            response = opened
            if response.status >= HTTP_ERROR_MIN:
                raise HttpFailureError(response.status)
            return response.read()
    except HTTPError as error:
        try:
            raise HttpFailureError(error.code) from error
        finally:
            error.close()
    except (TimeoutError, OSError) as error:
        provider = "fred"
        reason = "transport unavailable"
        raise TransientFailureError(provider, reason) from error


async def fetch_fred_csv(url: str) -> bytes:
    """Fetch one bounded FRED CSV request without blocking the async runtime."""
    return await run_sync(_download_fred_csv, url)


class _SecRecent(_Boundary):
    accessionNumber: tuple[str, ...]  # noqa: N815 - upstream spelling
    filingDate: tuple[str, ...]  # noqa: N815 - upstream spelling
    form: tuple[str, ...]
    primaryDocument: tuple[str, ...]  # noqa: N815 - upstream spelling


class _SecFilings(_Boundary):
    recent: _SecRecent


class _SecResponse(_Boundary):
    cik: str
    name: str
    filings: _SecFilings


class HttpMarketData:
    """Fetch and parse optional public feeds using one owned HTTP client."""

    def __init__(
        self,
        client: httpx2.AsyncClient,
        endpoints: MarketDataEndpoints,
        clock: Callable[[], datetime] | None = None,
        fred_fetcher: FredFetcher | None = None,
    ) -> None:
        """Take ownership of a client until context exit."""
        self._client = client
        self._fred_fetcher = fred_fetcher
        self._endpoints = endpoints
        self._clock = clock or (lambda: datetime.now(UTC))

    async def aclose(self) -> None:
        """Close the owned HTTP client at application shutdown."""
        await self._client.aclose()

    async def _get(self, url: str) -> httpx2.Response:
        response = await self._client.get(url)
        if response.is_error:
            raise HttpFailureError(response.status_code)
        return response

    def _provenance(
        self, source: str, ref: str, observed_at: datetime, execution_id: str
    ) -> Provenance:
        return Provenance(
            source=source,
            source_ref=ref,
            observed_at=observed_at,
            captured_at=self._clock(),
            confidence=0.9,
            execution_id=execution_id,
        )

    async def screener(self, execution_id: str) -> tuple[SecuritySnapshot, ...]:
        """Fetch and parse the NASDAQ universe."""
        response = await self._get(self._endpoints.screener_url)
        payload = _NasdaqResponse.model_validate_json(response.content)
        at = self._clock()
        return tuple(
            SecuritySnapshot(
                ticker=row.symbol.upper(),
                name=row.name,
                market_cap=row.marketCap,
                last_price=Decimal(row.lastsale.removeprefix("$").replace(",", "")),
                volume=row.volume,
                provenance=self._provenance("nasdaq-screener", str(response.url), at, execution_id),
            )
            for row in payload.data.securities
        )

    async def candles(self, ticker: str, execution_id: str) -> tuple[Candle, ...]:
        """Fetch normalized daily candles."""
        to_date = self._clock().date()
        url = self._endpoints.candles_url.format(
            ticker=ticker.upper(),
            fromdate=(to_date - timedelta(days=400)).isoformat(),
            todate=to_date.isoformat(),
        )
        response = await self._get(url)
        rows = _candle_rows(response)
        return tuple(
            Candle(
                ticker=ticker.upper(),
                opened_at=_date(row.datetime),
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
                provenance=self._provenance(
                    "market-candles", str(response.url), _date(row.datetime), execution_id
                ),
            )
            for row in rows
        )

    async def macro(self, series: str, execution_id: str) -> tuple[MacroObservation, ...]:
        """Fetch a public macro series."""
        to_date = self._clock().date()
        url = httpx2.URL(self._endpoints.macro_url.format(series=series)).copy_merge_params(
            {
                "cosd": (to_date - timedelta(days=MACRO_LOOKBACK_DAYS)).isoformat(),
                "coed": to_date.isoformat(),
            }
        )
        if self._fred_fetcher is None:
            response = await self._get(str(url))
            content = response.content
            source_ref = str(response.url)
        else:
            content = await self._fred_fetcher(str(url))
            source_ref = str(url)
        rows = _macro_rows(content)
        return tuple(
            MacroObservation(
                series=series,
                observed_at=row.date,
                value=row.value,
                provenance=self._provenance("macro-feed", source_ref, row.date, execution_id),
            )
            for row in rows
        )

    async def sec_submissions(self, cik: str, execution_id: str) -> tuple[SecSubmission, ...]:
        """Fetch recent SEC submissions for one CIK."""
        response = await self._get(self._endpoints.sec_url.format(cik=cik.zfill(10)))
        payload = _SecResponse.model_validate_json(response.content)
        recent = payload.filings.recent
        rows = zip(
            recent.accessionNumber,
            recent.filingDate,
            recent.form,
            recent.primaryDocument,
            strict=True,
        )
        return tuple(
            SecSubmission(
                cik=payload.cik.zfill(10),
                company_name=payload.name,
                accession_number=accession,
                form=form,
                filed_at=_date(filed),
                primary_document=document,
                provenance=self._provenance(
                    "sec-submissions", str(response.url), _date(filed), execution_id
                ),
            )
            for accession, filed, form, document in rows
        )

    async def rss(self, execution_id: str) -> tuple[NewsItem, ...]:
        """Fetch RSS titles and snippets without crawling articles."""
        response = await self._get(self._endpoints.rss_url)
        root = ET.fromstring(response.content)  # noqa: S314 - trusted configured public feed
        items: list[NewsItem] = []
        for node in root.findall(".//item"):
            published = parsedate_to_datetime(node.findtext("pubDate", default=""))
            url = node.findtext("link", default="")
            items.append(
                NewsItem(
                    title=node.findtext("title", default=""),
                    snippet=node.findtext("description", default=""),
                    url=url,
                    published_at=published,
                    provenance=self._provenance("rss", url, published, execution_id),
                )
            )
        return tuple(items)

    async def ticker_news(self, query: TickerNewsQuery, execution_id: str) -> tuple[NewsItem, ...]:
        """Fetch Google News ticker-search RSS without crawling articles."""
        url = httpx2.URL(self._endpoints.ticker_news_url).copy_merge_params(
            {
                "q": f'({query.ticker.upper()} OR "{query.company_name}")',
                "hl": "en-US",
                "gl": "US",
                "ceid": "US:en",
            }
        )
        response = await self._get(str(url))
        try:
            root = ET.fromstring(response.content)  # noqa: S314 - configured public RSS only
            return tuple(
                NewsItem(
                    title=node.findtext("title", default=""),
                    snippet=node.findtext("description", default=""),
                    url=node.findtext("link", default=""),
                    guid=node.findtext("guid"),
                    published_at=(
                        published := parsedate_to_datetime(node.findtext("pubDate", default=""))
                    ),
                    provenance=self._provenance(
                        "google-news-rss", str(response.url), published, execution_id
                    ),
                )
                for node in root.findall(".//item")
            )
        except (ET.ParseError, TypeError, ValueError) as error:
            field = "ticker-news"
            reason = "malformed RSS payload"
            raise ValidationFailureError(field, reason) from error


def _date(value: str) -> datetime:
    if "/" in value:
        return datetime.strptime(value, "%m/%d/%Y").replace(tzinfo=UTC)
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def _candle_rows(response: httpx2.Response) -> tuple[_CandleRow, ...]:
    try:
        status = _NasdaqStatusResponse.model_validate_json(response.content).status
        if status.rCode != NASDAQ_SUCCESS_CODE:
            reason = "embedded provider failure"
            raise ValidationFailureError(CANDLE_FIELD, reason)
        payload = _NasdaqHistoricalResponse.model_validate_json(response.content)
    except ValidationError as error:
        reason = "malformed provider payload"
        raise ValidationFailureError(CANDLE_FIELD, reason) from error
    if payload.data is None or not payload.data.tradesTable.rows:
        reason = "empty provider payload"
        raise ValidationFailureError(CANDLE_FIELD, reason)
    return tuple(
        sorted(
            (row.normalized() for row in payload.data.tradesTable.rows),
            key=lambda row: _date(row.datetime),
        )
    )


def _macro_rows(content: bytes) -> tuple[_MacroRow, ...]:
    try:
        if content.lstrip().startswith(b"{"):
            rows = _MacroResponse.model_validate_json(content).observations
        else:
            records = csv.DictReader(StringIO(content.decode()))
            date_field = next(
                field
                for field in ("observation_date", "DATE")
                if field in (records.fieldnames or ())
            )
            rows = tuple(
                _MacroRow.model_validate(
                    {"date": row[date_field], "value": tuple(row.values())[-1]}
                )
                for row in records
                if tuple(row.values())[-1] != "."
            )
    except (KeyError, StopIteration, ValidationError, ValueError) as error:
        raise ValidationFailureError(MACRO_FIELD, "malformed provider payload") from error
    if not rows:
        raise ValidationFailureError(MACRO_FIELD, "empty provider payload")
    return rows
