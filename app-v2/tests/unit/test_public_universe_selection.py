"""Public-universe selection boundaries."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

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
from quantinue.orchestration.policy import ScreeningConfig
from quantinue.roles.role_01_universe_screener.contracts import (
    UniverseMember,
    UniverseScreenerOutput,
)
from quantinue.roles.role_01_universe_screener.service import UniverseScreener
from quantinue.roles.role_02_technical_analysis.service import TechnicalAnalysis

NOW = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _UniverseMarketData:
    snapshots: tuple[SecuritySnapshot, ...]

    async def screener(self, execution_id: str) -> tuple[SecuritySnapshot, ...]:
        del execution_id
        return self.snapshots

    async def candles(self, ticker: str, execution_id: str) -> tuple[Candle, ...]:
        return tuple(
            Candle(
                ticker=ticker,
                opened_at=NOW - timedelta(days=59 - day),
                open=Decimal(99 + day) / 2,
                high=Decimal(102 + day) / 2,
                low=Decimal(98 + day) / 2,
                close=Decimal(101 + day) / 2,
                volume=1_000 + day,
                provenance=Provenance(
                    source="market-candles",
                    source_ref=f"https://example.test/{ticker}",
                    observed_at=NOW,
                    captured_at=NOW,
                    confidence=0.9,
                    execution_id=execution_id,
                ),
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


def _security(ticker: str, rank: int) -> SecuritySnapshot:
    return SecuritySnapshot(
        ticker=ticker,
        name=f"Company {rank}",
        market_cap=Decimal(10_000 - rank),
        last_price=Decimal(100),
        volume=1_000,
        provenance=Provenance(
            source="nasdaq-screener",
            source_ref="https://api.nasdaq.com/api/screener/stocks",
            observed_at=NOW,
            captured_at=NOW,
            confidence=0.9,
            execution_id="run-1",
        ),
    )


def test_legacy_checkpoint_contract_accepts_more_than_100_universe_members() -> None:
    # Given
    members = tuple(
        UniverseMember(
            as_of_date=NOW.date(),
            ticker=f"T{rank:03d}",
            company_name=f"Company {rank}",
            market_cap=10_000 - rank,
            evidence_ids=("legacy:01:market",),
        )
        for rank in range(101)
    )

    # When
    output = UniverseScreenerOutput(run_id="legacy", generated_at=NOW, members=members)

    # Then
    assert len(output.members) == 101


@pytest.mark.anyio
async def test_public_universe_preserves_requested_ticker_when_feed_contains_it() -> None:
    # Given
    snapshots = (_security("AAPL", 1), _security("NVDA", 2), _security("MSFT", 3))
    context = PipelineContext(request=PipelineRequest(ticker="NVDA", cycle_ts=NOW))

    # When
    result = await UniverseScreener(_UniverseMarketData(snapshots)).execute(context)

    # Then
    assert "NVDA" in result.universe


@pytest.mark.anyio
async def test_public_universe_keeps_all_eligible_ranked_by_market_cap() -> None:
    # Given: duplicates and a lower-cap requested ticker (market_cap = 10_000 - rank)
    first_fifty = tuple(_security(f"T{rank:03d}", rank) for rank in range(50))
    snapshots = (
        *first_fifty,
        _security("T010", 102),
        _security("NVDA", 101),
        _security("NVDA", 103),
    )
    context = PipelineContext(request=PipelineRequest(ticker="NVDA", cycle_ts=NOW))

    # When
    result = await UniverseScreener(_UniverseMarketData(snapshots)).execute(context)

    # Then: every unique name survives, ordered by market cap (NVDA is smallest)
    assert result.universe == (*tuple(f"T{rank:03d}" for rank in range(50)), "NVDA")
    assert len(result.universe) == len(set(result.universe)) == 51
    assert result.universe_output is not None
    assert len(result.universe_output.members) == 51


@pytest.mark.anyio
async def test_public_universe_truncation_keeps_the_largest_caps() -> None:
    # Given
    snapshots = (
        *tuple(_security(f"T{rank:03d}", rank) for rank in range(50)),
        _security("NVDA", 101),
    )
    context = PipelineContext(request=PipelineRequest(ticker="NVDA", cycle_ts=NOW))

    # When
    result = await UniverseScreener(
        _UniverseMarketData(snapshots), ScreeningConfig(universe_size=5)
    ).execute(context)

    # Then: the four largest caps plus the retained focus
    assert result.universe == ("T000", "T001", "T002", "T003", "NVDA")


@pytest.mark.anyio
async def test_public_universe_rejects_feed_without_requested_ticker() -> None:
    # Given
    snapshots = tuple(_security(f"T{rank:03d}", rank) for rank in range(100))
    context = PipelineContext(request=PipelineRequest(ticker="NVDA", cycle_ts=NOW))

    # When / Then
    with pytest.raises(ValidationFailureError, match="requested ticker NVDA is unavailable"):
        _ = await UniverseScreener(_UniverseMarketData(snapshots)).execute(context)


@pytest.mark.anyio
async def test_public_universe_rejects_empty_feed() -> None:
    # Given
    context = PipelineContext(request=PipelineRequest(ticker="NVDA", cycle_ts=NOW))

    # When / Then
    with pytest.raises(ValidationFailureError, match="no eligible securities"):
        _ = await UniverseScreener(_UniverseMarketData(())).execute(context)


@pytest.mark.anyio
async def test_public_universe_rejects_feed_with_only_zero_market_caps() -> None:
    # Given
    zero_cap = _security("NVDA", 1).model_copy(update={"market_cap": Decimal(0)})
    context = PipelineContext(request=PipelineRequest(ticker="NVDA", cycle_ts=NOW))

    # When / Then
    with pytest.raises(ValidationFailureError, match="no eligible securities"):
        _ = await UniverseScreener(_UniverseMarketData((zero_cap,))).execute(context)


@pytest.mark.anyio
async def test_role02_prices_only_the_configured_candidate_slice() -> None:
    # Given: the universe is wide, but candle work is capped by config
    snapshots = (
        *tuple(_security(f"T{rank:03d}", rank) for rank in range(50)),
        _security("NVDA", 101),
    )
    market_data = _UniverseMarketData(snapshots)
    screening = ScreeningConfig(technical_candidates=20, min_price_usd=0, min_avg_dollar_vol=0)
    context = PipelineContext(request=PipelineRequest(ticker="NVDA", cycle_ts=NOW))
    context = await UniverseScreener(market_data, screening).execute(context)

    # When
    result = await TechnicalAnalysis(market_data, screening).execute(context)

    # Then: the full universe is retained, but only 20 names are priced
    assert len(result.universe) == 51
    assert result.technical_output is not None
    assert tuple(item.ticker for item in result.technical_output.snapshots) == (
        *result.universe[:19],
        "NVDA",
    )
