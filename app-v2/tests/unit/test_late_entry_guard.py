"""Do not buy what has already run — the edge is gone and the stop is far.

`late_entry_max` sat in config with no consumer since M2. Wiring it exposed a
unit mismatch: the percent/fraction boundary, which is why the boundary cases
below are pinned exactly.

구 러너 삭제로 role_09 **서비스**를 통해 게이트를 확인하던 두 테스트는 사라졌다
(고정하던 코드가 같이 죽었다). 규칙 자체는 `build_order_plan`에 그대로 살아 있고
배분 잡이 그 함수를 부르므로, 규칙 테스트는 여기 남는다.
"""

from datetime import UTC, datetime

from quantinue.core.ontology import EvidenceKind
from quantinue.core.schemas import Evidence
from quantinue.orchestration.policy import ProfileConfig
from quantinue.roles.role_09_risk_portfolio.contracts import (
    RiskPortfolioInput,
    build_order_plan,
)

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
