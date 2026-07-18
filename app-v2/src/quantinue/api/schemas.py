"""FastAPI request, response, and redacted control-room schemas."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from quantinue.core.contracts import RunId, RunStatus, StageStatus
from quantinue.core.ontology import ModelProvider
from quantinue.market_data.models import NewsMatchStatus


class RunCreate(BaseModel):
    """Request to execute one pipeline cycle now."""

    model_config = ConfigDict(frozen=True)

    ticker: str = Field(default="NVDA", pattern=r"^[A-Z0-9.-]{1,12}$")

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        """Strip benign edge whitespace before enforcing the canonical alphabet."""
        return value.strip()


class HealthResponse(BaseModel):
    """Safe runtime mode summary."""

    model_config = ConfigDict(frozen=True)

    status: str
    broker_mode: str
    llm_mode: str


class AsyncRunStart(BaseModel):
    """Safe acknowledgement for an accepted asynchronous pipeline launch."""

    model_config = ConfigDict(frozen=True)

    accepted: bool
    ticker: str
    cycle_ts: datetime


class AttemptView(BaseModel):
    """One safe execution attempt without raw provider error content."""

    model_config = ConfigDict(frozen=True)

    attempt_no: int = Field(gt=0)
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None = Field(default=None, ge=0)
    failure_code: str | None = None


class StageView(BaseModel):
    """Stage state, timing, and durable-checkpoint summary."""

    model_config = ConfigDict(frozen=True)

    component: str
    name: str
    status: StageStatus
    summary: str
    attempts: tuple[AttemptView, ...]
    duration_ms: int | None = Field(default=None, ge=0)
    checkpointed: bool
    failure_code: str | None = None


class EvidenceView(BaseModel):
    """Source-addressable evidence and its parent lineage."""

    model_config = ConfigDict(frozen=True)

    evidence_id: str
    component: str
    source: str
    source_ref: str
    observed_at: datetime
    captured_at: datetime
    confidence: float = Field(ge=0, le=1)
    parent_evidence_ids: tuple[str, ...]
    model_name: str | None = None
    model_provider: ModelProvider | None = None
    prompt_version: str | None = None
    policy_version: str | None = None
    input_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class OrderView(BaseModel):
    """Idempotency and reconciliation-safe order summary."""

    model_config = ConfigDict(frozen=True)

    order_id: str
    client_order_id: str
    reconciliation_status: str
    quantity: int
    filled_avg_price: float


class ReviewView(BaseModel):
    """T+5 review summary linked to the pipeline run."""

    model_config = ConfigDict(frozen=True)

    outcome: str
    summary: str


class LiveStageView(BaseModel):
    """Canonical current or next stage for a running control-room projection."""

    model_config = ConfigDict(frozen=True)

    component: str
    name: str
    status: StageStatus


class SourceReferenceView(BaseModel):
    """Readable source reference with an optional validated browser destination."""

    model_config = ConfigDict(frozen=True)

    label: str = Field(max_length=4_096)
    href: str | None = Field(default=None, max_length=4_096)


class CollectionDetailView(BaseModel):
    """Safe collection fact retained for the administrator detail panel."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(max_length=200)
    summary: str = Field(max_length=1_000)
    source: str = Field(max_length=120)
    reference: SourceReferenceView
    score: float | None = Field(default=None, ge=0, le=1)


class StrategyDetailView(BaseModel):
    """Safe strategist decision facts without model inputs or provider payloads."""

    model_config = ConfigDict(frozen=True)

    proposal: str = Field(max_length=64)
    rationale: str = Field(max_length=1_000)
    gate: str = Field(max_length=64)
    blockers: tuple[str, ...] = Field(max_length=12)
    conviction: float | None = Field(default=None, ge=0, le=1)


class CriticDetailView(BaseModel):
    """Safe critic verdict facts without raw exception or provider detail."""

    model_config = ConfigDict(frozen=True)

    verdict: str = Field(max_length=64)
    rationale: str = Field(max_length=1_000)
    layer: str = Field(max_length=64)


class NewsSelectionItemView(BaseModel):
    """Safe ticker-news selection row."""

    model_config = ConfigDict(frozen=True)

    status: NewsMatchStatus
    is_representative: bool
    score: int = Field(ge=0)
    reasons: tuple[str, ...]
    relevance_evaluated: bool
    representative_label: str
    representative_explanation: str
    title: str
    published_at: str
    reference: SourceReferenceView


class NewsSelectionView(BaseModel):
    """Exact Role 06 selection counts and rows."""

    model_config = ConfigDict(frozen=True)

    fetched_count: int = Field(ge=0)
    relevant_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    representative_count: int = Field(ge=0, le=1)
    items: tuple[NewsSelectionItemView, ...]


class RoleDetailView(BaseModel):
    """One bounded 01--11 result block for administrator inspection."""

    model_config = ConfigDict(frozen=True)

    component: str = Field(max_length=64)
    title: str = Field(max_length=200)
    description: str = Field(max_length=600)
    status: str = Field(max_length=64)
    summary: str = Field(max_length=1_000)
    facts: tuple[tuple[str, str], ...] = Field(max_length=24)
    items: tuple[str, ...]
    news_selection: NewsSelectionView | None = None


class PortfolioAccountView(BaseModel):
    """Local simulated account totals."""

    model_config = ConfigDict(frozen=True)

    opening_cash: Decimal
    current_cash: Decimal
    equity: Decimal
    buying_power: Decimal
    currency: str


class PortfolioPositionView(BaseModel):
    """Marked local simulated holding."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    quantity: int = Field(gt=0)
    average_cost: Decimal
    mark_price: Decimal
    mark_source: str
    mark_as_of: datetime
    market_value: Decimal
    unrealized_pnl: Decimal
    allocation: Decimal


class SimulatedOrderView(BaseModel):
    """Local simulated order row."""

    model_config = ConfigDict(frozen=True)

    order_id: str
    ticker: str
    quantity: int = Field(gt=0)
    reference_price: Decimal
    status: str
    created_at: datetime


class SimulatedFillView(BaseModel):
    """Local simulated fill row."""

    model_config = ConfigDict(frozen=True)

    fill_id: str
    order_id: str
    ticker: str
    quantity: int = Field(gt=0)
    price: Decimal
    filled_at: datetime


class SimulatedPortfolioView(BaseModel):
    """Local buy-only portfolio projection."""

    model_config = ConfigDict(frozen=True)

    account: PortfolioAccountView
    positions: tuple[PortfolioPositionView, ...]
    orders: tuple[SimulatedOrderView, ...]
    fills: tuple[SimulatedFillView, ...]
    realized_pnl_label: str


class TerminalRunDetailView(BaseModel):
    """Redacted structured detail for one terminal pipeline run."""

    model_config = ConfigDict(frozen=True)

    disclosure: CollectionDetailView
    news: CollectionDetailView
    strategy: StrategyDetailView
    critic: CriticDetailView
    roles: tuple[RoleDetailView, ...] = Field(max_length=11)


class ControlRoomRun(BaseModel):
    """Complete redacted observability projection used by API and HTML."""

    model_config = ConfigDict(frozen=True)

    run_id: RunId
    ticker: str
    cycle_ts: datetime
    status: RunStatus
    progress: int = Field(ge=0, le=11)
    current_stage: LiveStageView | None
    next_stage: LiveStageView | None
    stages: tuple[StageView, ...]
    evidence: tuple[EvidenceView, ...]
    conviction: float | None
    side: str | None
    detail: TerminalRunDetailView
    order: OrderView | None
    review: ReviewView | None
