"""Role 09 measures the gap from the snapshot role 02 actually captured."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from quantinue.core.contracts import (
    PipelineContext,
    PipelineRequest,
    PriceSnapshot,
)
from quantinue.core.ontology import EvidenceKind
from quantinue.core.schemas import Evidence
from quantinue.db.memory import InMemoryRunStore
from quantinue.orchestration.policy import GatesConfig
from quantinue.roles.role_09_risk_portfolio.service import RiskPortfolio

GATES = GatesConfig()
# 2026-07-20 is a Monday; the bell rings at 13:30 UTC.
PREMARKET = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
MIDDAY = datetime(2026, 7, 20, 17, 0, tzinfo=UTC)


def _context(cycle_ts: datetime, *, current: float, close_prev: float) -> PipelineContext:
    context = PipelineContext(request=PipelineRequest(ticker="NVDA", cycle_ts=cycle_ts))
    return _with_critic_stage(context, current=current, close_prev=close_prev)


def _with_critic_stage(
    context: PipelineContext, *, current: float, close_prev: float
) -> PipelineContext:
    evidence = Evidence(
        evidence_id=f"{context.run_id}:08:critic",
        run_id=context.run_id,
        source="critic",
        source_ref="policy://critic/v1",
        observed_at=context.request.cycle_ts,
        captured_at=context.request.cycle_ts,
        confidence=1.0,
        kind=EvidenceKind.MODEL_OUTPUT,
    )
    return replace(
        context,
        last_price=current,
        critic_approved=True,
        price_snapshot=PriceSnapshot(
            current_price=current,
            day_high=max(current, close_prev),
            day_low=min(current, close_prev),
            close_prev=close_prev,
        ),
    ).add_stage("08", "크리틱", "승인", evidence=evidence)


def _service() -> RiskPortfolio:
    return RiskPortfolio(store=InMemoryRunStore(), daily_new_order_cap=5, gates=GATES)


@pytest.mark.anyio
async def test_monday_gap_up_skips_the_buy_planned_on_fridays_close() -> None:
    # Given: analysis priced on a 100.00 close, reopening 8% higher
    context = _context(PREMARKET, current=108.0, close_prev=100.0)

    updated = await _service().execute(context)

    assert updated.quantity == 0
    assert updated.risk_skipped_reason == "premarket_gap"


@pytest.mark.anyio
async def test_quiet_reopen_still_places_the_order() -> None:
    context = _context(PREMARKET, current=100.5, close_prev=100.0)

    updated = await _service().execute(context)

    assert updated.quantity > 0
    assert updated.risk_skipped_reason is None


@pytest.mark.anyio
async def test_midday_drift_of_the_same_size_is_not_treated_as_a_gap() -> None:
    # Given: the same 8% distance, but hours into the session
    context = _context(MIDDAY, current=108.0, close_prev=100.0)

    updated = await _service().execute(context)

    assert updated.risk_skipped_reason != "premarket_gap"
    assert updated.quantity > 0
