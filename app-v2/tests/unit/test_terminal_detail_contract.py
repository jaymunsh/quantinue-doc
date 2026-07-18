from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from quantinue.core.contracts import PipelineRun, RunId, RunStatus, StageResult, StageStatus
from quantinue.core.terminal_detail import (
    CollectionFact,
    CriticDetail,
    StrategyDetail,
    TerminalRunDetail,
)

NOW = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)


def test_pipeline_run_json_round_trip_preserves_existing_terminal_fields() -> None:
    # Given
    run = PipelineRun(
        run_id=RunId("run-1"),
        ticker="NVDA",
        cycle_ts=NOW,
        status=RunStatus.COMPLETED,
        stages=(
            StageResult(
                component="01",
                name="universe",
                status=StageStatus.COMPLETED,
                summary="screened",
            ),
        ),
        conviction=0.8,
        side="buy",
    )

    # When
    restored = PipelineRun.model_validate_json(run.model_dump_json())

    # Then
    assert restored == run


def test_pipeline_run_json_round_trip_preserves_redacted_terminal_detail() -> None:
    # Given
    detail = TerminalRunDetail(
        disclosure=CollectionFact(
            title="10-Q filed",
            summary="Revenue grew year over year.",
            source="SEC EDGAR",
            reference="sec://filing/0001",
            score=0.8,
        ),
        news=CollectionFact(
            title="Product launch",
            summary="A new product was announced.",
            source="Newswire",
            reference="https://example.invalid/news/1",
            score=0.7,
        ),
        strategy=StrategyDetail(
            proposal="buy",
            rationale="Technical momentum and fundamentals align.",
            gate="eligible",
            blockers=(),
            conviction=0.82,
        ),
        critic=CriticDetail(
            verdict="pass",
            rationale="No risk limit is breached.",
            layer="model",
        ),
    )
    run = PipelineRun(
        run_id=RunId("run-1"),
        ticker="NVDA",
        cycle_ts=NOW,
        status=RunStatus.COMPLETED,
        stages=(),
        detail=detail,
    )

    # When
    restored = PipelineRun.model_validate_json(run.model_dump_json())

    # Then
    assert restored.detail == detail


def test_pipeline_run_legacy_json_defaults_terminal_detail_placeholders() -> None:
    # Given
    legacy_payload = (
        '{"run_id":"legacy-run","ticker":"NVDA",'
        '"cycle_ts":"2026-07-13T13:00:00Z","status":"failed","stages":[]}'
    )

    # When
    run = PipelineRun.model_validate_json(legacy_payload)

    # Then
    assert run.detail == TerminalRunDetail()
    assert run.detail.disclosure == CollectionFact()
    assert run.detail.news == CollectionFact()
    assert run.detail.strategy == StrategyDetail()
    assert run.detail.critic == CriticDetail()


@pytest.mark.parametrize("conviction", [-0.01, 1.01])
def test_strategy_detail_rejects_conviction_outside_unit_interval(conviction: float) -> None:
    # Given
    detail = {"conviction": conviction}

    # When / Then
    with pytest.raises(ValidationError):
        _ = StrategyDetail.model_validate(detail)


def test_terminal_detail_rejects_raw_payload_fields_and_oversized_display_text() -> None:
    # Given
    raw_detail = {
        "disclosure": {
            "title": "x" * 201,
            "raw_provider_payload": "provider response",
        },
    }

    # When / Then
    with pytest.raises(ValidationError):
        _ = TerminalRunDetail.model_validate(raw_detail)
