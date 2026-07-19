"""How many of the surviving signals actually agreed with the decision.

`signal_consensus` was written as a literal 0 for every row since the schema
existed, so the column looked like data while carrying none. It is recorded,
never gated on — M7's learning loop will read it, so it must be true.
"""

from quantinue.orchestration.policy import GatesConfig, ProfileConfig
from quantinue.roles.role_07_strategist.contracts import StrategyInput, StrategyOutput

GATES = GatesConfig()
PROFILE = ProfileConfig()  # buy_threshold 0.65


def _input(**changes: object) -> StrategyInput:
    return StrategyInput.fixture(**changes)  # type: ignore[arg-type]


def test_every_signal_clearing_the_threshold_is_counted() -> None:
    source = _input(technical_score=0.8, disclosure_score=0.8, news_score=0.8, source_trust=0.9)

    consensus = StrategyOutput.vote_consensus(source, GATES, PROFILE, model_score=0.8)

    assert consensus == 4


def test_a_signal_below_the_threshold_does_not_agree() -> None:
    source = _input(technical_score=0.8, disclosure_score=0.2, news_score=0.8, source_trust=0.9)

    assert StrategyOutput.vote_consensus(source, GATES, PROFILE, model_score=0.8) == 3


def test_a_vote_stripped_for_low_trust_cannot_agree() -> None:
    # The untrusted news score loses its vote, so it cannot count as consent.
    trusted = _input(technical_score=0.8, disclosure_score=0.8, news_score=0.9, source_trust=0.9)
    untrusted = _input(technical_score=0.8, disclosure_score=0.8, news_score=0.9, source_trust=0.4)

    assert StrategyOutput.vote_consensus(trusted, GATES, PROFILE) == 3
    assert StrategyOutput.vote_consensus(untrusted, GATES, PROFILE) == 2


def test_an_absent_disclosure_cannot_agree() -> None:
    source = _input(technical_score=0.8, disclosure_score=None, news_score=0.8, source_trust=0.9)

    assert StrategyOutput.vote_consensus(source, GATES, PROFILE) == 2


def test_unanimous_dissent_is_zero() -> None:
    source = _input(technical_score=0.1, disclosure_score=0.1, news_score=0.1, source_trust=0.9)

    assert StrategyOutput.vote_consensus(source, GATES, PROFILE, model_score=0.1) == 0


def test_consensus_never_exceeds_the_four_available_signals() -> None:
    # The schema check must admit a unanimous four; it was written for three.
    source = _input(technical_score=1.0, disclosure_score=1.0, news_score=1.0, source_trust=1.0)

    assert StrategyOutput.vote_consensus(source, GATES, PROFILE, model_score=1.0) == 4


def test_threshold_boundary_counts_as_agreement() -> None:
    at_threshold = _input(technical_score=0.65, disclosure_score=0.1, news_score=0.1)
    below = _input(technical_score=0.649, disclosure_score=0.1, news_score=0.1)

    assert StrategyOutput.vote_consensus(at_threshold, GATES, PROFILE) == 1
    assert StrategyOutput.vote_consensus(below, GATES, PROFILE) == 0
