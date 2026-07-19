"""Typed contracts shared by all eleven pipeline roles."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum, unique
from typing import NewType
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from quantinue.core.context_detail import terminal_detail_from_context
from quantinue.core.ontology import ModelProvider
from quantinue.core.schemas import Evidence
from quantinue.core.terminal_detail import TerminalRunDetail
from quantinue.core.terminal_run_types import OrderResult, ReviewResult
from quantinue.llm.provider import AnalysisResult  # noqa: TC001
from quantinue.market_data.models import NewsMatchReason, NewsMatchStatus
from quantinue.roles.role_01_universe_screener.contracts import (  # noqa: TC001
    UniverseScreenerOutput,
)
from quantinue.roles.role_02_technical_analysis.contracts import (  # noqa: TC001
    TechnicalAnalysisOutput,
)
from quantinue.roles.role_03_daily_screener.contracts import (  # noqa: TC001
    DailyScreenerOutput,
)
from quantinue.roles.role_04_macro_analysis.contracts import (  # noqa: TC001
    MacroAnalysisOutput,
)
from quantinue.roles.role_05_disclosure_analysis.contracts import DisclosureSignal  # noqa: TC001
from quantinue.roles.role_06_news_analysis.contracts import NewsSignal  # noqa: TC001
from quantinue.roles.role_07_strategist.contracts import StrategyOutput  # noqa: TC001
from quantinue.roles.role_08_critic.contracts import CriticVerdict  # noqa: TC001

RunId = NewType("RunId", str)

__all__ = ("OrderResult", "ReviewResult")


@dataclass(frozen=True, slots=True)
class DisclosureSourceRecord:
    """Minimal SEC filing actually consumed by role 05."""

    filing_no: str
    title: str
    form_type: str
    filed_at: datetime
    event_type: str
    source_ref: str
    summary: str
    source: str = "sec-edgar"
    captured_at: datetime | None = None
    confidence: float = 1.0
    evidence_id: str = ""
    parent_evidence_ids: tuple[str, ...] = ()
    model_provider: ModelProvider = ModelProvider.MOCK
    model_name: str | None = None
    prompt_version: str | None = None
    policy_version: str | None = None
    input_hash: str | None = None


@dataclass(frozen=True, slots=True)
class NewsSourceRecord:
    """Minimal RSS item actually consumed by role 06."""

    news_key: str
    title: str
    url: str
    source: str
    published_at: datetime
    summary: str
    captured_at: datetime | None = None
    confidence: float = 1.0
    evidence_id: str = ""
    parent_evidence_ids: tuple[str, ...] = ()
    model_provider: ModelProvider = ModelProvider.MOCK
    model_name: str | None = None
    prompt_version: str | None = None
    policy_version: str | None = None
    input_hash: str | None = None
    selection_status: NewsMatchStatus = NewsMatchStatus.FETCHED
    relevance_score: int = 0
    relevance_reasons: tuple[NewsMatchReason, ...] = ()
    canonical_identity: str = ""


@unique
class RunStatus(StrEnum):
    """Pipeline run lifecycle."""

    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@unique
class StageStatus(StrEnum):
    """Role execution lifecycle."""

    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class PipelineRequest(BaseModel):
    """Boundary input for one ticker and one planned cycle slot."""

    model_config = ConfigDict(frozen=True)

    ticker: str = Field(min_length=1, max_length=12)
    cycle_ts: datetime
    automatic: bool = False

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        """Normalize ticker symbols once at the boundary."""
        return value.strip().upper()

    @field_validator("cycle_ts")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Reject ambiguous wall-clock values."""
        if value.tzinfo is None:
            msg = "cycle_ts must include a timezone"
            raise ValueError(msg)
        return value.astimezone(UTC)


class StageResult(BaseModel):
    """Observable result of one role."""

    model_config = ConfigDict(frozen=True)

    component: str
    name: str
    status: StageStatus
    summary: str


