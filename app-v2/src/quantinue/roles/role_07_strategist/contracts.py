"""Immutable role 07 strategy boundary contracts and code gates."""

# ruff: noqa: EM101, EM102, TRY003

from datetime import UTC, datetime, timedelta
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from quantinue.orchestration.policy import GatesConfig, ProfileConfig

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
    disclosure_score: float | None = Field(ge=0, le=1)
    news_score: float = Field(ge=0, le=1)
    is_daily_pick: bool
    source_trust: float = Field(default=1.0, ge=0, le=1)
    macro_risk_score: float = Field(default=0.0, ge=0, le=1)
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

    def blockers(self, gates: GatesConfig | None = None) -> tuple[str, ...]:
        """Return deterministic blockers before any model recommendation."""
        blockers: list[str] = []
        if self.disclosure_hard_blocked or self.news_hard_blocked:
            blockers.append("upstream_hard_block")
        cycle = self.cycle_ts.astimezone(UTC)
        if cycle - self.disclosure_snapshot_at.astimezone(UTC) > SnapshotMaxAge:
            blockers.append("stale_disclosure_snapshot")
        if cycle - self.news_snapshot_at.astimezone(UTC) > SnapshotMaxAge:
            blockers.append("stale_news_snapshot")
        if (
            gates is not None
            and self.disclosure_score is not None
            and self.disclosure_score <= gates.hard_negative_max
        ):
            # 강한 악재는 아무리 확신도가 높아도 매수를 막는다.
            blockers.append("hard_negative_sentiment")
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

    @staticmethod
    def vote_conviction(
        source: StrategyInput, gates: GatesConfig, model_score: float | None = None
    ) -> float:
        """Average the surviving signal votes, then apply the macro deduction.

        A news score sourced below the trust floor loses its vote entirely
        rather than being down-weighted, so an unreliable outlet cannot lift a
        decision at all. An absent disclosure abstains for the same reason in
        reverse: silence is not bad news, and scoring it zero would both dilute
        conviction and trip the hard-negative gate.
        """
        votes = [source.technical_score]
        if source.disclosure_score is not None:
            votes.append(source.disclosure_score)
        if source.source_trust >= gates.source_trust_min:
            votes.append(source.news_score)
        if model_score is not None:
            votes.append(model_score)
        raw = sum(votes) / len(votes)
        penalised = raw - gates.macro_penalty(source.macro_risk_score)
        return round(min(1.0, max(0.0, penalised)), 3)

    @staticmethod
    def vote_consensus(
        source: StrategyInput,
        gates: GatesConfig,
        profile: ProfileConfig,
        model_score: float | None = None,
    ) -> int:
        """Count how many surviving votes cleared the buy threshold.

        Recorded for later study, never gated on. A vote stripped upstream —
        untrusted news, absent disclosure — cannot consent, because silence is
        not agreement.
        """
        votes = [source.technical_score]
        if source.disclosure_score is not None:
            votes.append(source.disclosure_score)
        if source.source_trust >= gates.source_trust_min:
            votes.append(source.news_score)
        if model_score is not None:
            votes.append(model_score)
        return sum(1 for vote in votes if vote >= profile.buy_threshold)

    @classmethod
    def from_model(  # noqa: PLR0913 - each gate input is an explicit seam
        cls,
        source: StrategyInput,
        conviction: float,
        summary: str,
        *,
        gates: GatesConfig | None = None,
        profile: ProfileConfig | None = None,
        minimum_confidence: float = MIN_CONVICTION,
    ) -> Self:
        """Apply hard gates after schema-valid model output."""
        blockers = source.blockers(gates)
        threshold = profile.buy_threshold if profile is not None else minimum_confidence
        can_buy = source.is_daily_pick and conviction >= threshold and not blockers
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
