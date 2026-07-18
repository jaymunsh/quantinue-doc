"""Immutable role 07 strategy boundary contracts and code gates."""

# ruff: noqa: EM101, EM102, TRY003

from datetime import UTC, datetime, timedelta
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

SnapshotMaxAge = timedelta(minutes=5)
MIN_CONVICTION = 0.6


class ContractViolationError(ValueError):
    """Typed role-07 boundary contract failure."""


ContractViolation = ContractViolationError


class StrategyInput(BaseModel):
    """Normalized upstream facts presented to the strategist."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", str_strip_whitespace=True)

    run_id: str = Field(min_length=1)
    ticker: str = Field(min_length=1, max_length=12)
    cycle_ts: datetime
    technical_score: float = Field(ge=0, le=1)
    disclosure_score: float = Field(ge=0, le=1)
    news_score: float = Field(ge=0, le=1)
    is_daily_pick: bool
    disclosure_hard_blocked: bool = False
    news_hard_blocked: bool = False
    disclosure_snapshot_at: datetime
    news_snapshot_at: datetime
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_future_snapshots(self) -> Self:
        """Prevent future information from entering a strategy decision."""
        if self.cycle_ts.tzinfo is None:
            raise ContractViolation("cycle_ts must include a timezone")
        for name, timestamp in (
            ("disclosure_snapshot_at", self.disclosure_snapshot_at),
            ("news_snapshot_at", self.news_snapshot_at),
        ):
            if timestamp.tzinfo is None:
                raise ContractViolation(f"{name} must include a timezone")
            if timestamp.astimezone(UTC) > self.cycle_ts.astimezone(UTC):
                raise ContractViolation(f"{name} must not be after cycle_ts")
            if self.cycle_ts.astimezone(UTC) - timestamp.astimezone(UTC) > SnapshotMaxAge:
                raise PydanticCustomError(
                    "stale_snapshot",
                    "snapshot exceeds five-minute freshness SLA",
                )
        if any(not item.startswith(f"{self.run_id}:") for item in self.evidence_ids):
            raise ContractViolation("evidence must belong to the same run")
        if self.disclosure_hard_blocked and self.news_hard_blocked:
            raise ContractViolation("contradictory upstream state: both sources hard-blocked")
        return self

    @classmethod
    def fixture(cls, **changes: str | datetime | bool | float | tuple[str, ...]) -> Self:
        """Build an offline input whose snapshots meet the five-minute SLA."""
        now = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)
        values = {
            "run_id": "fixture-run",
            "ticker": "NVDA",
            "cycle_ts": now,
            "technical_score": 0.82,
            "disclosure_score": 0.78,
            "news_score": 0.74,
            "is_daily_pick": True,
            "disclosure_snapshot_at": now - timedelta(minutes=1),
            "news_snapshot_at": now - timedelta(minutes=1),
            "evidence_ids": (
                "fixture-run:technical",
                "fixture-run:disclosure",
                "fixture-run:news",
            ),
        }
        return cls.model_validate({**values, **changes})

    def blockers(self) -> tuple[str, ...]:
        """Return deterministic blockers before any model recommendation."""
        blockers: list[str] = []
        if self.disclosure_hard_blocked or self.news_hard_blocked:
            blockers.append("upstream_hard_block")
        cycle = self.cycle_ts.astimezone(UTC)
        if cycle - self.disclosure_snapshot_at.astimezone(UTC) > SnapshotMaxAge:
            blockers.append("stale_disclosure_snapshot")
        if cycle - self.news_snapshot_at.astimezone(UTC) > SnapshotMaxAge:
            blockers.append("stale_news_snapshot")
        return tuple(blockers)


class StrategyOutput(BaseModel):
    """Code-gated strategist result; model output cannot bypass blockers."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", str_strip_whitespace=True)

    run_id: str
    ticker: str
    cycle_ts: datetime
    side: Literal["buy", "hold"]
    conviction: float = Field(ge=0, le=1)
    summary: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    blockers: tuple[str, ...] = ()
    gate_passed: bool

    @model_validator(mode="after")
    def require_gate_proof_for_buy(self) -> Self:
        """Make direct buy construction without a passed code gate invalid."""
        if self.side == "buy" and (not self.gate_passed or self.blockers):
            raise ContractViolation("buy requires code gate proof")
        return self

    @classmethod
    def from_model(
        cls,
        source: StrategyInput,
        conviction: float,
        summary: str,
        minimum_confidence: float = MIN_CONVICTION,
    ) -> Self:
        """Apply hard gates after schema-valid model output."""
        blockers = source.blockers()
        can_buy = source.is_daily_pick and conviction >= minimum_confidence and not blockers
        return cls(
            run_id=source.run_id,
            ticker=source.ticker,
            cycle_ts=source.cycle_ts,
            side="buy" if can_buy else "hold",
            conviction=conviction,
            summary=summary,
            evidence_ids=source.evidence_ids,
            blockers=blockers,
            gate_passed=can_buy,
        )


StrategistInput = StrategyInput
StrategistOutput = StrategyOutput
