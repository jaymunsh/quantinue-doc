"""Immutable input and output contracts for role 01."""

from datetime import date, timedelta

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from quantinue.core.schemas import AwareDateTime, ContractModel, Evidence


class EvidenceBoundInput(ContractModel):
    """Common evidence-lineage boundary used by deterministic roles."""

    run_id: str = Field(min_length=1)
    execution_at: AwareDateTime
    evidence: tuple[Evidence, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_coherent_evidence(self) -> "EvidenceBoundInput":
        """Reject unavailable, stale, cross-run, or contradictory evidence."""
        by_id: dict[str, Evidence] = {}
        for item in self.evidence:
            if item.run_id != self.run_id:
                code = "evidence_run_mismatch"
                message = "evidence run_id does not match execution run"
                raise PydanticCustomError(code, message)
            if item.captured_at > self.execution_at:
                code = "future_evidence"
                message = "future evidence is unavailable at execution time"
                raise PydanticCustomError(code, message)
            if self.execution_at - item.captured_at > timedelta(minutes=5):
                code = "stale_evidence"
                message = "stale evidence exceeds five-minute limit"
                raise PydanticCustomError(code, message)
            prior = by_id.get(item.evidence_id)
            if prior is not None and prior != item:
                code = "contradictory_evidence"
                message = "contradictory evidence uses the same evidence_id"
                raise PydanticCustomError(code, message)
            by_id[item.evidence_id] = item
        available = frozenset(by_id)
        missing_parent = any(
            parent not in available for item in self.evidence for parent in item.parent_evidence_ids
        )
        if missing_parent:
            code = "missing_evidence_parent"
            message = "evidence lineage references an unavailable parent"
            raise PydanticCustomError(code, message)
        return self


class ListedSecurity(ContractModel):
    """One raw US-listed instrument from the free screener feed."""

    ticker: str = Field(min_length=1, max_length=12)
    company_name: str = Field(min_length=1)
    market_cap: int = Field(gt=0)
    security_type: str = Field(min_length=1)


class UniverseScreenerInput(EvidenceBoundInput):
    """Role 01 boundary for a weekly listing snapshot."""

    as_of_date: date | None = None
    listings: tuple[ListedSecurity, ...] = ()


class UniverseMember(ContractModel):
    """Persistable role 01 universe row."""

    as_of_date: date
    ticker: str = Field(min_length=1, max_length=12)
    company_name: str = Field(min_length=1)
    market_cap: int = Field(gt=0)
    evidence_ids: tuple[str, ...] = Field(min_length=1)


class UniverseScreenerOutput(ContractModel):
    """Top common-stock universe with its execution identity."""

    run_id: str = Field(min_length=1)
    generated_at: AwareDateTime
    members: tuple[UniverseMember, ...] = Field(max_length=2000)
