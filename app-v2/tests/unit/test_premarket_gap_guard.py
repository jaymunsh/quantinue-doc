"""A pick priced off Friday's close must not be executed into Monday's gap.

Entry, stop, and take-profit are all derived from the analysis reference close.
When the market reopens far away from it the whole bracket is meaningless, so a
new buy is skipped rather than sized against a stale reference.

The guard is session-scoped on purpose: a 3% move at 2pm is an ordinary day, a
3% move before the bell is a gap. Applying it all session would skip normal buys.
"""

from datetime import UTC, datetime, timedelta

import pytest

from quantinue.core.contracts import PriceSnapshot
from quantinue.core.ontology import EvidenceKind
from quantinue.core.schemas import Evidence
from quantinue.orchestration.policy import GatesConfig
from quantinue.roles.role_09_risk_portfolio.contracts import (
    RiskPortfolioInput,
    build_order_plan,
    gap_guard_applies,
)

GATES = GatesConfig()
NOW = datetime(2026, 7, 20, 13, 0, tzinfo=UTC)

_EVIDENCE = Evidence(
    evidence_id="run-gap:08:critic",
    run_id="run-gap",
    source="critic",
    source_ref="policy://critic/v1",
    observed_at=NOW,
    captured_at=NOW,
    confidence=1.0,
    kind=EvidenceKind.MODEL_OUTPUT,
)


def _plan_input(gap: float | None) -> RiskPortfolioInput:
    return RiskPortfolioInput(
        run_id="run-gap",
        execution_at=NOW,
        evidence=(_EVIDENCE,),
        signal_id=1,
        account_id=1,
        ticker="NVDA",
        cycle_ts=NOW,
        critic_approved=True,
        current_price=100.0,
        equity=100_000.0,
        daily_new_order_cap=5,
        risk_score=0.0,
        reference_gap=gap,
    )


def test_gap_beyond_the_threshold_skips_the_new_buy() -> None:
    plan = build_order_plan(_plan_input(0.08), premarket_gap_max=GATES.premarket_gap_max)

    assert plan.quantity == 0
    assert plan.skipped_reason == "premarket_gap"


def test_gap_threshold_boundary_is_inclusive() -> None:
    at_threshold = build_order_plan(_plan_input(0.03), premarket_gap_max=0.03)
    just_over = build_order_plan(_plan_input(0.031), premarket_gap_max=0.03)

    assert at_threshold.skipped_reason != "premarket_gap"
    assert just_over.skipped_reason == "premarket_gap"


def test_gap_down_is_guarded_too() -> None:
    # A collapse invalidates the bracket exactly as a spike does.
    plan = build_order_plan(_plan_input(0.09), premarket_gap_max=GATES.premarket_gap_max)

    assert plan.skipped_reason == "premarket_gap"


def test_no_reference_gap_leaves_the_plan_untouched() -> None:
    # Outside the guard window the gap is not measured at all.
    plan = build_order_plan(_plan_input(None), premarket_gap_max=GATES.premarket_gap_max)

    assert plan.quantity > 0
    assert plan.skipped_reason is None


@pytest.mark.parametrize(
    ("current", "close_prev", "expected"),
    [
        (103.0, 100.0, 0.03),
        (97.0, 100.0, 0.03),
        (100.0, 100.0, 0.0),
    ],
)
def test_snapshot_gap_is_the_absolute_move_from_the_reference_close(
    current: float, close_prev: float, expected: float
) -> None:
    snapshot = PriceSnapshot(
        current_price=current,
        day_high=max(current, close_prev),
        day_low=min(current, close_prev),
        close_prev=close_prev,
    )

    assert round(snapshot.gap_from_reference(), 6) == expected


def test_guard_window_covers_premarket_and_the_opening_stretch() -> None:
    open_utc = datetime(2026, 7, 20, 13, 30, tzinfo=UTC)  # 09:30 New York

    assert gap_guard_applies(open_utc - timedelta(hours=2), open_utc, 30) is True
    assert gap_guard_applies(open_utc, open_utc, 30) is True
    assert gap_guard_applies(open_utc + timedelta(minutes=29), open_utc, 30) is True
    assert gap_guard_applies(open_utc + timedelta(minutes=31), open_utc, 30) is False
