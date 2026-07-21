"""The LLM spend ledger — what a day's calls actually cost, in the database."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from quantinue.db.postgres import PostgresRunStore
from quantinue.llm.budget import LlmUsageRecord

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="disposable PostgreSQL URL not provided"
)

_DAY = date(2026, 7, 21)


def _record(cost: str, *, hour: int, task: str = "strategy") -> LlmUsageRecord:
    return LlmUsageRecord(
        called_at=datetime(2026, 7, 21, hour, tzinfo=UTC),
        task=task,
        model="gpt-ledger-test",
        prompt_tokens=1_000,
        completion_tokens=500,
        est_cost_usd=Decimal(cost),
        run_id="ledger-test",
    )


@pytest.mark.anyio
async def test_a_days_calls_add_up_to_that_days_spend() -> None:
    """예산 판정의 입력이다 — 합이 틀리면 상한이 틀린 값을 막는다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()

    # When
    await store.domain.record_llm_usage(_record("0.003", hour=1))
    await store.domain.record_llm_usage(_record("0.007", hour=2, task="critic"))

    # Then
    assert await store.domain.llm_spend_on(_DAY) == Decimal("0.010")
    await store.close()


@pytest.mark.anyio
async def test_a_day_with_no_calls_costs_nothing() -> None:
    """행이 없는 날은 NULL이 아니라 0이어야 한다 — 비교가 죽지 않게."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()

    # Then
    assert await store.domain.llm_spend_on(date(2019, 1, 2)) == Decimal(0)
    await store.close()
