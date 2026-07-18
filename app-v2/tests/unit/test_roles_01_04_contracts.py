"""Contract and deterministic service tests for pipeline roles 01 through 04."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from pydantic_core import to_json

from quantinue.core.contracts import PipelineContext, PipelineRequest
from quantinue.core.ontology import Bucket, EvidenceKind, Regime
from quantinue.core.schemas import Evidence
from quantinue.db.codec import CONTEXT_ADAPTER, encode_context
from quantinue.roles.role_01_universe_screener.contracts import (
    EvidenceBoundInput,
    UniverseScreenerInput,
)
from quantinue.roles.role_01_universe_screener.service import UniverseScreener
from quantinue.roles.role_02_technical_analysis.contracts import TechnicalAnalysisInput
from quantinue.roles.role_02_technical_analysis.service import TechnicalAnalysis
from quantinue.roles.role_03_daily_screener.contracts import DailyPick, DailyScreenerInput
from quantinue.roles.role_03_daily_screener.service import DailyScreener
from quantinue.roles.role_04_macro_analysis.contracts import MacroAnalysisInput
from quantinue.roles.role_04_macro_analysis.service import MacroAnalysis

NOW = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)


def evidence(
    *, evidence_id: str = "e1", run_id: str = "run-1", age: timedelta | None = None
) -> Evidence:
    captured_at = NOW - (age or timedelta())
    return Evidence(
        evidence_id=evidence_id,
        run_id=run_id,
        source="fixture",
        source_ref=f"fixture://{evidence_id}",
        observed_at=captured_at,
        captured_at=captured_at,
        confidence=1.0,
        kind=EvidenceKind.MARKET_DATA,
    )


@pytest.mark.parametrize(
    "contract",
    [UniverseScreenerInput, TechnicalAnalysisInput, DailyScreenerInput, MacroAnalysisInput],
)
def test_each_role_contract_accepts_valid_evidence(
    contract: type[EvidenceBoundInput],
) -> None:
    # Given
    payload = {"run_id": "run-1", "execution_at": NOW, "evidence": (evidence(),)}

    # When
    result = contract.model_validate(payload)

    # Then
    assert result.run_id == "run-1"


@pytest.mark.parametrize(
    "contract",
    [UniverseScreenerInput, TechnicalAnalysisInput, DailyScreenerInput, MacroAnalysisInput],
)
def test_each_role_contract_rejects_missing_evidence(
    contract: type[EvidenceBoundInput],
) -> None:
    # Given
    payload = {"run_id": "run-1", "execution_at": NOW, "evidence": ()}

    # When / Then
    with pytest.raises(ValidationError, match="at least 1"):
        _ = contract.model_validate(payload)


@pytest.mark.parametrize(
    "contract",
    [UniverseScreenerInput, TechnicalAnalysisInput, DailyScreenerInput, MacroAnalysisInput],
)
def test_contract_rejects_stale_evidence_when_older_than_five_minutes(
    contract: type[EvidenceBoundInput],
) -> None:
    # Given
    stale = evidence(age=timedelta(minutes=5, microseconds=1))
    payload = {"run_id": "run-1", "execution_at": NOW, "evidence": (stale,)}

    # When / Then
    with pytest.raises(ValidationError, match="stale"):
        _ = contract.model_validate(payload)


@pytest.mark.parametrize(
    "contract",
    [UniverseScreenerInput, TechnicalAnalysisInput, DailyScreenerInput, MacroAnalysisInput],
)
def test_each_role_contract_rejects_future_evidence(
    contract: type[EvidenceBoundInput],
) -> None:
    # Given
    future_at = NOW + timedelta(seconds=1)
    future = evidence().model_copy(update={"captured_at": future_at, "observed_at": future_at})

    # When / Then
    with pytest.raises(ValidationError, match="future"):
        _ = contract(run_id="run-1", execution_at=NOW, evidence=(future,))


@pytest.mark.parametrize(
    "contract",
    [UniverseScreenerInput, TechnicalAnalysisInput, DailyScreenerInput, MacroAnalysisInput],
)
def test_each_role_contract_rejects_cross_run_evidence(
    contract: type[EvidenceBoundInput],
) -> None:
    # Given
    foreign = evidence(run_id="other-run")

    # When / Then
    with pytest.raises(ValidationError, match="run_id"):
        _ = contract(run_id="run-1", execution_at=NOW, evidence=(foreign,))


@pytest.mark.parametrize(
    "contract",
    [UniverseScreenerInput, TechnicalAnalysisInput, DailyScreenerInput, MacroAnalysisInput],
)
def test_each_role_contract_rejects_contradictory_evidence(
    contract: type[EvidenceBoundInput],
) -> None:
    # Given
    first = evidence()
    contradictory = first.model_copy(update={"source_ref": "fixture://different"})

    # When / Then
    with pytest.raises(ValidationError, match="contradictory"):
        _ = contract(run_id="run-1", execution_at=NOW, evidence=(first, contradictory))


@pytest.mark.parametrize(
    "contract",
    [UniverseScreenerInput, TechnicalAnalysisInput, DailyScreenerInput, MacroAnalysisInput],
)
def test_each_role_contract_rejects_missing_lineage_parent(
    contract: type[EvidenceBoundInput],
) -> None:
    # Given
    orphan = evidence().model_copy(update={"parent_evidence_ids": ("missing",)})

    # When / Then
    with pytest.raises(ValidationError, match="lineage"):
        _ = contract(run_id="run-1", execution_at=NOW, evidence=(orphan,))


def test_contract_rejects_future_evidence() -> None:
    # Given
    item = evidence()
    future_at = NOW + timedelta(seconds=1)
    future = item.model_copy(update={"captured_at": future_at, "observed_at": future_at})

    # When / Then
    with pytest.raises(ValidationError, match="future"):
        _ = MacroAnalysisInput(run_id="run-1", execution_at=NOW, evidence=(future,))


def test_contract_rejects_contradictory_duplicate_evidence() -> None:
    # Given
    first = evidence()
    contradictory = first.model_copy(update={"source_ref": "fixture://different"})

    # When / Then
    with pytest.raises(ValidationError, match="contradictory"):
        _ = TechnicalAnalysisInput(
            run_id="run-1", execution_at=NOW, evidence=(first, contradictory)
        )


def test_contract_rejects_cross_run_evidence() -> None:
    # Given
    foreign = evidence(run_id="other-run")

    # When / Then
    with pytest.raises(ValidationError, match="run_id"):
        _ = UniverseScreenerInput(run_id="run-1", execution_at=NOW, evidence=(foreign,))


def test_contract_rejects_missing_lineage_parent() -> None:
    # Given
    orphan = evidence().model_copy(update={"parent_evidence_ids": ("missing",)})

    # When / Then
    with pytest.raises(ValidationError, match="lineage"):
        _ = DailyScreenerInput(run_id="run-1", execution_at=NOW, evidence=(orphan,))


@pytest.mark.parametrize("score", [-0.01, 999.0])
def test_role03_rejects_score_outside_normalized_range(score: float) -> None:
    # Given
    payload = {
        "trade_date": NOW.date(),
        "ticker": "NVDA",
        "universe_as_of": NOW.date(),
        "bucket": Bucket.TREND_LEADER,
        "rank": 1,
        "sector": "Technology",
        "score": score,
        "evidence_ids": ("e1",),
    }

    # When / Then
    with pytest.raises(
        ValidationError,
        match=r"greater than or equal to 0|less than or equal to 1",
    ):
        _ = DailyPick.model_validate(payload)


@pytest.mark.anyio
async def test_fixture_services_preserve_pipeline_context_compatibility() -> None:
    # Given
    context = PipelineContext(request=PipelineRequest(ticker="nvda", cycle_ts=NOW))

    # When
    for service in (UniverseScreener(), TechnicalAnalysis(), DailyScreener(), MacroAnalysis()):
        context = await service.execute(context)

    # Then
    assert context.universe == ("NVDA",)
    assert context.technical_score == 0.82
    assert context.is_daily_pick is True
    assert context.macro_regime == Regime.NEUTRAL
    assert [stage.component for stage in context.stages] == ["01", "02", "03", "04"]


@pytest.mark.anyio
async def test_role02_output_survives_checkpoint_round_trip() -> None:
    # Given
    context = PipelineContext(request=PipelineRequest(ticker="NVDA", cycle_ts=NOW))
    context = await UniverseScreener().execute(context)
    context = await TechnicalAnalysis().execute(context)

    # When
    restored = CONTEXT_ADAPTER.validate_json(to_json(encode_context(context)))

    # Then
    assert restored.technical_output == context.technical_output
