"""Incremental T+1 through T+5 production review processor."""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum, unique
from typing import Protocol

from quantinue.core.ontology import ModelProvider
from quantinue.roles.role_11_reviewer.calendar import (
    Clock,
    TradingCalendar,
    UsEquityTradingCalendar,
)

_FINAL_OFFSET = 5


@dataclass(frozen=True, slots=True)
class DueReviewSignal:
    """Canonical signal fields required by delayed review."""

    signal_id: int
    run_id: str
    ticker: str
    side: str
    trade_date: date
    base_price: Decimal


@dataclass(frozen=True, slots=True)
class ReviewSnapshotWrite:
    """Self-contained canonical evidence for one delayed close."""

    signal_id: int
    day_offset: int
    price_date: date
    close: Decimal
    source: str
    source_ref: str
    observed_at: datetime
    captured_at: datetime
    confidence: float
    evidence_id: str
    parent_evidence_ids: tuple[str, ...]
    model_provider: ModelProvider | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    policy_version: str | None = None
    input_hash: str | None = None


class ReviewRepository(Protocol):
    """Persistence operations consumed by the processor."""

    async def get_signal(self, signal_id: int) -> DueReviewSignal | None:
        """Load one canonical review projection."""
        ...

    async def snapshot_offsets(self, signal_id: int) -> frozenset[int]:
        """Return captured trading offsets."""
        ...

    async def save_snapshot(self, value: ReviewSnapshotWrite) -> None:
        """Persist one idempotent official close."""
        ...

    async def finalize_review(self, signal: DueReviewSignal, lesson: str) -> None:
        """Upsert the final review from persisted snapshots."""
        ...


class ClosingPriceProvider(Protocol):
    """Historical official-close lookup capability."""

    async def close(self, ticker: str, session_date: date) -> Decimal:
        """Return the official close for a trading session."""
        ...


@unique
class ReviewProcessStatus(StrEnum):
    """Observable delayed-review state."""

    NOT_FOUND = "not_found"
    PENDING = "pending"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class ReviewProcessResult:
    """One idempotent processor invocation result."""

    signal_id: int
    status: ReviewProcessStatus
    captured_offsets: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ReviewProcessor:
    """Capture every due close and finalize only after the fifth session."""

    repository: ReviewRepository
    prices: ClosingPriceProvider
    clock: Clock
    calendar: TradingCalendar = field(default_factory=UsEquityTradingCalendar)

    async def process(self, signal_id: int) -> ReviewProcessResult:
        """Advance one signal without sleeping or depending on wall-clock time."""
        signal = await self.repository.get_signal(signal_id)
        if signal is None:
            return ReviewProcessResult(signal_id, ReviewProcessStatus.NOT_FOUND, ())
        existing = await self.repository.snapshot_offsets(signal_id)
        captured = set(existing)
        now = self.clock.now()
        for offset in range(1, _FINAL_OFFSET + 1):
            session = self.calendar.offset(signal.trade_date, trading_days=offset)
            if offset not in captured and now >= self.calendar.session_close(session):
                close = await self.prices.close(signal.ticker, session)
                await self.repository.save_snapshot(
                    ReviewSnapshotWrite(
                        signal_id=signal_id,
                        day_offset=offset,
                        price_date=session,
                        close=close,
                        source="market_data",
                        source_ref=f"market-data://close/{signal.ticker}/{session.isoformat()}",
                        observed_at=self.calendar.session_close(session),
                        captured_at=now,
                        confidence=1.0,
                        evidence_id=f"{signal.run_id}:11:close:{offset}",
                        parent_evidence_ids=(f"{signal.run_id}:07:strategy",),
                    )
                )
                captured.add(offset)
        if len(captured) < _FINAL_OFFSET:
            return ReviewProcessResult(
                signal_id, ReviewProcessStatus.PENDING, tuple(sorted(captured))
            )
        await self.repository.finalize_review(signal, "T+5 deterministic outcome review")
        return ReviewProcessResult(
            signal_id, ReviewProcessStatus.COMPLETED, tuple(sorted(captured))
        )
