"""공시 채점 잡: SEC 폼을 07이 투표할 수 있는 점수로 바꾼다.

뉴스가 아니라 공시를 채점하는 이유는 실측이다. 와이어 뉴스(allow 0.95)는
우리 픽을 한 종목도 덮지 않았고(4개 픽 날짜 전부 겹침 0), 픽을 덮는 뉴스는
전부 benzinga(gray 0.50)라 ``gates.source_trust_min``(0.55)에서 표를 잃는다.
공시는 sec.gov(allow)이고 픽의 25~30%를 덮으며, 무엇보다 ``disclosure_score``는
``vote_conviction``에서 신뢰도 게이트를 아예 타지 않는다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from quantinue.llm.provider import AnalysisMetadata, AnalysisResult, AnalysisTask
from quantinue.roles.analysis.contracts import AnalysisSubject
from quantinue.roles.disclosure.job import DisclosureScoringJob

_AS_OF = date(2026, 7, 17)
_SESSION = date(2026, 7, 16)


class _Analyzer:
    """Return a fixed score so the job's scope logic is what the test measures."""

    def __init__(self, score: float = 0.8) -> None:
        self._score = score
        self.prompts: list[tuple[AnalysisTask, str]] = []

    async def analyze(
        self, task: AnalysisTask, prompt: str, *, profile: str | None = None
    ) -> AnalysisResult:
        self.prompts.append((task, prompt))
        return AnalysisResult(
            score=self._score,
            label="ok",
            reason="fixture rationale",
            metadata=AnalysisMetadata(
                model="test",
                provider="mock",
                prompt_version="v1",
                policy_version="v1",
                input_hash="0" * 64,
            ),
        )


class _FragileAnalyzer(_Analyzer):
    """Blow up on whichever prompt names the marked ticker."""

    def __init__(self, fails_for: str) -> None:
        super().__init__()
        self._fails_for = fails_for

    async def analyze(
        self, task: AnalysisTask, prompt: str, *, profile: str | None = None
    ) -> AnalysisResult:
        if f"Ticker: {self._fails_for}\n" in prompt:
            msg = "structured output failed"
            raise RuntimeError(msg)
        return await super().analyze(task, prompt, profile=profile)


def _subject(ticker: str, rank: int) -> AnalysisSubject:
    return AnalysisSubject(
        ticker=ticker,
        rank=rank,
        score=0.9,
        bucket="momentum",
        close=Decimal(100),
        high=Decimal(101),
        low=Decimal(99),
        close_prev=Decimal(98),
    )


class _Domain:
    """The two readers the job needs, plus a recording sink."""

    def __init__(
        self, subjects: tuple[str, ...], filings: dict[str, tuple[str, ...]]
    ) -> None:
        self._subjects = tuple(
            _subject(ticker, index + 1) for index, ticker in enumerate(subjects)
        )
        self._filings = filings
        self.saved: list[object] = []

    async def analysis_subjects(
        self, as_of: date, session: date
    ) -> tuple[AnalysisSubject, ...]:
        return self._subjects

    async def disclosure_evidence(
        self, session: date, tickers: tuple[str, ...]
    ) -> dict[str, tuple[str, ...]]:
        return {
            ticker: forms
            for ticker, forms in self._filings.items()
            if ticker in tickers
        }

    async def save_disclosure_signal(self, value: object) -> None:
        self.saved.append(value)


@pytest.mark.anyio
async def test_scores_only_the_picks_that_filed() -> None:
    """공시가 없는 종목은 기권이다 — 침묵에 점수를 붙이면 그게 판단을 희석한다."""
    analyzer = _Analyzer()
    domain = _Domain(subjects=("AAPL", "MSFT"), filings={"AAPL": ("8-K",)})
    job = DisclosureScoringJob(store=domain, analyzer=analyzer)

    run = await job.run(as_of=_AS_OF, session=_SESSION)

    assert tuple(score.ticker for score in run.scores) == ("AAPL",)
    assert len(analyzer.prompts) == 1
    assert analyzer.prompts[0][0] is AnalysisTask.DISCLOSURE


