"""Role 09 decides once per subscribing account, not once per cycle.

Research (01-08) stays a single pass — the expensive part must not multiply
with the account count. Only sizing and execution fan out.
"""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from quantinue.core.contracts import PipelineContext, PipelineRequest, PriceSnapshot
from quantinue.core.ontology import EvidenceKind
from quantinue.core.schemas import Evidence
from quantinue.db.domain_records import AccountRiskState
from quantinue.db.memory import InMemoryRunStore
from quantinue.orchestration.policy import GatesConfig, ProfileConfig
from quantinue.roles.role_09_risk_portfolio.service import RiskPortfolio

NOW = datetime(2026, 7, 20, 17, 0, tzinfo=UTC)
PROFILES = {
    "aggressive": ProfileConfig(),
    "conservative": ProfileConfig(max_weight=0.10, max_positions=5, min_cash_ratio=0.30),
}


class _AccountsStore(InMemoryRunStore):
    """Run store that also answers the account-subscription queries."""

    def __init__(self, accounts: tuple[AccountRiskState, ...]) -> None:
        super().__init__()
        self._accounts = accounts

    async def active_accounts(self) -> tuple[AccountRiskState, ...]:
        return self._accounts

    async def account_risk_state(self, account_id: int) -> AccountRiskState | None:
        return next(
            (item for item in self._accounts if item.account_id == account_id), None
        )


def _state(account_id: int, equity: str, inv_type: str) -> AccountRiskState:
    return AccountRiskState(
        account_id=account_id,
        cash=Decimal(equity),
        equity=Decimal(equity),
        open_position_count=0,
        inv_type=inv_type,
    )


def _context() -> PipelineContext:
    context = PipelineContext(request=PipelineRequest(ticker="NVDA", cycle_ts=NOW))
    evidence = Evidence(
        evidence_id=f"{context.run_id}:08:critic",
        run_id=context.run_id,
        source="critic",
        source_ref="policy://critic/v1",
        observed_at=NOW,
        captured_at=NOW,
        confidence=1.0,
        kind=EvidenceKind.MODEL_OUTPUT,
    )
    return replace(
        context,
        last_price=100.0,
        critic_approved=True,
        price_snapshot=PriceSnapshot(
            current_price=100.0, day_high=100.0, day_low=100.0, close_prev=100.0
        ),
    ).add_stage("08", "크리틱", "승인", evidence=evidence)


def _service(accounts: tuple[AccountRiskState, ...]) -> RiskPortfolio:
    return RiskPortfolio(
        store=_AccountsStore(accounts),
        daily_new_order_cap=5,
        gates=GatesConfig(),
        profiles=PROFILES,
    )


@pytest.mark.anyio
async def test_one_plan_per_subscribing_account() -> None:
    accounts = (
        _state(1, "100000.00", "aggressive"),
        _state(2, "100000.00", "conservative"),
        _state(3, "5000.00", "aggressive"),
    )

    updated = await _service(accounts).execute(_context())

    assert tuple(plan.account_id for plan in updated.account_plans) == (1, 2, 3)


@pytest.mark.anyio
async def test_each_account_is_sized_by_its_own_capital_and_profile() -> None:
    accounts = (
        _state(1, "100000.00", "aggressive"),
        _state(2, "100000.00", "conservative"),
        _state(3, "5000.00", "aggressive"),
    )

    updated = await _service(accounts).execute(_context())

    by_account = {plan.account_id: plan.quantity for plan in updated.account_plans}
    assert by_account == {1: 200, 2: 100, 3: 10}


@pytest.mark.anyio
async def test_one_account_being_blocked_does_not_block_the_others() -> None:
    # 2번 계좌만 책이 가득 찼다.
    accounts = (
        _state(1, "100000.00", "aggressive"),
        replace(_state(2, "100000.00", "conservative"), open_position_count=5),
    )

    updated = await _service(accounts).execute(_context())

    by_account = {plan.account_id: plan.skipped_reason for plan in updated.account_plans}
    assert by_account[1] is None
    assert by_account[2] == "max_positions"


@pytest.mark.anyio
async def test_no_active_account_yields_no_plans() -> None:
    updated = await _service(()).execute(_context())

    assert updated.account_plans == ()
    assert updated.quantity == 0


@pytest.mark.anyio
async def test_the_summary_reports_every_account() -> None:
    accounts = (
        _state(1, "100000.00", "aggressive"),
        _state(2, "100000.00", "conservative"),
    )

    updated = await _service(accounts).execute(_context())

    assert "2개 계좌" in updated.stages[-1].summary
