"""Do not buy what has already run — the edge is gone and the stop is far.

`late_entry_max` sat in config with no consumer since M2. Wiring it exposed a
unit mismatch: role 02 reports `ret_5d` in percent (`*100`) while the profile
threshold is a fraction, so a naive comparison is off by 100x and the gate
would never fire.
"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from quantinue.core.contracts import PipelineContext, PipelineRequest, PriceSnapshot
from quantinue.core.ontology import EvidenceKind
from quantinue.core.schemas import Evidence
from quantinue.db.memory import InMemoryRunStore
from quantinue.orchestration.policy import GatesConfig, ProfileConfig
from quantinue.roles.role_02_technical_analysis.contracts import (
    TechnicalAnalysisOutput,
    TechnicalSnapshot,
    Trend,
)
from quantinue.roles.role_09_risk_portfolio.contracts import (
    RiskPortfolioInput,
    build_order_plan,
)
from quantinue.roles.role_09_risk_portfolio.service import RiskPortfolio

AGGRESSIVE = ProfileConfig()  # late_entry_max 0.15
CONSERVATIVE = ProfileConfig(late_entry_max=0.12)
MIDDAY = datetime(2026, 7, 20, 17, 0, tzinfo=UTC)

_EVIDENCE = Evidence(
    evidence_id="run-late:08:critic",
    run_id="run-late",
    source="critic",
    source_ref="policy://critic/v1",
    observed_at=MIDDAY,
    captured_at=MIDDAY,
    confidence=1.0,
    kind=EvidenceKind.MODEL_OUTPUT,
)


def _plan_input(recent_return: float | None) -> RiskPortfolioInput:
    return RiskPortfolioInput(
        run_id="run-late",
        execution_at=MIDDAY,
        evidence=(_EVIDENCE,),
        signal_id=1,
        account_id=1,
        ticker="NVDA",
        cycle_ts=MIDDAY,
        critic_approved=True,
        current_price=100.0,
        equity=100_000.0,
        daily_new_order_cap=5,
        risk_score=0.0,
        recent_return=recent_return,
    )


def test_a_stock_already_up_beyond_the_profile_limit_is_not_bought() -> None:
    plan = build_order_plan(_plan_input(0.22), late_entry_max=AGGRESSIVE.late_entry_max)

    assert plan.quantity == 0
    assert plan.skipped_reason == "late_entry"


def test_late_entry_boundary_is_inclusive() -> None:
    at_limit = build_order_plan(_plan_input(0.15), late_entry_max=0.15)
    just_over = build_order_plan(_plan_input(0.151), late_entry_max=0.15)

    assert at_limit.skipped_reason != "late_entry"
    assert just_over.skipped_reason == "late_entry"


def test_the_conservative_profile_stops_earlier_than_the_aggressive_one() -> None:
    run_up = _plan_input(0.13)

    aggressive = build_order_plan(run_up, late_entry_max=AGGRESSIVE.late_entry_max)
    conservative = build_order_plan(run_up, late_entry_max=CONSERVATIVE.late_entry_max)

    assert aggressive.skipped_reason != "late_entry"
    assert conservative.skipped_reason == "late_entry"


def test_a_fall_is_never_a_late_entry() -> None:
    # Only an upward run-up removes the edge; a drawdown is a different question.
    plan = build_order_plan(_plan_input(-0.30), late_entry_max=0.15)

    assert plan.quantity > 0
    assert plan.skipped_reason is None


def _context(ret_5d_percent: float) -> PipelineContext:
    context = PipelineContext(request=PipelineRequest(ticker="NVDA", cycle_ts=MIDDAY))
    evidence = Evidence(
        evidence_id=f"{context.run_id}:08:critic",
        run_id=context.run_id,
        source="critic",
        source_ref="policy://critic/v1",
        observed_at=MIDDAY,
        captured_at=MIDDAY,
        confidence=1.0,
        kind=EvidenceKind.MODEL_OUTPUT,
    )
    snapshot = TechnicalSnapshot(
        trade_date=MIDDAY.date(),
        ticker="NVDA",
        close=100.0,
        rs_20=1.0,
        vol_ratio=1.0,
        ret_5d=ret_5d_percent,
        ret_20d=1.0,
        atr_pct=1.0,
        high_252_ratio=1.0,
        rsi=50.0,
        macd=0.0,
        ma20=100.0,
        ma50=100.0,
        trend=Trend.UP,
        evidence_ids=(f"{context.run_id}:02:technical",),
    )
    return replace(
        context,
        last_price=100.0,
        critic_approved=True,
        price_snapshot=PriceSnapshot(
            current_price=100.0, day_high=100.0, day_low=100.0, close_prev=100.0
        ),
        technical_output=TechnicalAnalysisOutput(run_id=context.run_id, snapshots=(snapshot,)),
    ).add_stage("08", "크리틱", "승인", evidence=evidence)


def _service(profile: ProfileConfig) -> RiskPortfolio:
    return RiskPortfolio(
        store=InMemoryRunStore(),
        daily_new_order_cap=5,
        gates=GatesConfig(),
        profile=profile,
    )


@pytest.mark.anyio
async def test_percent_returns_are_normalised_before_the_fraction_threshold() -> None:
    # 22% arrives as 22.0 from role 02, not 0.22.
    updated = await _service(AGGRESSIVE).execute(_context(22.0))

    assert updated.risk_skipped_reason == "late_entry"


@pytest.mark.anyio
async def test_a_modest_run_up_still_buys() -> None:
    updated = await _service(AGGRESSIVE).execute(_context(4.0))

    assert updated.quantity > 0
    assert updated.risk_skipped_reason is None
