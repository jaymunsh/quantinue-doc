"""An absent disclosure abstains — it neither votes nor condemns.

Scoring a missing disclosure as 0.0 would trip `hard_negative_max` and block
every buy for a company that simply has no recent filing. This mirrors the
existing news rule: a signal that cannot be trusted loses its vote rather than
being counted as bad news.
"""

from quantinue.orchestration.policy import GatesConfig
from quantinue.roles.role_07_strategist.contracts import StrategyInput, StrategyOutput

GATES = GatesConfig()


def _input(**changes: object) -> StrategyInput:
    return StrategyInput.fixture(**changes)  # type: ignore[arg-type]


def test_absent_disclosure_is_excluded_from_the_vote() -> None:
    # Given: identical inputs, one with no disclosure at all
    present = _input(technical_score=0.8, disclosure_score=0.8, news_score=0.8, source_trust=0.9)
    absent = _input(technical_score=0.8, disclosure_score=None, news_score=0.8, source_trust=0.9)

    # When
    with_disclosure = StrategyOutput.vote_conviction(present, GATES)
    without_disclosure = StrategyOutput.vote_conviction(absent, GATES)

    # Then: abstention averages the survivors, it does not drag the mean to zero
    assert with_disclosure == 0.8
    assert without_disclosure == 0.8


def test_absent_disclosure_does_not_dilute_conviction_like_a_zero_would() -> None:
    strong = {"technical_score": 0.9, "news_score": 0.9, "source_trust": 0.9}
    absent = _input(disclosure_score=None, **strong)
    scored_zero = _input(disclosure_score=0.0, **strong)

    assert StrategyOutput.vote_conviction(absent, GATES) > StrategyOutput.vote_conviction(
        scored_zero, GATES
    )


def test_absent_disclosure_does_not_trip_the_hard_negative_gate() -> None:
    # Given: no disclosure versus a genuinely damning one
    absent = _input(disclosure_score=None)
    damning = _input(disclosure_score=0.10)

    # Then: only real bad news blocks the buy
    assert "hard_negative_sentiment" not in absent.blockers(GATES)
    assert "hard_negative_sentiment" in damning.blockers(GATES)


def test_absent_disclosure_still_allows_a_buy_when_other_signals_are_strong() -> None:
    source = _input(technical_score=0.9, disclosure_score=None, news_score=0.9, source_trust=0.9)
    conviction = StrategyOutput.vote_conviction(source, GATES)

    output = StrategyOutput.from_model(source, conviction, "strong technicals", gates=GATES)

    assert output.side == "buy"
