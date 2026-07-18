"""Production review processing behavior."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from quantinue.api.reviews import build_review_router
from quantinue.roles.role_11_reviewer.processor import (
    DueReviewSignal,
    ReviewProcessor,
    ReviewProcessStatus,
    ReviewSnapshotWrite,
)


@dataclass(slots=True)
class MutableClock:
    """Mutable fixture clock used to advance time without waiting."""

    current: datetime

    def now(self) -> datetime:
        return self.current


@dataclass(slots=True)
class FakeReviews:
    """Mutable fake whose purpose is recording idempotent repository writes."""

    signal: DueReviewSignal
    snapshots: dict[int, Decimal] = field(default_factory=dict)
    final_count: int = 0

    async def get_signal(self, signal_id: int) -> DueReviewSignal | None:
        return self.signal if signal_id == self.signal.signal_id else None

    async def snapshot_offsets(self, signal_id: int) -> frozenset[int]:
        del signal_id
        return frozenset(self.snapshots)

    async def save_snapshot(self, value: ReviewSnapshotWrite) -> None:
        _ = self.snapshots.setdefault(value.day_offset, value.close)

    async def finalize_review(self, signal: DueReviewSignal, lesson: str) -> None:
        del signal, lesson
        self.final_count += 1


@dataclass(frozen=True, slots=True)
class Prices:
    async def close(self, ticker: str, session_date: date) -> Decimal:
        del ticker
        return Decimal(100 + session_date.day)


@pytest.mark.anyio
async def test_processor_is_pending_before_first_due_close() -> None:
    # Given
    signal = DueReviewSignal(7, "run-7", "NVDA", "hold", date(2026, 7, 2), Decimal(100))
    store = FakeReviews(signal)
    processor = ReviewProcessor(
        store, Prices(), MutableClock(datetime(2026, 7, 3, 19, 59, tzinfo=UTC))
    )

    # When
    result = await processor.process(7)

    # Then
    assert result.status is ReviewProcessStatus.PENDING
    assert store.snapshots == {}


@pytest.mark.anyio
async def test_processor_persists_due_sessions_and_finalizes_idempotently() -> None:
    # Given: July 3 is a holiday and the fifth session is July 10.
    signal = DueReviewSignal(8, "run-8", "NVDA", "hold", date(2026, 7, 2), Decimal(100))
    store = FakeReviews(signal)
    clock = MutableClock(datetime(2026, 7, 10, 20, 1, tzinfo=UTC))
    processor = ReviewProcessor(store, Prices(), clock)

    # When
    first = await processor.process(8)
    second = await processor.process(8)

    # Then
    assert first.status is ReviewProcessStatus.COMPLETED
    assert second.status is ReviewProcessStatus.COMPLETED
    assert set(store.snapshots) == {1, 2, 3, 4, 5}
    assert all(offset in store.snapshots for offset in range(1, 6))
    assert store.final_count == 2  # repository finalization is an idempotent upsert


@pytest.mark.anyio
async def test_review_api_advances_injected_clock_without_waiting() -> None:
    # Given
    signal = DueReviewSignal(9, "run-9", "NVDA", "hold", date(2026, 7, 2), Decimal(100))
    store = FakeReviews(signal)
    clock = MutableClock(datetime(2026, 7, 3, 19, 59, tzinfo=UTC))
    app = FastAPI()
    app.include_router(build_review_router(ReviewProcessor(store, Prices(), clock)))

    # When
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        early = await client.post("/api/reviews/9/process")
        clock.current = datetime(2026, 7, 10, 20, 1, tzinfo=UTC)
        final = await client.post("/api/reviews/9/process")

    # Then
    assert early.json()["status"] == "pending"
    assert final.json()["status"] == "completed"
    assert store.final_count == 1