class RoleEvidenceTrace(BaseModel):
    """Deterministic provenance record linked to one completed role."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    run_id: RunId
    evidence_id: str = ""
    parent_evidence_ids: tuple[str, ...] = ()
    component: str = Field(pattern=r"^(0[1-9]|1[01])$")
    source: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    observed_at: datetime
    captured_at: datetime
    confidence: float = Field(ge=0.0, le=1.0)
    model_name: str | None = None
    model_provider: ModelProvider | None = None
    prompt_version: str | None = None
    policy_version: str | None = None
    input_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("observed_at", "captured_at")
    @classmethod
    def require_trace_timezone(cls, value: datetime) -> datetime:
        """Normalize trace timestamps while rejecting ambiguous wall-clock values."""
        if value.tzinfo is None:
            msg = "evidence trace timestamps must include a timezone"
            raise ValueError(msg)
        return value.astimezone(UTC)


class PipelineRun(BaseModel):
    """API and persistence representation of a completed pipeline run."""

    model_config = ConfigDict(frozen=True)

    run_id: RunId
    ticker: str
    cycle_ts: datetime
    status: RunStatus
    stages: tuple[StageResult, ...]
    evidence_trace: tuple[RoleEvidenceTrace, ...] = ()
    conviction: float | None = None
    side: str | None = None
    account_id: int | None = Field(default=None, gt=0)
    detail: TerminalRunDetail = Field(default_factory=TerminalRunDetail)
    order: OrderResult | None = None
    review: ReviewResult | None = None
    automatic: bool = False
    candidate_rank: int | None = Field(default=None, ge=1, le=50)


@dataclass(frozen=True, slots=True)
class PipelineContext:
    """Internal immutable state passed through roles 01 to 11."""

    request: PipelineRequest
    run_id: RunId = field(default_factory=lambda: RunId(uuid4().hex))
    stages: tuple[StageResult, ...] = ()
    evidence_trace: tuple[RoleEvidenceTrace, ...] = ()
    universe: tuple[str, ...] = ()
    universe_output: UniverseScreenerOutput | None = None
    technical_output: TechnicalAnalysisOutput | None = None
    daily_screener_output: DailyScreenerOutput | None = None
    macro_output: MacroAnalysisOutput | None = None
    last_price: float | None = None
    technical_score: float | None = None
    is_daily_pick: bool = False
    macro_regime: str | None = None
    macro_risk_score: float | None = None
    disclosure_score: float | None = None
    news_score: float | None = None
    disclosure_source: DisclosureSourceRecord | None = None
    news_source: NewsSourceRecord | None = None
    disclosure_sources: tuple[DisclosureSourceRecord, ...] = ()
    news_sources: tuple[NewsSourceRecord, ...] = ()
    disclosure_output: DisclosureSignal | None = None
    news_output: NewsSignal | None = None
    disclosure_analysis: AnalysisResult | None = None
    news_analysis: AnalysisResult | None = None
    conviction: float | None = None
    side: str | None = None
    strategy_output: StrategyOutput | None = None
    critic_approved: bool = False
    critic_verdict: CriticVerdict | None = None
    risk_decision: str | None = None
    risk_skipped_reason: str | None = None
    risk_entry_price: float | None = None
    signal_id: int | None = None
    account_id: int | None = None
    quantity: int | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    order: OrderResult | None = None
    review: ReviewResult | None = None
    candidate_rank: int | None = None

    def add_stage(
        self, component: str, name: str, summary: str, *, evidence: Evidence | None = None
    ) -> PipelineContext:
        """Return a new context with a completed stage appended."""
        stage = StageResult(
            component=component,
            name=name,
            status=StageStatus.COMPLETED,
            summary=summary,
        )
        selected = evidence or Evidence(
            evidence_id=f"{self.run_id}:{component}:code-result",
            run_id=self.run_id,
            source="role-code",
            source_ref=f"policy://role/{component}",
            observed_at=self.request.cycle_ts,
            captured_at=self.request.cycle_ts,
            confidence=1.0,
            parent_evidence_ids=(self.evidence_trace[-1].evidence_id,)
            if self.evidence_trace
            else (),
        )
        trace = RoleEvidenceTrace(
            run_id=self.run_id,
            evidence_id=selected.evidence_id,
            parent_evidence_ids=selected.parent_evidence_ids,
            component=component,
            source=selected.source,
            source_ref=selected.source_ref,
            observed_at=selected.observed_at,
            captured_at=selected.captured_at,
            confidence=selected.confidence,
            model_name=selected.model_name,
            model_provider=selected.model_provider,
            prompt_version=selected.prompt_version,
            policy_version=selected.policy_version,
            input_hash=selected.input_hash,
        )
        return replace(
            self,
            stages=(*self.stages, stage),
            evidence_trace=(*self.evidence_trace, trace),
        )

    def to_run(self) -> PipelineRun:
        """Convert internal state to its persisted boundary model."""
        return PipelineRun(
            run_id=self.run_id,
            ticker=self.request.ticker,
            cycle_ts=self.request.cycle_ts,
            status=RunStatus.COMPLETED,
            stages=self.stages,
            evidence_trace=self.evidence_trace,
            conviction=self.conviction,
            side=self.side,
            account_id=self.account_id,
            detail=terminal_detail_from_context(self),
            order=self.order,
            review=self.review,
            automatic=self.request.automatic,
            candidate_rank=self.candidate_rank,
        )
