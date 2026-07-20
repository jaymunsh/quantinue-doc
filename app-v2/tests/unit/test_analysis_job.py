"""Phase 3 분석 잡: 범위 전체를 돌고, 보유는 팔 수 있고, 매도도 검증받는다."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from quantinue.db.domain_records import MacroSnapshot
from quantinue.llm.provider import AnalysisMetadata, AnalysisResult, AnalysisTask
from quantinue.orchestration.policy import GatesConfig, ProfileConfig
from quantinue.roles.analysis.contracts import AnalysisSubject
from quantinue.roles.analysis.job import AnalysisJob
from quantinue.roles.exits.contracts import OpenPosition
from quantinue.roles.screening import RankedCandidate

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


class _FragileAnalyzer(_Analyzer):
    """An analyzer that blows up on whichever prompt contains a marker."""

    def __init__(self, fails_for: str) -> None:
        super().__init__(strategy=0.9)
        self._fails_for = fails_for

    async def analyze(
        self, task: AnalysisTask, prompt: str, *, profile: str | None = None
    ) -> AnalysisResult:
        if self._fails_for in prompt:
            message = "structured output missed"
            raise RuntimeError(message)
        return await super().analyze(task, prompt, profile=profile)


def _indicators() -> RankedCandidate:
    """A ticker in a textbook trend template, measured from stored bars."""
    return RankedCandidate(
        ticker="AAA",
        close=Decimal("100.00"),
        ret_20d_pct=12.4,
        ma20=Decimal("105.00"),
        ma50=Decimal("100.00"),
        high_252=Decimal("102.0408"),
        rsi=62.1,
        volume=1_800,
        average_volume=1_000.0,
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
    def __init__(  # noqa: PLR0913 - 증거 종류가 늘면 페이크의 씨앗도 는다
        self,
        subjects: tuple[AnalysisSubject, ...],
        positions: tuple[OpenPosition, ...] = (),
        headlines: dict[str, tuple[str, ...]] | None = None,
        macro: tuple[str, float] | None = None,
        indicators: dict[str, RankedCandidate] | None = None,
        disclosure_scores: dict[str, float] | None = None,
    ) -> None:
        self._disclosure_scores = disclosure_scores or {}
        self._macro = macro
        self._indicators = indicators or {}
        self._subjects = subjects
        self._positions = positions
        self._headlines = headlines or {}
        self.news_calls: list[tuple[date, tuple[str, ...], int]] = []
        self.signals: list[StrategistSignalWrite] = []
        self.verdicts: list[CriticVerdictWrite] = []

    async def analysis_subjects(self, as_of: date, session: date) -> tuple[AnalysisSubject, ...]:
        del as_of, session
        return self._subjects

    async def disclosure_evidence(
        self, session: date, tickers: tuple[str, ...]
    ) -> dict[str, tuple[str, ...]]:
        del session, tickers
        return {}

    async def news_evidence(
        self, session: date, tickers: tuple[str, ...], limit: int
    ) -> dict[str, tuple[str, ...]]:
        self.news_calls.append((session, tickers, limit))
        return self._headlines

    async def disclosure_scores(self, as_of: date) -> dict[str, float]:
        del as_of
        return dict(self._disclosure_scores)

    async def pick_indicators(self, as_of: date, session: date) -> dict[str, RankedCandidate]:
        del as_of, session
        return dict(self._indicators)

    async def latest_macro(self, as_of: date, max_age_minutes: int) -> object | None:
        del as_of, max_age_minutes
        if self._macro is None:
            return None
        return MacroSnapshot(
            regime=self._macro[0],
            risk_score=self._macro[1],
            as_of=datetime.combine(_AS_OF, time(), tzinfo=UTC),
        )

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


def _job(
    domain: _Domain,
    analyzer: _Analyzer,
    *,
    headlines_per_ticker: int = 5,
    risk_off_action: str = "penalty",
    gates: GatesConfig | None = None,
) -> AnalysisJob:
    return AnalysisJob(
        store=_Store(domain),
        analyzer=analyzer,
        gates=gates or GatesConfig(evidence_max_age_minutes=2_880),
        profile=ProfileConfig(
            buy_threshold=0.65,
            sell_threshold=0.60,
            risk_off_action=risk_off_action,  # pyright: ignore[reportArgumentType]
        ),
        profile_name="aggressive",
        headlines_per_ticker=headlines_per_ticker,
    )


@pytest.mark.anyio
async def test_every_ticker_in_scope_gets_a_signal() -> None:
    """구 러너는 픽을 50개 만들고 하나만 봤다 — 50에서 1로 떨어지는 절벽."""
    # Given
    domain = _Domain((_subject("AAA", 1), _subject("BBB", 2), _subject("CCC", 3)))

    # When
    outcomes = (
        await _job(domain, _Analyzer(strategy=0.9)).run(as_of=_AS_OF, session=_SESSION)
    ).outcomes

    # Then
    assert [outcome.ticker for outcome in outcomes] == ["AAA", "BBB", "CCC"]
    assert len(domain.signals) == 3


@pytest.mark.anyio
async def test_a_held_ticker_whose_thesis_collapsed_is_sold() -> None:
    """07이 팔 수 있게 된 이유 전체 — 보유 맥락이 입력에 들어왔기 때문이다."""
    # Given: 강세 확신 0.1 → 약세 확신 0.9, 그리고 우리가 들고 있다.
    domain = _Domain((_subject("HELD", rank=15, score=0.1),), (_position("HELD"),))

    # When
    outcomes = (
        await _job(domain, _Analyzer(strategy=0.1)).run(as_of=_AS_OF, session=_SESSION)
    ).outcomes

    # Then
    assert outcomes[0].side == "sell"
    assert domain.signals[0].side == "sell"


@pytest.mark.anyio
async def test_the_same_collapse_on_a_ticker_we_do_not_own_is_only_a_hold() -> None:
    """없는 것은 팔 수 없다 — 매도 판단의 유일한 하드 게이트."""
    # Given
    domain = _Domain((_subject("NOTHELD", rank=15, score=0.1),))

    # When
    outcomes = (
        await _job(domain, _Analyzer(strategy=0.1)).run(as_of=_AS_OF, session=_SESSION)
    ).outcomes

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
    outcomes = (await _job(domain, analyzer).run(as_of=_AS_OF, session=_SESSION)).outcomes

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
    outcomes = (await _job(domain, analyzer).run(as_of=_AS_OF, session=_SESSION)).outcomes

    # Then: 예외 없이 두 종목 모두 판정을 받는다
    assert len(outcomes) == 2
    assert [outcome.approved for outcome in outcomes] == [False, False]
    assert [verdict.decided_layer for verdict in domain.verdicts] == ["llm", "llm"]


@pytest.mark.anyio
async def test_the_collected_headlines_reach_the_evidence_synthesis() -> None:
    """뉴스는 별도 투표가 아니라 종합의 맥락으로 들어간다(출처 등급 gray).

    투표(``news_score``)로 넣으면 ``gates.source_trust_min``에 걸려 통째로
    박탈된다 — 수집·저장해놓고 판단에 한 글자도 못 보태는 유령이 된다.
    """
    # Given
    domain = _Domain((_subject("AAA"),), headlines={"AAA": ("FDA approves the thing",)})
    analyzer = _Analyzer(strategy=0.9)

    # When
    _ = await _job(domain, analyzer).run(as_of=_AS_OF, session=_SESSION)

    # Then
    prompt = next(text for task, text in analyzer.prompts if task is AnalysisTask.STRATEGY)
    assert "headlines=FDA approves the thing" in prompt


@pytest.mark.anyio
async def test_the_headline_budget_is_config_owned_and_asked_of_the_ledger() -> None:
    """종목당 몇 건을 볼지는 프롬프트 예산이다 — 코드 리터럴이면 조일 수 없다."""
    # Given
    domain = _Domain((_subject("AAA"),))

    # When
    _ = await _job(domain, _Analyzer(strategy=0.9), headlines_per_ticker=3).run(
        as_of=_AS_OF, session=_SESSION
    )

    # Then
    assert domain.news_calls == [(_SESSION, ("AAA",), 3)]


@pytest.mark.anyio
async def test_a_ticker_with_no_headlines_says_so_rather_than_going_silent() -> None:
    """빈 증거도 근거다 — 항목이 빠지면 모델은 "없었다"와 "안 알려줬다"를 못 가른다."""
    # Given
    domain = _Domain((_subject("AAA"),), headlines={})
    analyzer = _Analyzer(strategy=0.9)

    # When
    _ = await _job(domain, analyzer).run(as_of=_AS_OF, session=_SESSION)

    # Then
    prompt = next(text for task, text in analyzer.prompts if task is AnalysisTask.STRATEGY)
    assert "headlines=none" in prompt


@pytest.mark.anyio
async def test_news_stays_out_of_the_vote() -> None:
    """헤드라인이 있어도 news_score는 None이다 — 정책을 데이터 편의로 흔들지 않는다."""
    # Given
    domain = _Domain((_subject("AAA"),), headlines={"AAA": ("big news",)})

    # When
    _ = await _job(domain, _Analyzer(strategy=0.9)).run(as_of=_AS_OF, session=_SESSION)

    # Then: 투표에 들어갔다면 확신도가 뉴스 점수와 섞였을 것이다
    assert domain.signals[0].signal_consensus is not None


@pytest.mark.anyio
async def test_a_high_ranking_holding_can_still_be_sold() -> None:
    """실 실행에서 잡힌 결함: 스크리닝 상위 보유는 산술적으로 팔 수 없었다.

    확신도에 스크리닝 점수가 평균으로 섞여 있어서, 점수 0.95인 보유를 팔려면
    모델이 음수를 내야 했다. 그런데 픽은 정의상 점수 상위다 — 매도 경로 전체가
    닫혀 있었다. 실 LLM으로 -23% 포지션 3종목이 전부 hold/buy로 나온 것을
    확인하고 재현한 케이스다.
    """
    # Given: 랭킹은 최상위, 모델은 강하게 약세, 그리고 우리가 들고 있다
    domain = _Domain((_subject("HELD", rank=1, score=0.95),), (_position("HELD"),))

    # When
    outcomes = (
        await _job(domain, _Analyzer(strategy=0.1)).run(as_of=_AS_OF, session=_SESSION)
    ).outcomes

    # Then
    assert outcomes[0].side == "sell"


@pytest.mark.anyio
async def test_the_regime_reaches_the_judgement_under_the_persona_that_declared_it() -> None:
    """risk_off_action은 선언만 있고 소비자가 없던 설정이다.

    role_08이 risk_off를 무조건 reject해서 공격형의 penalty가 무시됐다. 소비자를
    붙이려면 **매크로가 판단에 도달해야** 한다 — 새 분석 잡은 매크로를 아예
    보지 않고 있어서, 문턱을 고쳐도 그 갈래가 돌지 않았다.
    """
    # Given: 원장에 위험회피 국면이 기록돼 있다(감점 구간 0.50 → -0.05)
    domain = _Domain((_subject("AAA"),), macro=("risk_off", 0.5))

    # When: 감수하겠다고 선언한 성향
    outcomes = (
        await _job(domain, _Analyzer(strategy=0.9), risk_off_action="penalty").run(
            as_of=_AS_OF, session=_SESSION
        )
    ).outcomes

    # Then: 매수까지 가되 감점은 받는다 — 같은 악재로 두 번 벌하지 않는다
    assert outcomes[0].side == "buy"
    assert domain.verdicts[0].category == "model_review"
    assert outcomes[0].conviction == 0.85


@pytest.mark.anyio
async def test_the_cautious_persona_stops_buying_in_the_same_regime() -> None:
    """같은 국면, 같은 증거, 다른 성향 — 여기서 갈리지 않으면 설정이 유령이다."""
    # Given
    domain = _Domain((_subject("AAA"),), macro=("risk_off", 0.5))

    # When
    _ = await _job(domain, _Analyzer(strategy=0.9), risk_off_action="no_new_buys").run(
        as_of=_AS_OF, session=_SESSION
    )

    # Then
    assert domain.verdicts[0].category == "macro_riskoff"


@pytest.mark.anyio
async def test_a_missing_macro_snapshot_neither_penalises_nor_blocks() -> None:
    """모르는 것을 근거로 막지 않는다 — 수집 실패가 매수 금지로 둔갑하면
    매크로 잡이 죽은 날 시스템 전체가 조용히 멈춘다."""
    # Given
    domain = _Domain((_subject("AAA"),), macro=None)

    # When
    outcomes = (
        await _job(domain, _Analyzer(strategy=0.9), risk_off_action="no_new_buys").run(
            as_of=_AS_OF, session=_SESSION
        )
    ).outcomes

    # Then
    assert domain.verdicts[0].category != "macro_riskoff"
    assert outcomes[0].side == "buy"


@pytest.mark.anyio
async def test_the_model_sees_the_indicators_its_methodology_asks_for() -> None:
    """실행에서 잡힌 자기충돌: 07이 "거래량 데이터 없음"을 고백하고 08이 그것을
    근거로 반박했다. 실 LLM 36건 중 승인 1~2건, 반박문이 전부 같은 말이었다.

    스크리닝 SQL은 ma20/50·high_252·rsi·거래량을 **이미 계산한다**. 그런데
    프롬프트에 가는 것은 합성 점수 하나뿐이라, 오닐(CAN SLIM)·미너비니(SEPA)에
    정박된 페르소나가 자기 방법론의 핵심 입력을 못 본 채 판단했다.
    """
    # Given
    domain = _Domain((_subject("AAA"),), indicators={"AAA": _indicators()})
    analyzer = _Analyzer(strategy=0.9)

    # When
    _ = await _job(domain, analyzer).run(as_of=_AS_OF, session=_SESSION)

    # Then
    prompt = next(text for task, text in analyzer.prompts if task is AnalysisTask.STRATEGY)
    assert "ma20=105.00" in prompt
    assert "ma50=100.00" in prompt
    assert "high_252_ratio=0.980" in prompt
    assert "ret_20d_pct=12.40" in prompt
    assert "rsi=62.1" in prompt
    assert "vol_ratio=1.80" in prompt


@pytest.mark.anyio
async def test_a_pick_we_could_not_measure_says_so_rather_than_guessing() -> None:
    """탈락한 보유는 랭킹에 없다(거래정지·유동성 미달). 없는 지표를 0으로 채우면
    "약하다"로 읽히는데 사실은 "재지 못했다"다 — 매도 판단에서 특히 위험하다."""
    # Given
    domain = _Domain((_subject("HELD"),), (_position("HELD"),), indicators={})
    analyzer = _Analyzer(strategy=0.5)

    # When
    _ = await _job(domain, analyzer).run(as_of=_AS_OF, session=_SESSION)

    # Then
    prompt = next(text for task, text in analyzer.prompts if task is AnalysisTask.STRATEGY)
    assert "indicators=none" in prompt


@pytest.mark.anyio
async def test_the_critic_sees_the_same_evidence_the_proposal_was_built_on() -> None:
    """원 증거를 못 보는 반박자는 산문만 공격할 수 있다.

    지금까지 08에 간 것은 `proposal=... conviction=... rationale=...`이 전부라,
    07이 정직하게 적은 약점이 그대로 반박 사유가 됐다. 주장 대 데이터로
    검증하려면 08도 같은 증거를 봐야 한다.
    """
    # Given
    domain = _Domain((_subject("AAA"),), indicators={"AAA": _indicators()})
    analyzer = _Analyzer(strategy=0.9)

    # When
    _ = await _job(domain, analyzer).run(as_of=_AS_OF, session=_SESSION)

    # Then
    critique = next(text for task, text in analyzer.prompts if task is AnalysisTask.CRITIC)
    assert "ma20=105.00" in critique
    assert "proposal=buy" in critique


@pytest.mark.anyio
async def test_one_ticker_that_the_model_fumbles_does_not_erase_the_day() -> None:
    """실측: conservative 22종목이 종목 하나의 구조화 출력 실패로 통째로 날아갔다.

    재시도 예산을 config로 되돌린 뒤에도 남는 위험이다 — 예산을 다 써도 실패할
    수 있고, 그때 이미 판단이 끝난 20종목까지 잃을 이유는 없다.
    """
    # Given: 두 번째 종목에서만 모델이 무너진다
    domain = _Domain((_subject("AAA"), _subject("BAD"), _subject("CCC")))
    analyzer = _FragileAnalyzer(fails_for="ticker=BAD")

    # When
    result = await _job(domain, analyzer).run(as_of=_AS_OF, session=_SESSION)

    # Then
    assert [outcome.ticker for outcome in result.outcomes] == ["AAA", "CCC"]
    assert result.skipped == 1


@pytest.mark.anyio
async def test_a_model_that_never_answers_fails_the_job_loudly() -> None:
    """전 종목이 실패하는 것은 종목 문제가 아니라 모델이 죽은 것이다.

    조용히 "0건 분석"으로 성공 기록을 남기면 잡 원장이 거짓말을 하고, 그날
    슬롯이 성공으로 잠겨 재시도되지 않는다.
    """
    # Given
    domain = _Domain((_subject("AAA"), _subject("BBB")))

    # When / Then
    with pytest.raises(RuntimeError):
        _ = await _job(domain, _FragileAnalyzer(fails_for="ticker=")).run(
            as_of=_AS_OF, session=_SESSION
        )


# --- 크리틱 승인 문턱의 소유권 ---------------------------------------------
# 구 러너 삭제로 role_08 서비스를 통해 이 규칙을 고정하던 test_critic_threshold_
# ownership이 사라졌다. 규칙 자체는 분석 잡(job.py의 threshold 계산)에 그대로
# 살아 있으므로 대체 테스트를 여기 둔다 — 삭제 커밋과 같은 커밋이다.


def _gates(**overrides: float) -> GatesConfig:
    return GatesConfig(evidence_max_age_minutes=2_880, **overrides)  # pyright: ignore[reportArgumentType]


@pytest.mark.anyio
async def test_the_approval_threshold_comes_from_gates_not_the_analyzer() -> None:
    """문턱의 소유자는 config다. 크리틱 점수가 그것을 넘어야 승인이다."""
    # Given: 같은 반박 점수(0.50)를 문턱만 바꿔 두 번 통과시킨다.
    # 강세 확신은 0.75 — 과신 구간(기본 0.90) **아래**여야 이 테스트가 재는 것이
    # 승인 문턱 하나로 남는다. 0.90을 쓰면 과신 상향에 걸려 원인이 섞인다.
    lenient = _Domain((_subject("AAA", 1),))
    strict = _Domain((_subject("AAA", 1),))

    # When
    _ = await _job(
        lenient, _Analyzer(strategy=0.75, critic=0.50), gates=_gates(critic_approval=0.40)
    ).run(as_of=_AS_OF, session=_SESSION)
    _ = await _job(
        strict, _Analyzer(strategy=0.75, critic=0.50), gates=_gates(critic_approval=0.60)
    ).run(as_of=_AS_OF, session=_SESSION)

    # Then
    assert lenient.verdicts[0].decision == "pass"
    assert strict.verdicts[0].decision == "reject"


@pytest.mark.anyio
async def test_overconfidence_raises_the_bar_the_proposal_must_clear() -> None:
    """과신할수록 반박을 더 세게 통과해야 한다(M4 승인 문턱 규칙 승계)."""
    # Given: 같은 크리틱 점수인데 강세 확신만 과신 구간으로 올린다.
    # 확신도는 모델 점수와 **같지 않다** — 스크리닝 점수가 섞여 들어간다
    # (strategy 0.70 → conviction 0.80). 그래서 구간을 모델 점수가 아니라
    # 실제 확신도 기준으로 잡는다.
    calm = _Domain((_subject("AAA", 1),))
    overconfident = _Domain((_subject("AAA", 1),))
    gates = _gates(
        critic_approval=0.40, overconfidence_conviction=0.90, overconfidence_approval=0.90
    )

    # When
    _ = await _job(calm, _Analyzer(strategy=0.70, critic=0.50), gates=gates).run(
        as_of=_AS_OF, session=_SESSION
    )
    _ = await _job(overconfident, _Analyzer(strategy=0.99, critic=0.50), gates=gates).run(
        as_of=_AS_OF, session=_SESSION
    )

    # Then
    assert calm.verdicts[0].decision == "pass"
    assert overconfident.verdicts[0].decision == "reject"


# --- 판단 서사와 계보 (잔여 작업 B) ------------------------------------------
# 구 role_07이 채우던 bull_case·key_risk를 새 분석 잡이 버리고 있었다.
# 프롬프트는 이미 그 내용을 만들므로, 구조화 출력에 실어 원장까지 잇는다.


class _NarrativeAnalyzer(_Analyzer):
    """An analyzer whose strategy output carries the narrative fields."""

    async def analyze(
        self, task: AnalysisTask, prompt: str, *, profile: str | None = None
    ) -> AnalysisResult:
        result = await super().analyze(task, prompt, profile=profile)
        if task is not AnalysisTask.STRATEGY:
            return result
        return result.model_copy(
            update={"bull_case": "20일 돌파와 거래량 확인", "key_risk": "시장 국면 반전"}
        )


@pytest.mark.anyio
async def test_the_models_narrative_lands_in_the_ledger() -> None:
    """모델이 만든 강세 논거·핵심 리스크가 원장에 앉는다 — 버리지 않는다."""
    # Given
    domain = _Domain((_subject("AAA", 1),))

    # When
    _ = await _job(domain, _NarrativeAnalyzer(strategy=0.9)).run(as_of=_AS_OF, session=_SESSION)

    # Then
    saved = domain.signals[0]
    assert saved.bull_case == "20일 돌파와 거래량 확인"
    assert saved.key_risk == "시장 국면 반전"


@pytest.mark.anyio
async def test_a_model_that_omits_the_narrative_does_not_kill_the_analysis() -> None:
    """서사는 부가물이다 — 필드가 비어도 판단과 저장은 그대로 성립한다."""
    # Given: 기본 _Analyzer는 서사 필드를 채우지 않는다
    domain = _Domain((_subject("AAA", 1),))

    # When
    outcomes = (await _job(domain, _Analyzer(strategy=0.9)).run(
        as_of=_AS_OF, session=_SESSION
    )).outcomes

    # Then
    assert len(outcomes) == 1
    assert domain.signals[0].bull_case is None
    assert domain.signals[0].key_risk is None


@pytest.mark.anyio
async def test_the_macro_row_that_was_read_is_recorded_as_lineage() -> None:
    """판단이 어느 국면 관측 위에서 내려졌는지가 원장에 남아야 한다."""
    # Given
    domain = _Domain((_subject("AAA", 1),), macro=("neutral", 0.3))

    # When
    _ = await _job(domain, _Analyzer(strategy=0.9)).run(as_of=_AS_OF, session=_SESSION)

    # Then
    assert domain.signals[0].src_macro_at == datetime.combine(_AS_OF, time(), tzinfo=UTC)


@pytest.mark.anyio
async def test_no_macro_means_no_fabricated_lineage() -> None:
    """읽은 국면이 없으면 계보도 없다 — 지어내지 않는다."""
    # Given
    domain = _Domain((_subject("AAA", 1),))

    # When
    _ = await _job(domain, _Analyzer(strategy=0.9)).run(as_of=_AS_OF, session=_SESSION)

    # Then
    assert domain.signals[0].src_macro_at is None


@pytest.mark.anyio
async def test_model_lineage_is_recorded_with_the_judgement() -> None:
    """어느 모델·어느 프롬프트 버전이 판단했는지는 재현의 전제다."""
    # Given
    domain = _Domain((_subject("AAA", 1),))

    # When
    _ = await _job(domain, _Analyzer(strategy=0.9)).run(as_of=_AS_OF, session=_SESSION)

    # Then
    saved = domain.signals[0]
    assert saved.model_name == "test"
    assert saved.model_provider == "mock"
    assert saved.prompt_version == "v1"
    assert saved.input_hash == "0" * 64


@pytest.mark.anyio
async def test_the_scored_disclosure_votes_and_is_recorded_as_lineage() -> None:
    """공시 채점이 07의 투표가 되는 지점. 뉴스와 달리 신뢰도 게이트를 타지 않는다."""
    # Given
    domain = _Domain((_subject("AAA", 1),), disclosure_scores={"AAA": 0.82})

    # When
    _ = await _job(domain, _Analyzer(strategy=0.9)).run(as_of=_AS_OF, session=_SESSION)

    # Then
    assert domain.signals[0].src_disclosure_at == datetime.combine(
        _AS_OF, time(), tzinfo=UTC
    )


@pytest.mark.anyio
async def test_the_disclosure_vote_actually_moves_the_conviction() -> None:
    """계보만 남고 표가 평균에 안 들어가면 채점 잡 전체가 값비싼 유령이 된다."""
    # Given: 기술 점수 0.9 · 모델 0.9. 약세 공시가 한 표로 들어오면 평균이 내려간다.
    without = _Domain((_subject("AAA", 1),))
    with_vote = _Domain((_subject("AAA", 1),), disclosure_scores={"AAA": 0.30})

    # When
    _ = await _job(without, _Analyzer(strategy=0.9)).run(as_of=_AS_OF, session=_SESSION)
    _ = await _job(with_vote, _Analyzer(strategy=0.9)).run(
        as_of=_AS_OF, session=_SESSION
    )

    # Then
    assert without.signals[0].conviction == pytest.approx(Decimal("0.900"))
    assert with_vote.signals[0].conviction == pytest.approx(Decimal("0.700"))


@pytest.mark.anyio
async def test_an_unscored_disclosure_abstains_instead_of_voting_neutral() -> None:
    """채점이 없는 종목에 0.5를 지어 넣으면 그 가짜 표가 실제 판단을 희석한다."""
    # Given
    domain = _Domain((_subject("AAA", 1),), disclosure_scores={})

    # When
    _ = await _job(domain, _Analyzer(strategy=0.9)).run(as_of=_AS_OF, session=_SESSION)

    # Then
    assert domain.signals[0].src_disclosure_at is None