@pytest.mark.anyio
async def test_writes_the_signal_at_the_cycle_the_analysis_job_will_read() -> None:
    """계보 FK가 걸리는 자리다 — 분석 잡과 같은 자정 cycle_ts여야 조인된다."""
    domain = _Domain(subjects=("AAPL",), filings={"AAPL": ("8-K", "10-Q")})
    job = DisclosureScoringJob(store=domain, analyzer=_Analyzer(score=0.72))

    await job.run(as_of=_AS_OF, session=_SESSION)

    (saved,) = domain.saved
    assert saved.ticker == "AAPL"
    assert saved.cycle_ts == datetime.combine(_AS_OF, datetime.min.time(), tzinfo=UTC)
    assert saved.trade_date == _AS_OF
    assert saved.has_signal is True
    assert saved.sentiment_score == pytest.approx(0.72)
    assert saved.disclosure_count == 2


@pytest.mark.anyio
async def test_one_ticker_failing_does_not_take_the_rest_down() -> None:
    """실측된 결함 패턴이다 — 모델이 구조화 출력을 한 번 놓쳐 성향 하나가 통째로 날아갔다."""
    analyzer = _FragileAnalyzer(fails_for="MSFT")
    domain = _Domain(
        subjects=("AAPL", "MSFT", "NVDA"),
        filings={"AAPL": ("8-K",), "MSFT": ("8-K",), "NVDA": ("10-Q",)},
    )
    job = DisclosureScoringJob(store=domain, analyzer=analyzer)

    run = await job.run(as_of=_AS_OF, session=_SESSION)

    assert tuple(score.ticker for score in run.scores) == ("AAPL", "NVDA")
    # 건너뛴 수가 사라지면 잡 원장이 "전부 채점했다"로 읽힌다.
    assert run.skipped == 1


@pytest.mark.anyio
async def test_every_ticker_failing_fails_the_job() -> None:
    """전 종목 실패는 모델이 죽은 것이다 — 조용히 '0건 채점'으로 성공하면 슬롯이 잠긴다."""

    class _DeadAnalyzer(_Analyzer):
        async def analyze(
            self, task: AnalysisTask, prompt: str, *, profile: str | None = None
        ) -> AnalysisResult:
            msg = "model is down"
            raise RuntimeError(msg)

    domain = _Domain(
        subjects=("AAPL", "MSFT"), filings={"AAPL": ("8-K",), "MSFT": ("10-Q",)}
    )
    job = DisclosureScoringJob(store=domain, analyzer=_DeadAnalyzer())

    with pytest.raises(RuntimeError):
        await job.run(as_of=_AS_OF, session=_SESSION)


@pytest.mark.anyio
async def test_no_filings_at_all_is_a_quiet_success() -> None:
    """공시 없는 날은 정상이다 — 모델이 죽은 것과 구별되어야 한다."""
    domain = _Domain(subjects=("AAPL", "MSFT"), filings={})
    job = DisclosureScoringJob(store=domain, analyzer=_Analyzer())

    run = await job.run(as_of=_AS_OF, session=_SESSION)

    assert run.scores == ()
    assert run.skipped == 0


@pytest.mark.anyio
async def test_the_scoring_model_is_recorded_with_the_vote() -> None:
    """어느 모델이 이 표를 만들었는지는 재현의 전제다 — 스키마도 NOT NULL로 요구한다."""
    domain = _Domain(subjects=("AAPL",), filings={"AAPL": ("8-K",)})
    job = DisclosureScoringJob(store=domain, analyzer=_Analyzer())

    await job.run(as_of=_AS_OF, session=_SESSION)

    (saved,) = domain.saved
    assert saved.model_provider == "mock"
    assert saved.model_name == "test"
