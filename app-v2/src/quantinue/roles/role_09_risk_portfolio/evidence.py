"""The freshness and lineage boundary every sizing decision must clear.

구 11단계 러너의 트레이스를 증거로 바꾸던 어댑터(``evidence_from_pipeline_traces``)가
같이 있었는데, 그건 ``PipelineContext``를 받았으므로 러너와 함께 죽었다. 남은
``LateStageEvidenceInput``은 러너와 무관한 계약이다 — 배분 잡의
``RiskPortfolioInput``이 이것을 상속해 "5분 지난 증거로 주문 크기를 정하지
않는다"는 M4 방어선을 그대로 받는다.
"""

from datetime import timedelta

from pydantic import Field, model_validator

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
