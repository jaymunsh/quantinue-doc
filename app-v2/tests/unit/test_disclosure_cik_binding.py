"""Role 05 must read the requested company's filings — or none at all.

The live path used to request a hardcoded CIK (NVDA), so every ticker was
scored on NVDA's disclosures. That silently falsified `disclosure_score`,
which is one of role 07's four votes and the input to the hard-negative gate.
"""

from datetime import UTC, datetime, timedelta

import pytest

from quantinue.core.contracts import PipelineContext, PipelineRequest
from quantinue.llm.provider import AnalysisResult, AnalysisTask, DeterministicAnalyzer
from quantinue.market_data.models import (
    Candle,
    MacroObservation,
    NewsItem,
    Provenance,
    SecSubmission,
    SecuritySnapshot,
)
from quantinue.roles.role_05_disclosure_analysis.service import DisclosureAnalysis

NOW = datetime(2026, 7, 14, 3, 0, tzinfo=UTC)
APPLE_CIK = "0000320193"


def _submission(cik: str, form: str = "8-K") -> SecSubmission:
    return SecSubmission(
        cik=cik,
        company_name="Apple Inc.",
        accession_number="0000320193-26-000042",
        form=form,
        filed_at=NOW - timedelta(minutes=1),
        primary_document="aapl-8k.htm",
        provenance=Provenance(
            source="sec-edgar",
            source_ref=f"https://data.sec.gov/submissions/CIK{cik}.json",
            observed_at=NOW - timedelta(minutes=1),
            captured_at=NOW,
            confidence=0.9,
            execution_id="run-disclosure",
        ),
    )


class _SecMarketData:
    """Market data that resolves CIKs and records what role 05 asked for."""

    def __init__(
        self,
        *,
        cik_map: dict[str, str] | None = None,
        filings: tuple[SecSubmission, ...] | None = None,
    ) -> None:
        self._cik_map = {"AAPL": APPLE_CIK} if cik_map is None else cik_map
        self._filings = (_submission(APPLE_CIK),) if filings is None else filings
        self.requested_ciks: list[str] = []
        self.requested_tickers: list[str] = []

    async def screener(self, execution_id: str) -> tuple[SecuritySnapshot, ...]:
        del execution_id
        return ()

    async def candles(self, ticker: str, execution_id: str) -> tuple[Candle, ...]:
        del ticker, execution_id
        return ()

    async def macro(self, series: str, execution_id: str) -> tuple[MacroObservation, ...]:
        del series, execution_id
        return ()

    async def rss(self, execution_id: str) -> tuple[NewsItem, ...]:
        del execution_id
        return ()

    async def sec_cik_for_ticker(self, ticker: str, execution_id: str) -> str | None:
        del execution_id
        self.requested_tickers.append(ticker)
        return self._cik_map.get(ticker)

    async def sec_submissions(self, cik: str, execution_id: str) -> tuple[SecSubmission, ...]:
        del execution_id
        self.requested_ciks.append(cik)
        return self._filings


class _CountingAnalyzer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def analyze(self, task: AnalysisTask, prompt: str) -> AnalysisResult:
        self.calls.append(prompt)
        return await DeterministicAnalyzer().analyze(task, prompt)


def _context(ticker: str) -> PipelineContext:
    return PipelineContext(request=PipelineRequest(ticker=ticker, cycle_ts=NOW))


@pytest.mark.anyio
async def test_disclosure_reads_the_filings_of_the_requested_ticker() -> None:
    # Given: a run for AAPL
    market_data = _SecMarketData()
    service = DisclosureAnalysis(analyzer=_CountingAnalyzer(), market_data=market_data)

    # When
    updated = await service.execute(_context("AAPL"))

    # Then: the CIK came from the ticker, not from a hardcoded constant
    assert market_data.requested_tickers == ["AAPL"]
    assert market_data.requested_ciks == [APPLE_CIK]
    assert updated.disclosure_score is not None


@pytest.mark.anyio
async def test_unresolvable_ticker_abstains_instead_of_scoring_another_company() -> None:
    # Given: a ticker absent from the SEC ticker map
    market_data = _SecMarketData(cik_map={})
    analyzer = _CountingAnalyzer()
    service = DisclosureAnalysis(analyzer=analyzer, market_data=market_data)

    # When
    updated = await service.execute(_context("ZZZZ"))

    # Then: no filings fetched, no tokens spent, and no fabricated score
    assert market_data.requested_ciks == []
    assert analyzer.calls == []
    assert updated.disclosure_score is None


@pytest.mark.anyio
async def test_company_without_filings_abstains_rather_than_scoring_zero() -> None:
    # Given: a resolvable company whose submissions feed is empty
    market_data = _SecMarketData(filings=())
    analyzer = _CountingAnalyzer()
    service = DisclosureAnalysis(analyzer=analyzer, market_data=market_data)

    # When
    updated = await service.execute(_context("AAPL"))

    # Then: absence is not evidence of a bad disclosure
    assert analyzer.calls == []
    assert updated.disclosure_score is None


@pytest.mark.anyio
async def test_market_data_without_cik_resolution_abstains() -> None:
    # Given: a transport predating CIK resolution (no sec_cik_for_ticker)
    class _LegacyMarketData(_SecMarketData):
        sec_cik_for_ticker = None  # type: ignore[assignment]

    market_data = _LegacyMarketData()
    analyzer = _CountingAnalyzer()
    service = DisclosureAnalysis(analyzer=analyzer, market_data=market_data)

    # When
    updated = await service.execute(_context("AAPL"))

    # Then: it must not fall back to some other company's filings
    assert market_data.requested_ciks == []
    assert analyzer.calls == []
    assert updated.disclosure_score is None
