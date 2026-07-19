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
