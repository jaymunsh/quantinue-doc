"""Evidence traces remain linked across the offline 01-11 pipeline."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from quantinue.core.contracts import PipelineRequest, PipelineRun, RoleEvidenceTrace, RunId
from quantinue.orchestration.factory import build_default_orchestrator

NOW = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)


@pytest.mark.anyio
async def test_offline_pipeline_emits_one_linked_trace_per_role() -> None:
    # Given
    request = PipelineRequest(ticker="NVDA", cycle_ts=NOW)

    # When
    run = await build_default_orchestrator().run(request)

    # Then
    assert len(run.evidence_trace) == 11
    assert tuple(trace.component for trace in run.evidence_trace) == tuple(
        f"{component:02d}" for component in range(1, 12)
    )
    assert all(trace.run_id == run.run_id for trace in run.evidence_trace)
    assert run.evidence_trace[4].observed_at == NOW - timedelta(minutes=1)
    assert run.evidence_trace[5].observed_at == NOW - timedelta(minutes=1)
    assert all(trace.captured_at == NOW for trace in run.evidence_trace)
    assert run.evidence_trace[0].source == "market-fixture"
    assert run.evidence_trace[0].source_ref == "fixture://universe/NVDA"
    assert run.evidence_trace[4].source == "sec-edgar-fixture"
    assert run.evidence_trace[4].source_ref == "sec://filing/fixture-filing"
    assert run.evidence_trace[4].confidence == 0.9
    assert run.evidence_trace[5].source == "rss-fixture"
    assert run.evidence_trace[5].source_ref == "https://example.invalid/fixture-news"
    assert run.evidence_trace[5].confidence == 0.85
    assert run.evidence_trace[0].parent_evidence_ids == ()
    assert run.evidence_trace[3].parent_evidence_ids == ()


@pytest.mark.anyio
async def test_llm_stage_trace_preserves_redacted_model_lineage() -> None:
    # Given
    request = PipelineRequest(ticker="NVDA", cycle_ts=NOW)

    # When
    run = await build_default_orchestrator().run(request)

    # Then
    disclosure = run.evidence_trace[4]
    assert disclosure.model_name == "deterministic-mock-v1"
    assert disclosure.model_provider == "mock"
    assert disclosure.prompt_version
    assert disclosure.policy_version
    assert len(disclosure.input_hash or "") == 64
    dumped = disclosure.model_dump_json()
    assert "UNTRUSTED_EXTERNAL_DATA" not in dumped
    assert "Revenue increased" not in dumped


@pytest.mark.anyio
async def test_trace_uses_direct_parents_instead_of_all_prior_evidence() -> None:
    # Given
    request = PipelineRequest(ticker="NVDA", cycle_ts=NOW)

    # When
    run = await build_default_orchestrator().run(request)

    # Then
    assert run.evidence_trace[1].parent_evidence_ids == (run.evidence_trace[0].evidence_id,)
    assert run.evidence_trace[6].parent_evidence_ids == tuple(
        run.evidence_trace[index].evidence_id for index in (1, 4, 5)
    )
    assert run.evidence_trace[9].parent_evidence_ids == (run.evidence_trace[8].evidence_id,)
    assert run.evidence_trace[10].parent_evidence_ids == (run.evidence_trace[9].evidence_id,)


def test_pipeline_run_without_trace_remains_deserializable() -> None:
    # Given
    legacy_payload = (
        '{"run_id":"legacy-run","ticker":"NVDA",'
        '"cycle_ts":"2026-07-13T13:00:00Z","status":"completed","stages":[]}'
    )

    # When
    run = PipelineRun.model_validate_json(legacy_payload)

    # Then
    assert run.evidence_trace == ()


def test_legacy_trace_without_lineage_fields_remains_deserializable() -> None:
    # Given
    legacy_trace = (
        '{"run_id":"legacy-run","component":"01","source":"pipeline_role",'
        '"source_ref":"01:universe-screener",'
        '"observed_at":"2026-07-13T13:00:00Z",'
        '"captured_at":"2026-07-13T13:00:00Z","confidence":1.0}'
    )

    # When
    trace = RoleEvidenceTrace.model_validate_json(legacy_trace)

    # Then
    assert trace.evidence_id == ""
    assert trace.parent_evidence_ids == ()


def test_role_evidence_trace_is_strict_and_json_serializable() -> None:
    # Given
    trace = RoleEvidenceTrace(
        run_id=RunId("run-1"),
        component="01",
        source="pipeline_role",
        source_ref="01:universe-screener",
        observed_at=NOW,
        captured_at=NOW,
        confidence=1.0,
        evidence_id="run-1:role:01",
        parent_evidence_ids=(),
    )

    # When
    payload = trace.model_dump(mode="json")

    # Then
    assert payload["run_id"] == "run-1"
    assert payload["observed_at"] == "2026-07-13T13:00:00Z"
    with pytest.raises(ValidationError, match="frozen_instance"):
        trace.confidence = 0.5
