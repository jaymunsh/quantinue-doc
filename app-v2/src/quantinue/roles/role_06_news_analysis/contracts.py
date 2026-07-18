"""Immutable role 06 RSS news boundary contracts."""

# ruff: noqa: C901, EM101, TRY003

from datetime import UTC, datetime, timedelta
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from quantinue.core.ontology import EventType

NEUTRAL_SENTIMENT = 0.5


class ContractViolationError(ValueError):
    """Typed role-06 boundary contract failure."""


ContractViolation = ContractViolationError


class NewsSignal(BaseModel):
    """Packed title-plus-RSS-snippet signal for downstream decisions."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", str_strip_whitespace=True)

    run_id: str = Field(min_length=1)
    cycle_ts: datetime
    has_signal: bool
    news_count: int = Field(default=0, ge=0)
    event_type: EventType | None = None
    published_at: datetime | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    peak_importance: float | None = Field(default=None, ge=0, le=1)
    sentiment_score: float | None = Field(default=None, ge=0, le=1)
    risk_score: float | None = Field(default=None, ge=0, le=1)
    source_trust: float | None = Field(default=None, ge=0, le=1)
    grade_score: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    disclosure_ref: str | None = None
    reason: str | None = None
    is_hard_blocked: bool = False
    hard_block_reason: str | None = None
    summary: str | None = None
    source: str = Field(min_length=1)
    source_ref: str | None = None
    parent_evidence_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_lineage(self) -> Self:
        """Reject future events and untraceable actionable news."""
        if self.cycle_ts.tzinfo is None:
            raise ContractViolation("cycle_ts must include a timezone")
        if self.published_at is not None:
            if self.published_at.tzinfo is None:
                raise ContractViolation("published_at must include a timezone")
            if self.published_at.astimezone(UTC) > self.cycle_ts.astimezone(UTC):
                raise ContractViolation("published_at must not be after cycle_ts")
            if self.cycle_ts.astimezone(UTC) - self.published_at.astimezone(UTC) > timedelta(
                minutes=5
            ):
                raise ContractViolation("news evidence exceeds five-minute freshness SLA")
        if self.has_signal and self.published_at is None:
            raise ContractViolation("published_at is required when has_signal is true")
        if self.has_signal and not self.source_ref:
            raise ContractViolation("source_ref is required when has_signal is true")
        if self.has_signal and self.news_count == 0:
            raise ContractViolation("news_count must be positive when has_signal is true")
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
            raise ContractViolation("hard-blocked news cannot be positive")
        return self

    @classmethod
    def fixture(cls, **changes: str | datetime | bool | float | tuple[str, ...] | None) -> Self:
        """Build the stable offline RSS fixture."""
        values = {
            "run_id": "fixture-run",
            "cycle_ts": datetime(2026, 7, 13, 13, 0, tzinfo=UTC),
            "has_signal": True,
            "news_count": 2,
            "event_type": "product_deal",
            "published_at": datetime(2026, 7, 13, 12, 59, tzinfo=UTC),
            "importance": 0.7,
            "peak_importance": 0.8,
            "sentiment_score": 0.74,
            "risk_score": 0.1,
            "source_trust": 0.9,
            "grade_score": 1.0,
            "confidence": 0.85,
            "reason": "Multiple trusted reports confirm demand growth.",
            "summary": "AI accelerator orders expanded.",
            "source": "reuters.com",
            "source_ref": "https://example.invalid/fixture-news",
            "parent_evidence_ids": ("fixture-run:rss-fixture",),
        }
        return cls.model_validate({**values, **changes})


NewsAnalysisInput = NewsSignal
NewsAnalysisOutput = NewsSignal
