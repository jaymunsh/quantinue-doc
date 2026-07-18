"""Contract and hard-gate tests for pipeline roles 05 through 08."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from quantinue.core.contracts import PipelineContext, PipelineRequest
from quantinue.roles.role_05_disclosure_analysis.contracts import DisclosureSignal
from quantinue.roles.role_05_disclosure_analysis.service import FixtureSecDisclosureSource
from quantinue.roles.role_06_news_analysis.contracts import NewsSignal
from quantinue.roles.role_06_news_analysis.service import FixtureRssNewsSource
from quantinue.roles.role_07_strategist.contracts import StrategyInput, StrategyOutput
from quantinue.roles.role_08_critic.contracts import CriticInput, CriticVerdict

NOW = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)


def test_disclosure_signal_rejects_future_source_time() -> None:
    # Given / When / Then
    with pytest.raises(ValidationError, match="filed_at must not be after cycle_ts"):
        _ = DisclosureSignal.fixture(filed_at=NOW + timedelta(seconds=1), cycle_ts=NOW)


def test_news_signal_requires_source_lineage_when_signal_exists() -> None:
    # Given / When / Then
    with pytest.raises(ValidationError, match="source_ref is required"):
        _ = NewsSignal.fixture(source_ref=None)


def test_strategy_output_downgrades_buy_when_hard_blocked() -> None:
    # Given
    strategy_input = StrategyInput.fixture(disclosure_hard_blocked=True)

    # When
    output = StrategyOutput.from_model(strategy_input, conviction=0.99, summary="buy")

    # Then
    assert output.side == "hold"
    assert output.blockers == ("upstream_hard_block",)


def test_strategy_output_uses_injected_confidence_threshold() -> None:
    # Given
    strategy_input = StrategyInput.fixture()

    # When
    output = StrategyOutput.from_model(
        strategy_input, conviction=0.8, summary="buy", minimum_confidence=0.9
    )

    # Then
    assert output.side == "hold"


def test_strategy_output_model_rejects_phase_two_sell() -> None:
    # Given: an otherwise valid strategist result
    strategy_input = StrategyInput.fixture()

    # When / Then: model output requests the phase-two sell action
    with pytest.raises(ValidationError):
        _ = StrategyOutput.model_validate(
            {
                "run_id": strategy_input.run_id,
                "ticker": strategy_input.ticker,
                "cycle_ts": strategy_input.cycle_ts,
                "side": "sell",
                "conviction": 0.8,
                "summary": "phase two",
                "evidence_ids": strategy_input.evidence_ids,
                "gate_passed": True,
            }
        )


def test_critic_rejects_contradictory_lineage_without_llm() -> None:
    # Given
    critic_input = CriticInput.fixture(
        disclosure_filing_no="filing-1",
        news_disclosure_ref="filing-1",
    )

    # When
    verdict = CriticVerdict.apply_hard_gates(critic_input)

    # Then
    assert verdict is not None
    assert verdict.decision == "reject"
    assert verdict.category == "fake_consensus"


def test_critic_holds_stale_and_missing_event_time() -> None:
    # Given
    stale = CriticInput.fixture(news_published_at=NOW - timedelta(days=4), cycle_ts=NOW)
    missing = CriticInput.fixture(news_published_at=None, cycle_ts=NOW)

    # When
    stale_verdict = CriticVerdict.apply_hard_gates(stale)
    missing_verdict = CriticVerdict.apply_hard_gates(missing)

    # Then
    assert stale_verdict is not None
    assert stale_verdict.decision == "reject"
    assert missing_verdict is not None
    assert missing_verdict.decision == "hold"


def test_contracts_are_immutable() -> None:
    # Given
    signal = DisclosureSignal.fixture()

    # When / Then
    with pytest.raises(ValidationError):
        signal.confidence = 0.1


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"event_type": None}, "event_type is required"),
        ({"importance": None}, "importance is required"),
        ({"sentiment_score": None}, "sentiment_score is required"),
        ({"risk_score": None}, "risk_score is required"),
        ({"confidence": None}, "confidence is required"),
        ({"reason": None}, "reason is required"),
        ({"filing_no": None}, "filing_no is required"),
        ({"parent_evidence_ids": ()}, "parent_evidence_ids is required"),
        ({"parent_evidence_ids": ("other-run:sec",)}, "same run"),
    ],
)
def test_role05_rejects_incomplete_or_cross_run_lineage(
    changes: dict[str, str | tuple[str, ...] | None], message: str
) -> None:
    # Given / When / Then
    with pytest.raises(ValidationError, match=message):
        _ = DisclosureSignal.fixture(**changes)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"parent_evidence_ids": ()}, "parent_evidence_ids is required"),
        ({"parent_evidence_ids": ("other-run:rss",)}, "same run"),
        (
            {"is_hard_blocked": True, "hard_block_reason": None},
            "hard_block_reason is required",
        ),
        (
            {"is_hard_blocked": True, "hard_block_reason": "risk", "sentiment_score": 0.8},
            "hard-blocked news cannot be positive",
        ),
    ],
)
def test_role06_rejects_unsafe_or_cross_run_lineage(
    changes: dict[str, str | tuple[str, ...] | bool | float | None], message: str
) -> None:
    # Given / When / Then
    with pytest.raises(ValidationError, match=message):
        _ = NewsSignal.fixture(**changes)


def test_role07_rejects_cross_run_and_direct_buy_without_gate_proof() -> None:
    # Given / When / Then
    with pytest.raises(ValidationError, match="same run"):
        _ = StrategyInput.fixture(evidence_ids=("other-run:news",))


def test_role07_boundary_rejects_snapshot_older_than_five_minutes() -> None:
    # Given
    stale_at = NOW - timedelta(minutes=6)

    # When / Then
    with pytest.raises(ValidationError, match="snapshot exceeds five-minute freshness SLA"):
        _ = StrategyInput.fixture(cycle_ts=NOW, news_snapshot_at=stale_at)


@pytest.mark.parametrize(
    ("case", "changes", "error"),
    [
        ("valid", {}, None),
        ("missing", {"event_type": None}, "event_type is required"),
        ("stale", {"filed_at": NOW - timedelta(minutes=6)}, "freshness SLA"),
        ("future", {"filed_at": NOW + timedelta(seconds=1)}, "after cycle_ts"),
        (
            "contradictory",
            {"is_hard_blocked": True, "hard_block_reason": "risk", "sentiment_score": 0.8},
            "cannot be positive",
        ),
        ("cross-run", {"parent_evidence_ids": ("other:sec",)}, "same run"),
        ("missing-parent", {"parent_evidence_ids": ()}, "parent_evidence_ids"),
    ],
)
def test_role05_true_contract_matrix(
    case: str,
    changes: dict[str, str | datetime | bool | float | tuple[str, ...] | None],
    error: str | None,
) -> None:
    # Given / When / Then
    assert case
    if error is None:
        assert DisclosureSignal.fixture(**changes).has_signal
    else:
        with pytest.raises(ValidationError, match=error):
            _ = DisclosureSignal.fixture(**changes)


@pytest.mark.parametrize(
    ("case", "changes", "error"),
    [
        ("valid", {}, None),
        ("missing", {"source_ref": None}, "source_ref is required"),
        ("stale", {"published_at": NOW - timedelta(minutes=6)}, "freshness SLA"),
        ("future", {"published_at": NOW + timedelta(seconds=1)}, "after cycle_ts"),
        (
            "contradictory",
            {"is_hard_blocked": True, "hard_block_reason": "risk", "sentiment_score": 0.8},
            "cannot be positive",
        ),
        ("cross-run", {"parent_evidence_ids": ("other:rss",)}, "same run"),
        ("missing-parent", {"parent_evidence_ids": ()}, "parent_evidence_ids"),
    ],
)
def test_role06_true_contract_matrix(
    case: str,
    changes: dict[str, str | datetime | bool | float | tuple[str, ...] | None],
    error: str | None,
) -> None:
    # Given / When / Then
    assert case
    if error is None:
        assert NewsSignal.fixture(**changes).has_signal
    else:
        with pytest.raises(ValidationError, match=error):
            _ = NewsSignal.fixture(**changes)


@pytest.mark.parametrize(
    ("case", "changes", "error"),
    [
        ("valid", {}, None),
        ("missing", {"evidence_ids": ()}, "too_short"),
        ("stale", {"news_snapshot_at": NOW - timedelta(minutes=6)}, "freshness SLA"),
        ("future", {"news_snapshot_at": NOW + timedelta(seconds=1)}, "after cycle_ts"),
        (
            "contradictory",
            {"disclosure_hard_blocked": True, "news_hard_blocked": True},
            "contradictory upstream",
        ),
        ("cross-run", {"evidence_ids": ("other:news",)}, "same run"),
        ("missing-parent", {"evidence_ids": ()}, "too_short"),
    ],
)
def test_role07_true_contract_matrix(
    case: str,
    changes: dict[str, str | datetime | bool | float | tuple[str, ...]],
    error: str | None,
) -> None:
    # Given / When / Then
    assert case
    if error is None:
        assert StrategyInput.fixture(**changes).is_daily_pick
    else:
        with pytest.raises(ValidationError, match=error):
            _ = StrategyInput.fixture(**changes)


@pytest.mark.parametrize(
    ("case", "changes", "expected", "error"),
    [
        ("valid", {}, None, None),
        ("missing", {"evidence_ids": ()}, None, "too_short"),
        ("stale", {"news_published_at": NOW - timedelta(days=4)}, "reject", None),
        ("future", {"news_published_at": NOW + timedelta(seconds=1)}, None, "after cycle_ts"),
        (
            "contradictory",
            {"disclosure_filing_no": "same", "news_disclosure_ref": "same"},
            "reject",
            None,
        ),
        ("cross-run", {"evidence_ids": ("other:news",)}, None, "same run"),
        ("missing-parent", {"evidence_ids": ()}, None, "too_short"),
    ],
)
def test_role08_true_contract_matrix(
    case: str,
    changes: dict[str, str | datetime | float | tuple[str, ...] | None],
    expected: str | None,
    error: str | None,
) -> None:
    # Given / When / Then
    assert case
    if error is not None:
        with pytest.raises(ValidationError, match=error):
            _ = CriticInput.fixture(**changes)
    else:
        verdict = CriticVerdict.apply_hard_gates(CriticInput.fixture(**changes))
        assert (verdict.decision if verdict is not None else None) == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    "cycle_ts",
    [
        datetime(2024, 1, 2, 15, 0, tzinfo=UTC),
        datetime(2030, 12, 31, 20, 30, tzinfo=UTC),
    ],
)
async def test_fixture_sources_derive_event_time_from_each_requested_cycle(
    cycle_ts: datetime,
) -> None:
    # Given
    context = PipelineContext(request=PipelineRequest(ticker="NVDA", cycle_ts=cycle_ts))

    # When
    disclosure = await FixtureSecDisclosureSource().latest(context)
    news = await FixtureRssNewsSource().latest(context)

    # Then
    assert disclosure.filed_at is not None
    assert news.published_at is not None
    assert timedelta(0) <= cycle_ts - disclosure.filed_at <= timedelta(minutes=5)
    assert timedelta(0) <= cycle_ts - news.published_at <= timedelta(minutes=5)


def test_role08_direct_pass_requires_gate_layer_and_low_objection_confidence() -> None:
    # Given
    source = CriticInput.fixture()

    # When / Then
    with pytest.raises(ValidationError, match="pass requires gate proof"):
        _ = CriticVerdict(
            run_id=source.run_id,
            signal_id=source.signal_id,
            ticker=source.ticker,
            decision="pass",
            category=None,
            objection=None,
            confidence=0.8,
            decided_layer="llm",
            evidence_ids=source.evidence_ids,
        )
