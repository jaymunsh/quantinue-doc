"""Immutable role 05 disclosure boundary contracts."""

# ruff: noqa: C901, EM101, EM102, PLR0912, TRY003

from datetime import UTC, datetime, timedelta
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from quantinue.core.ontology import EventType

NEUTRAL_SENTIMENT = 0.5


class ContractViolationError(ValueError):
    """Typed role-05 boundary contract failure."""


ContractViolation = ContractViolationError


class DisclosureSignal(BaseModel):
    """Packed SEC signal consumed by roles 07 and 08."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", str_strip_whitespace=True)

    run_id: str = Field(min_length=1)
    cycle_ts: datetime
    has_signal: bool
    event_type: EventType | None = None
    filed_at: datetime | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    sentiment_score: float | None = Field(default=None, ge=0, le=1)
    risk_score: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason: str | None = None
    is_hard_blocked: bool = False
    hard_block_reason: str | None = None
    summary: str | None = None
    filing_no: str | None = None
    source: str = "sec-edgar"
    source_ref: str | None = None
    parent_evidence_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_lineage(self) -> Self:
        """Require complete, time-safe lineage for actionable signals."""
        cycle_ts = self.cycle_ts.astimezone(UTC)
        if self.cycle_ts.tzinfo is None:
            raise ContractViolation("cycle_ts must include a timezone")
        if self.filed_at is not None:
            if self.filed_at.tzinfo is None:
                raise ContractViolation("filed_at must include a timezone")
            if self.filed_at.astimezone(UTC) > cycle_ts:
                raise ContractViolation("filed_at must not be after cycle_ts")
            if cycle_ts - self.filed_at.astimezone(UTC) > timedelta(minutes=5):
                raise ContractViolation("disclosure evidence exceeds five-minute freshness SLA")
        if self.has_signal and self.filed_at is None:
            raise ContractViolation("filed_at is required when has_signal is true")
        if self.has_signal and not self.source_ref:
            raise ContractViolation("source_ref is required when has_signal is true")
        required = (
            ("event_type", self.event_type),
            ("importance", self.importance),
            ("sentiment_score", self.sentiment_score),
            ("risk_score", self.risk_score),
            ("confidence", self.confidence),
            ("reason", self.reason),
            ("filing_no", self.filing_no),
        )
        for name, value in required:
            if self.has_signal and value is None:
                raise ContractViolation(f"{name} is required when has_signal is true")
        if self.has_signal and not self.parent_evidence_ids:
            raise ContractViolation("parent_evidence_ids is required when has_signal is true")
        if any(not item.startswith(f"{self.run_id}:") for item in self.parent_evidence_ids):
            raise ContractViolation("parent evidence must belong to the same run")
        if self.is_hard_blocked and not self.hard_block_reason:
            raise ContractViolation("hard_block_reason is required when blocked")
        if (
            self.is_hard_blocked
            and self.sentiment_score is not None
            and self.sentiment_score > NEUTRAL_SENTIMENT
        ):
            raise ContractViolation("hard-blocked disclosure cannot be positive")
        return self

    @classmethod
    def fixture(cls, **changes: str | datetime | bool | float | tuple[str, ...] | None) -> Self:
        """Build the stable offline SEC fixture."""
        values = {
            "run_id": "fixture-run",
            "cycle_ts": datetime(2026, 7, 13, 13, 0, tzinfo=UTC),
            "has_signal": True,
            "event_type": "earnings",
            "filed_at": datetime(2026, 7, 13, 12, 59, tzinfo=UTC),
            "importance": 0.8,
            "sentiment_score": 0.78,
            "risk_score": 0.1,
            "confidence": 0.9,
            "reason": "Revenue increased and guidance was maintained.",
            "summary": "Quarterly results exceeded expectations.",
            "filing_no": "fixture-filing",
            "source_ref": "sec://filing/fixture-filing",
            "parent_evidence_ids": ("fixture-run:sec-fixture",),
        }
        return cls.model_validate({**values, **changes})


DisclosureAnalysisInput = DisclosureSignal
DisclosureAnalysisOutput = DisclosureSignal
