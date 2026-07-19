"""Ticker-to-CIK resolution against the SEC company_tickers feed."""

import httpx2
import pytest

from quantinue.market_data.http_source import HttpMarketData, MarketDataEndpoints

TICKER_MAP = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
}


def _wire(counter: list[str]) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        counter.append(str(request.url))
        return httpx2.Response(200, json=TICKER_MAP, request=request)

    return httpx2.MockTransport(handler)


def _source(counter: list[str]) -> HttpMarketData:
    return HttpMarketData(
        httpx2.AsyncClient(transport=_wire(counter)),
        MarketDataEndpoints.defaults(),
    )


@pytest.mark.anyio
async def test_ticker_resolves_to_a_zero_padded_cik() -> None:
    calls: list[str] = []

    cik = await _source(calls).sec_cik_for_ticker("AAPL", "run-1")

    assert cik == "0000320193"


@pytest.mark.anyio
async def test_lookup_is_case_insensitive() -> None:
    calls: list[str] = []

    assert await _source(calls).sec_cik_for_ticker("nvda", "run-1") == "0001045810"


@pytest.mark.anyio
async def test_unlisted_ticker_resolves_to_none() -> None:
    calls: list[str] = []

    assert await _source(calls).sec_cik_for_ticker("ZZZZ", "run-1") is None


@pytest.mark.anyio
async def test_ticker_map_is_fetched_once_and_reused() -> None:
    # Given: the map covers every listed company, so refetching it per ticker
    # would multiply a multi-megabyte download across the deep-analysis fan-out.
    calls: list[str] = []
    source = _source(calls)

    await source.sec_cik_for_ticker("AAPL", "run-1")
    await source.sec_cik_for_ticker("NVDA", "run-1")
    await source.sec_cik_for_ticker("ZZZZ", "run-1")

    assert len(calls) == 1
