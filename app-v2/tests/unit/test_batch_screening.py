from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from anyio.lowlevel import checkpoint

from quantinue.core.contracts import PipelineContext, PipelineRequest
from quantinue.core.errors import ValidationFailureError
from quantinue.market_data.models import (
    Candle,
    MacroObservation,
    NewsItem,
    Provenance,
    SecSubmission,
    SecuritySnapshot,
)
from quantinue.roles.role_01_universe_screener.service import UniverseScreener
from quantinue.roles.role_02_technical_analysis.service import TechnicalAnalysis
from quantinue.roles.role_03_daily_screener.service import DailyScreener

NOW = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)


@dataclass(slots=True)
class _BatchMarketData:
    tickers: tuple[str, ...]
    failed: frozenset[str] = frozenset()
    active: int = 0
    peak: int = 0
    requested: list[str] = field(default_factory=list)

    async def screener(self, execution_id: str) -> tuple[SecuritySnapshot, ...]:
        return tuple(
            SecuritySnapshot(
                ticker=ticker,
                name=f"Company {ticker}",
                market_cap=Decimal(1_000_000 - rank),
                last_price=Decimal(100),
                volume=10_000,
                provenance=_provenance(ticker, execution_id),
            )
            for rank, ticker in enumerate(self.tickers)
        )

    async def candles(self, ticker: str, execution_id: str) -> tuple[Candle, ...]:
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.requested.append(ticker)
        await checkpoint()
        self.active -= 1
        if ticker in self.failed:
            field_name = "candles"
            raise ValidationFailureError(field_name, f"{ticker} unavailable")
        offset = self.tickers.index(ticker) + 1
        return tuple(
            Candle(
                ticker=ticker,
                opened_at=NOW - timedelta(days=59 - day),
                open=Decimal(80 + offset) + Decimal(day) / 10,
                high=Decimal(82 + offset) + Decimal(day) / 10,
                low=Decimal(79 + offset) + Decimal(day) / 10,
                close=Decimal(81 + offset) + Decimal(day * offset) / 100,
                volume=10_000 + day * offset,
                provenance=_provenance(ticker, execution_id),
            )
            for day in range(60)
        )

    async def macro(self, series: str, execution_id: str) -> tuple[MacroObservation, ...]:
        del series, execution_id
        return ()

    async def sec_submissions(self, cik: str, execution_id: str) -> tuple[SecSubmission, ...]:
        del cik, execution_id
        return ()

    async def rss(self, execution_id: str) -> tuple[NewsItem, ...]:
        del execution_id
        return ()


def _provenance(ticker: str, execution_id: str) -> Provenance:
    return Provenance(
        source="batch-test",
        source_ref=f"https://example.test/{ticker}",
        observed_at=NOW,
        captured_at=NOW,
        confidence=0.9,
        execution_id=execution_id,
    )


def _tickers(count: int = 50) -> tuple[str, ...]:
    return (*tuple(f"T{rank:03d}" for rank in range(count - 1)), "NVDA")


async def _screen(market: _BatchMarketData) -> PipelineContext:
    context = PipelineContext(request=PipelineRequest(ticker="NVDA", cycle_ts=NOW))
    context = await UniverseScreener(market).execute(context)
    return await TechnicalAnalysis(market).execute(context)


@pytest.mark.anyio
async def test_technical_analysis_is_bounded_stable_and_uses_real_candles() -> None:
    # Given
    market = _BatchMarketData(_tickers())

    # When
    result = await _screen(market)

    # Then
    assert result.technical_output is not None
    snapshots = result.technical_output.snapshots
    assert market.peak <= 5
    assert tuple(item.ticker for item in snapshots) == _tickers(20)
    assert len(snapshots) == len(result.to_run().detail.roles[1].items) == 20
    assert market.requested == list(_tickers(20))
    assert snapshots[0].ret_20d != snapshots[1].ret_20d
    assert snapshots[0].ma20 != snapshots[1].ma20


@pytest.mark.anyio
async def test_technical_analysis_records_partial_failures_but_requires_ten_successes() -> None:
    # Given
    market = _BatchMarketData(_tickers(), frozenset({"T003", "T017"}))

    # When
    result = await _screen(market)

    # Then
    assert result.technical_output is not None
    assert len(result.technical_output.snapshots) == 20
    assert result.technical_output.excluded_insufficient_history == ("T003", "T017")


@pytest.mark.anyio
async def test_technical_analysis_rejects_requested_ticker_failure() -> None:
    # Given
    market = _BatchMarketData(_tickers(), frozenset({"NVDA"}))

    # When / Then
    with pytest.raises(ValidationFailureError, match="requested ticker NVDA"):
        _ = await _screen(market)


@pytest.mark.anyio
async def test_technical_analysis_rejects_fewer_than_twenty_successes() -> None:
    # Given
    tickers = _tickers()
    market = _BatchMarketData(tickers, frozenset(tickers[18:-1]))

    # When / Then
    with pytest.raises(ValidationFailureError, match="exactly 20"):
        _ = await _screen(market)


@pytest.mark.anyio
async def test_daily_screener_ranks_ten_and_keeps_requested_focus() -> None:
    # Given
    technical = await _screen(_BatchMarketData(_tickers()))

    # When
    first = await DailyScreener().execute(technical)
    second = await DailyScreener().execute(technical)

    # Then
    assert first.daily_screener_output == second.daily_screener_output
    assert first.daily_screener_output is not None
    assert len(first.daily_screener_output.picks) == 10
    assert len({pick.ticker for pick in first.daily_screener_output.picks}) == 10
    assert any(
        pick.ticker == "NVDA" and pick.is_requested_focus
        for pick in first.daily_screener_output.picks
    )
    assert len(first.to_run().detail.roles[2].items) == 10
    assert any(
        "NVDA" in item and "사용자 요청 심층 분석" in item
        for item in first.to_run().detail.roles[2].items
    )
    assert first.request.ticker == "NVDA"
