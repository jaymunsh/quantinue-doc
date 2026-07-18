"""Shared late-stage evidence boundary and pipeline-trace adapter."""

from datetime import timedelta

from pydantic import Field, model_validator

from quantinue.core.contracts import PipelineContext
from quantinue.core.ontology import EvidenceKind
from quantinue.core.schemas import AwareDateTime, ContractModel, Evidence


class LateStageEvidenceInput(ContractModel):
    """Fresh, coherent evidence required before sizing or submission."""

    run_id: str = Field(min_length=1)
    execution_at: AwareDateTime
    evidence: tuple[Evidence, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_coherent_evidence(self) -> "LateStageEvidenceInput":
        """Reject stale, future, cross-run, contradictory, or orphaned evidence."""
        by_id: dict[str, Evidence] = {}
        for item in self.evidence:
            if item.run_id != self.run_id:
                message = "evidence run_id does not match execution run"
                raise ValueError(message)
            if item.captured_at > self.execution_at:
                message = "future evidence is unavailable at execution time"
                raise ValueError(message)
            if self.execution_at - item.captured_at > timedelta(minutes=5):
                message = "stale evidence exceeds five-minute limit"
                raise ValueError(message)
            prior = by_id.get(item.evidence_id)
            if prior is not None and prior != item:
                message = "contradictory evidence uses the same evidence_id"
                raise ValueError(message)
            by_id[item.evidence_id] = item
        available = frozenset(by_id)
        missing_parent = any(
            parent not in available for item in self.evidence for parent in item.parent_evidence_ids
        )
        if missing_parent:
            message = "evidence lineage references an unavailable parent"
            raise ValueError(message)
        return self


def evidence_from_pipeline_traces(
    context: PipelineContext, components: tuple[str, ...]
) -> tuple[Evidence, ...]:
    """Convert completed role traces into an explicit ordered evidence chain."""
    by_id = {trace.evidence_id: trace for trace in context.evidence_trace}
    required = {
        trace.evidence_id for trace in context.evidence_trace if trace.component in components
    }
    pending = list(required)
    while pending:
        trace = by_id[pending.pop()]
        for parent_id in trace.parent_evidence_ids:
            if parent_id in by_id and parent_id not in required:
                required.add(parent_id)
                pending.append(parent_id)
    selected = tuple(trace for trace in context.evidence_trace if trace.evidence_id in required)
    return tuple(
        Evidence(
            evidence_id=trace.evidence_id,
            run_id=trace.run_id,
            source=trace.source,
            source_ref=trace.source_ref,
            observed_at=trace.observed_at,
            captured_at=trace.captured_at,
            confidence=trace.confidence,
            kind=EvidenceKind.MODEL_OUTPUT,
            parent_evidence_ids=tuple(
                parent for parent in trace.parent_evidence_ids if parent in required
            ),
            model_name=trace.model_name,
            model_provider=trace.model_provider,
            prompt_version=trace.prompt_version,
            policy_version=trace.policy_version,
            input_hash=trace.input_hash,
        )
        for trace in selected
    )
