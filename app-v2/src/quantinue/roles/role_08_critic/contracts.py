"""Immutable role 08 critic contracts and deterministic pre-model gates."""

# ruff: noqa: EM101, EM102, PLR0911, TRY003

from datetime import UTC, datetime, timedelta
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_PRICE_MOVE = 0.30
REJECTION_CONFIDENCE = 0.70


class ContractViolationError(ValueError):
    """Typed role-08 boundary contract failure."""


ContractViolation = ContractViolationError


class CriticInput(BaseModel):
    """Buy proposal plus independently traceable source facts."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", str_strip_whitespace=True)

    run_id: str = Field(min_length=1)
    signal_id: int = Field(gt=0)
    ticker: str = Field(min_length=1, max_length=12)
    cycle_ts: datetime
    side: Literal["buy"] = "buy"
    conviction: float = Field(ge=0, le=1)
    current_price: float = Field(gt=0)
    day_high: float = Field(gt=0)
    day_low: float = Field(gt=0)
    close_prev: float = Field(gt=0)
    macro_regime: Literal["risk_on", "neutral", "risk_off"] = "neutral"
    disclosure_filing_no: str | None = None
    disclosure_filed_at: datetime | None = None
    news_disclosure_ref: str | None = None
    news_published_at: datetime | None = None
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def prevent_future_information(self) -> Self:
        """Reject source events unavailable at the planned decision slot."""
        if self.cycle_ts.tzinfo is None:
            raise ContractViolation("cycle_ts must include a timezone")
        for name, timestamp in (
            ("disclosure_filed_at", self.disclosure_filed_at),
            ("news_published_at", self.news_published_at),
        ):
            if timestamp is not None and timestamp.tzinfo is None:
                raise ContractViolation(f"{name} must include a timezone")
            if timestamp is not None and timestamp.astimezone(UTC) > self.cycle_ts.astimezone(UTC):
                raise ContractViolation(f"{name} must not be after cycle_ts")
        if not self.evidence_ids:
            raise ContractViolation("evidence_ids must not be empty")
        if any(not item.startswith(f"{self.run_id}:") for item in self.evidence_ids):
            raise ContractViolation("evidence must belong to the same run")
        return self

    @classmethod
    def fixture(cls, **changes: str | datetime | float | tuple[str, ...] | None) -> Self:
        """Build a deterministic valid buy proposal fixture."""
        now = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)
        values = {
            "run_id": "fixture-run",
            "signal_id": 1,
            "ticker": "NVDA",
            "cycle_ts": now,
            "conviction": 0.8,
            "current_price": 128.4,
            "day_high": 130.0,
            "day_low": 126.0,
            "close_prev": 127.0,
            "disclosure_filing_no": "filing-a",
            "disclosure_filed_at": now - timedelta(hours=1),
            "news_published_at": now - timedelta(hours=1),
            "evidence_ids": (
                "fixture-run:strategy",
                "fixture-run:disclosure",
                "fixture-run:news",
            ),
        }
        return cls.model_validate({**values, **changes})


class CriticVerdict(BaseModel):
    """Auditable verdict with an explicit decision layer."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", str_strip_whitespace=True)

    run_id: str
    signal_id: int
    ticker: str
    decision: Literal["pass", "reject", "hold"]
    category: str | None
    objection: str | None
    confidence: float = Field(ge=0, le=1)
    decided_layer: Literal["quality_gate", "hard_rule", "llm", "gate"]
    source: Literal["fresh", "cache", "cooldown"] = "fresh"
    skipped_rules: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...]

    @model_validator(mode="after")
    def require_pass_gate_proof(self) -> Self:
        """Allow pass only as the low-confidence result of the final code gate."""
        if not self.evidence_ids:
            raise ContractViolation("verdict evidence_ids must not be empty")
        if self.decision == "pass" and (
            self.decided_layer != "gate" or self.confidence >= REJECTION_CONFIDENCE
        ):
            raise ContractViolation("pass requires gate proof and confidence below 0.70")
        return self

    @classmethod
    def apply_hard_gates(cls, source: CriticInput) -> Self | None:
        """Return a terminal verdict when quality, risk, or lineage blocks apply."""
        if (
            source.day_high < source.day_low
            or not source.day_low <= source.current_price <= source.day_high
        ):
            return cls(
                run_id=source.run_id,
                signal_id=source.signal_id,
                ticker=source.ticker,
                evidence_ids=source.evidence_ids,
                decision="hold",
                category="data_quality",
                objection="invalid price snapshot",
                confidence=1.0,
                decided_layer="quality_gate",
            )
        if abs(source.current_price - source.close_prev) / source.close_prev > MAX_PRICE_MOVE:
            return cls(
                run_id=source.run_id,
                signal_id=source.signal_id,
                ticker=source.ticker,
                evidence_ids=source.evidence_ids,
                decision="hold",
                category="data_quality",
                objection="price snapshot exceeds 30% range",
                confidence=1.0,
                decided_layer="quality_gate",
            )
        if source.macro_regime == "risk_off":
            return cls(
                run_id=source.run_id,
                signal_id=source.signal_id,
                ticker=source.ticker,
                evidence_ids=source.evidence_ids,
                decision="reject",
                category="macro_riskoff",
                objection="risk-off regime",
                confidence=1.0,
                decided_layer="hard_rule",
            )
        if (
            source.disclosure_filing_no
            and source.news_disclosure_ref == source.disclosure_filing_no
        ):
            return cls(
                run_id=source.run_id,
                signal_id=source.signal_id,
                ticker=source.ticker,
                evidence_ids=source.evidence_ids,
                decision="reject",
                category="fake_consensus",
                objection="news and disclosure share one source event",
                confidence=1.0,
                decided_layer="hard_rule",
            )
        event_times = (source.disclosure_filed_at, source.news_published_at)
        if any(timestamp is None for timestamp in event_times):
            return cls(
                run_id=source.run_id,
                signal_id=source.signal_id,
                ticker=source.ticker,
                evidence_ids=source.evidence_ids,
                decision="hold",
                category="stale",
                objection="event time unavailable",
                confidence=1.0,
                decided_layer="quality_gate",
            )
        if any(
            source.cycle_ts.astimezone(UTC) - timestamp.astimezone(UTC) > timedelta(days=3)
            for timestamp in event_times
            if timestamp is not None
        ):
            return cls(
                run_id=source.run_id,
                signal_id=source.signal_id,
                ticker=source.ticker,
                evidence_ids=source.evidence_ids,
                decision="reject",
                category="stale",
                objection="source evidence is stale",
                confidence=1.0,
                decided_layer="hard_rule",
            )
        return None


CriticOutput = CriticVerdict
