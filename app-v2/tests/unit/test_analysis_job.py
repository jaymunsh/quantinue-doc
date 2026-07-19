"""Phase 3 분석 잡: 범위 전체를 돌고, 보유는 팔 수 있고, 매도도 검증받는다."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from quantinue.llm.provider import AnalysisMetadata, AnalysisResult, AnalysisTask
from quantinue.orchestration.policy import GatesConfig, ProfileConfig
from quantinue.roles.analysis.contracts import AnalysisSubject
from quantinue.roles.analysis.job import AnalysisJob
from quantinue.roles.exits.contracts import OpenPosition

if TYPE_CHECKING:
    from quantinue.db.domain_records import CriticVerdictWrite, StrategistSignalWrite

_AS_OF = date(2026, 7, 17)
_SESSION = date(2026, 7, 16)


class _Analyzer:
    """Return a fixed bullishness so the code gates are what decide."""

    def __init__(self, strategy: float, critic: float = 0.9) -> None:
        self._scores = {AnalysisTask.STRATEGY: strategy, AnalysisTask.CRITIC: critic}
        self.prompts: list[tuple[AnalysisTask, str]] = []
        self.profiles: list[tuple[AnalysisTask, str | None]] = []

    async def analyze(
        self, task: AnalysisTask, prompt: str, *, profile: str | None = None
    ) -> AnalysisResult:
        self.prompts.append((task, prompt))
        self.profiles.append((task, profile))
        return AnalysisResult(
            score=self._scores[task],
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


def _subject(ticker: str, rank: int = 1, score: float = 0.9) -> AnalysisSubject:
    return AnalysisSubject(
        ticker=ticker,
        rank=rank,
        score=score,
        bucket="trend_leader",
        close=Decimal("100.00"),
        high=Decimal("101.00"),
        low=Decimal("99.00"),
        close_prev=Decimal("99.50"),
    )


class _Domain:
    def __init__(
        self,
        subjects: tuple[AnalysisSubject, ...],
        positions: tuple[OpenPosition, ...] = (),
    ) -> None:
        self._subjects = subjects
        self._positions = positions
        self.signals: list[StrategistSignalWrite] = []
        self.verdicts: list[CriticVerdictWrite] = []

    async def analysis_subjects(
        self, as_of: date, session: date
    ) -> tuple[AnalysisSubject, ...]:
        del as_of, session
        return self._subjects

    async def disclosure_evidence(
        self, session: date, tickers: tuple[str, ...]
    ) -> dict[str, tuple[str, ...]]:
        del session, tickers
        return {}

    async def open_positions(self) -> tuple[OpenPosition, ...]:
        return self._positions

    async def save_signal(self, value: StrategistSignalWrite) -> int:
        self.signals.append(value)
        return len(self.signals)

    async def save_verdict(self, value: CriticVerdictWrite) -> int:
        self.verdicts.append(value)
        return len(self.verdicts)


class _Store:
    def __init__(self, domain: _Domain) -> None:
        self.domain = domain


def _position(ticker: str, quantity: int = 10) -> OpenPosition:
    return OpenPosition(
        order_id=1,
        signal_id=1,
        account_id=1,
        ticker=ticker,
        quantity=quantity,
        entry_price=Decimal("120.00"),
        stop_price=Decimal("102.00"),
        take_profit_price=Decimal("144.00"),
        filled_on=date(2026, 7, 10),
        inv_type="aggressive",
    )


def _job(domain: _Domain, analyzer: _Analyzer) -> AnalysisJob:
    return AnalysisJob(
        store=_Store(domain),
        analyzer=analyzer,
        gates=GatesConfig(evidence_max_age_minutes=2_880),
        profile=ProfileConfig(buy_threshold=0.65, sell_threshold=0.60),
        profile_name="aggressive",
    )


@pytest.mark.anyio
async def test_every_ticker_in_scope_gets_a_signal() -> None:
    """구 러너는 픽을 50개 만들고 하나만 봤다 — 50에서 1로 떨어지는 절벽."""
    # Given
    domain = _Domain((_subject("AAA", 1), _subject("BBB", 2), _subject("CCC", 3)))

    # When
    outcomes = await _job(domain, _Analyzer(strategy=0.9)).run(
        as_of=_AS_OF, session=_SESSION
    )

    # Then
    assert [outcome.ticker for outcome in outcomes] == ["AAA", "BBB", "CCC"]
    assert len(domain.signals) == 3


@pytest.mark.anyio
async def test_a_held_ticker_whose_thesis_collapsed_is_sold() -> None:
    """07이 팔 수 있게 된 이유 전체 — 보유 맥락이 입력에 들어왔기 때문이다."""
    # Given: 강세 확신 0.1 → 약세 확신 0.9, 그리고 우리가 들고 있다.
    domain = _Domain((_subject("HELD", rank=15, score=0.1),), (_position("HELD"),))

    # When
    outcomes = await _job(domain, _Analyzer(strategy=0.1)).run(
        as_of=_AS_OF, session=_SESSION
    )

    # Then
    assert outcomes[0].side == "sell"
    assert domain.signals[0].side == "sell"


@pytest.mark.anyio
async def test_the_same_collapse_on_a_ticker_we_do_not_own_is_only_a_hold() -> None:
    """없는 것은 팔 수 없다 — 매도 판단의 유일한 하드 게이트."""
    # Given
    domain = _Domain((_subject("NOTHELD", rank=15, score=0.1),))

    # When
    outcomes = await _job(domain, _Analyzer(strategy=0.1)).run(
        as_of=_AS_OF, session=_SESSION
    )

    # Then
    assert outcomes[0].side == "hold"


@pytest.mark.anyio
async def test_a_sell_proposal_is_reviewed_rather_than_waved_through() -> None:
    """패닉 매도를 반박할 자리가 없으면 모델의 약세 확신이 그대로 집행된다."""
    # Given
    analyzer = _Analyzer(strategy=0.1, critic=0.9)
    domain = _Domain((_subject("HELD", rank=15, score=0.1),), (_position("HELD"),))

    # When
    _ = await _job(domain, analyzer).run(as_of=_AS_OF, session=_SESSION)

    # Then: 매도 제안에도 크리틱 콜이 실제로 나갔다.
    assert AnalysisTask.CRITIC in {task for task, _ in analyzer.prompts}
    assert domain.verdicts[0].decision == "pass"


@pytest.mark.anyio
async def test_a_hold_costs_no_model_call_for_review() -> None:
    """hold은 아무것도 집행하지 않는다 — 콜 예산은 돈이 움직이는 판단에만."""
    # Given: 매수 문턱에도 매도 문턱에도 못 미치는 어중간한 확신.
    analyzer = _Analyzer(strategy=0.5)
    domain = _Domain((_subject("MEH", score=0.5),))

    # When
    outcomes = await _job(domain, analyzer).run(as_of=_AS_OF, session=_SESSION)

    # Then
    assert outcomes[0].side == "hold"
    assert [task for task, _ in analyzer.prompts] == [AnalysisTask.STRATEGY]
    assert domain.verdicts[0].category == "no_action_proposal"


@pytest.mark.anyio
async def test_the_model_sees_the_holding_context_not_just_three_floats() -> None:
    """구 07의 프롬프트는 f"technical=..., disclosure=..., news=..."가 전부였다."""
    # Given
    analyzer = _Analyzer(strategy=0.9)
    domain = _Domain((_subject("HELD", rank=15, score=0.1),), (_position("HELD"),))

    # When
    _ = await _job(domain, analyzer).run(as_of=_AS_OF, session=_SESSION)

    # Then
    prompt = next(text for task, text in analyzer.prompts if task is AnalysisTask.STRATEGY)
    assert "ticker=HELD" in prompt
    assert "held_quantity=10" in prompt
    assert "entry_price=120.00" in prompt
    assert "unrealized_pct=-16.67" in prompt


@pytest.mark.anyio
async def test_the_judgement_call_carries_the_persona_it_is_made_under() -> None:
    """성향이 분석기까지 안 가면 두 페르소나가 같은 프롬프트로 돈다 — 실행에서 그랬다."""
    # Given
    domain = _Domain((_subject("AAA"),))
    analyzer = _Analyzer(strategy=0.9)

    # When
    _ = await _job(domain, analyzer).run(as_of=_AS_OF, session=_SESSION)

    # Then
    assert (AnalysisTask.STRATEGY, "aggressive") in analyzer.profiles


@pytest.mark.anyio
async def test_a_rejected_proposal_still_produces_a_valid_verdict() -> None:
    """크리틱이 반박에 성공한 첫 종목에서 그날 분석 전체가 죽던 것.

    mock 분석기는 크리틱에 고정 0.82를 내서 항상 통과했다 — reject 갈래는
    실 LLM을 붙이기 전까지 한 번도 실행된 적이 없다.
    """
    # Given: 크리틱이 승인 문턱을 못 넘는 점수를 낸다
    domain = _Domain((_subject("AAA"), _subject("BBB")))
    analyzer = _Analyzer(strategy=0.9, critic=0.1)

    # When
    outcomes = await _job(domain, analyzer).run(as_of=_AS_OF, session=_SESSION)

    # Then: 예외 없이 두 종목 모두 판정을 받는다
    assert len(outcomes) == 2
    assert [outcome.approved for outcome in outcomes] == [False, False]
    assert [verdict.decided_layer for verdict in domain.verdicts] == ["llm", "llm"]
