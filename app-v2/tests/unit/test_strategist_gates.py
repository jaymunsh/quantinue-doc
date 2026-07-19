"""Role 07 defence gates: untrusted news, hostile macro, and hard negatives."""

from quantinue.orchestration.policy import GatesConfig, ProfileConfig
from quantinue.roles.role_07_strategist.contracts import StrategyInput, StrategyOutput

GATES = GatesConfig()
AGGRESSIVE = ProfileConfig()  # buy_threshold 0.65
CONSERVATIVE = ProfileConfig(buy_threshold=0.75)


def _input(**changes: object) -> StrategyInput:
    return StrategyInput.fixture(**changes)  # type: ignore[arg-type]


def test_untrusted_news_is_excluded_from_the_vote() -> None:
    # Given: a glowing news score from a source below the trust floor
    trusted = _input(news_score=0.9, source_trust=0.9)
    untrusted = _input(news_score=0.9, source_trust=0.54)

    # When
    with_trust = StrategyOutput.vote_conviction(trusted, GATES)
    without_trust = StrategyOutput.vote_conviction(untrusted, GATES)

    # Then: the low-trust story cannot lift conviction
    assert with_trust > without_trust


def test_source_trust_boundary_is_inclusive_at_the_floor() -> None:
    at_floor = StrategyOutput.vote_conviction(_input(news_score=0.9, source_trust=0.55), GATES)
    below = StrategyOutput.vote_conviction(_input(news_score=0.9, source_trust=0.549), GATES)

    assert at_floor > below


def test_macro_penalty_reduces_conviction_by_the_table() -> None:
    calm = StrategyOutput.vote_conviction(_input(macro_risk_score=0.0), GATES)
    hostile = StrategyOutput.vote_conviction(_input(macro_risk_score=0.90), GATES)

    assert round(calm - hostile, 3) == 0.30


def test_hard_negative_sentiment_blocks_a_buy() -> None:
    source = _input(disclosure_score=0.10)

    blockers = source.blockers(GATES)

    assert "hard_negative_sentiment" in blockers


def test_hard_negative_boundary_allows_exactly_the_threshold() -> None:
    assert "hard_negative_sentiment" not in _input(disclosure_score=0.151).blockers(GATES)
    assert "hard_negative_sentiment" in _input(disclosure_score=0.15).blockers(GATES)


def test_buy_threshold_is_profile_owned() -> None:
    source = _input(technical_score=0.72, disclosure_score=0.72, news_score=0.72, source_trust=0.9)
    conviction = StrategyOutput.vote_conviction(source, GATES)

    aggressive = StrategyOutput.from_model(
        source, conviction, "s", gates=GATES, profile=AGGRESSIVE
    )
    conservative = StrategyOutput.from_model(
        source, conviction, "s", gates=GATES, profile=CONSERVATIVE
    )

    # 같은 신호라도 성향에 따라 결론이 갈린다 (0.65 통과 / 0.75 미달).
    assert aggressive.side == "buy"
    assert conservative.side == "hold"


def test_buy_threshold_boundary_is_exact() -> None:
    source = _input()
    profile = ProfileConfig(buy_threshold=0.700)

    passing = StrategyOutput.from_model(source, 0.700, "s", gates=GATES, profile=profile)
    failing = StrategyOutput.from_model(source, 0.699, "s", gates=GATES, profile=profile)

    assert passing.side == "buy"
    assert failing.side == "hold"


def test_blocked_source_cannot_buy_regardless_of_conviction() -> None:
    source = _input(disclosure_hard_blocked=True)

    result = StrategyOutput.from_model(source, 0.99, "s", gates=GATES, profile=AGGRESSIVE)

    assert result.side == "hold"
    assert "upstream_hard_block" in result.blockers


def _held_input(technical: float) -> StrategyInput:
    """A ticker we own, still ranking well on the very score that got us in."""
    return _input(technical_score=technical, held_quantity=3)


def test_the_entry_ranking_does_not_dilute_the_case_for_selling() -> None:
    """실측으로 잡힌 결함: 상위 랭킹 보유는 **산술적으로** 팔 수 없었다.

    확신도가 (기술점수 + 모델점수)/2였고 약세 확신을 그 여집합으로 읽었다.
    기술 점수 0.95인 종목을 공격형 문턱(0.60)으로 팔려면 모델이 -0.15를 내야
    한다 — 불가능하다. 그런데 픽은 정의상 기술 점수 상위라 **매도 경로 전체가
    닫혀 있었다**. 실 LLM으로 -23% 포지션 3종목을 돌려 확인했다(약세 확신
    최대 0.447).
    """
    # Given: 모델은 강하게 약세, 기술 랭킹은 여전히 높다
    source = _held_input(0.95)

    # When
    bearishness = StrategyOutput.vote_bearishness(source, GatesConfig(), 0.15)

    # Then: 판단은 모델의 것이다 — 랭킹은 "지금 사기 좋은가"를 답하지
    # "계속 들고 있어야 하는가"를 답하지 않는다
    assert bearishness == 0.85


def test_a_worsening_macro_regime_counts_as_downside_evidence() -> None:
    """매크로 감점은 확신도에서 빼는 값이다 — 하방 판정에서는 더해야 대칭이다."""
    # Given
    source = _input(technical_score=0.5, held_quantity=3, macro_risk_score=0.95)

    # When
    bearishness = StrategyOutput.vote_bearishness(source, GatesConfig(), 0.5)

    # Then: 0.5 여집합 + 0.90 구간 감점(0.30)
    assert bearishness == 0.80


def test_without_a_model_score_the_ranking_is_all_we_have() -> None:
    """모델이 없는 경로(구 러너 회귀)에서도 답이 있어야 한다."""
    # Given / When
    bearishness = StrategyOutput.vote_bearishness(_held_input(0.2), GatesConfig(), None)

    # Then
    assert bearishness == 0.80


def test_both_personas_can_now_reach_a_sell_and_the_cautious_one_reaches_it_first() -> None:
    """성향 격차가 매도 방향에서도 살아 있어야 한다 — 지금까지는 둘 다 못 팔았다."""
    # Given: 모델이 0.45로 애매하게 약세 (약세 확신 0.55)
    source = _held_input(0.95)
    bearishness = StrategyOutput.vote_bearishness(source, GatesConfig(), 0.45)

    # When
    cautious = StrategyOutput.from_model(
        source, 0.7, "s", profile=ProfileConfig(sell_threshold=0.50), bearishness=bearishness
    )
    bold = StrategyOutput.from_model(
        source, 0.7, "s", profile=ProfileConfig(sell_threshold=0.60), bearishness=bearishness
    )

    # Then
    assert cautious.side == "sell"
    assert bold.side != "sell"
