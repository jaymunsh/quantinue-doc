"""Role 09 and 10 deterministic boundary contracts."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from quantinue.core.ontology import EvidenceKind
from quantinue.core.schemas import Evidence
from quantinue.roles.role_09_risk_portfolio.contracts import (
    RiskPortfolioInput,
    RiskPortfolioOutput,
    build_order_plan,
)
from quantinue.roles.role_10_order_execution.contracts import OrderExecutionInput

NOW = datetime(2026, 7, 13, 1, tzinfo=UTC)


def source_evidence() -> tuple[Evidence, ...]:
    return (
        Evidence(
            evidence_id="run-1:08",
            run_id="run-1",
            source="pipeline_role",
            source_ref="08:critic",
            observed_at=NOW,
            captured_at=NOW,
            confidence=1.0,
            kind=EvidenceKind.MODEL_OUTPUT,
        ),
    )


def test_risk_sizing_uses_risk_budget_then_position_cap() -> None:
    request = RiskPortfolioInput(
        run_id="run-1",
        execution_at=NOW,
        evidence=source_evidence(),
        signal_id=4022,
        account_id=1,
        ticker="NVDA",
        cycle_ts=NOW,
        critic_approved=True,
        current_price=100,
        equity=10_000,
    )

    result = build_order_plan(request)

    assert result == RiskPortfolioOutput(
        run_id="run-1",
        signal_id=4022,
        account_id=1,
        ticker="NVDA",
        decision="planned",
        quantity=25,
        entry_price=100,
        stop_loss=85,
        take_profit=120,
        skipped_reason=None,
        evidence_ids=("run-1:08",),
    )


def test_order_plan_uses_injected_bracket_ratios_and_risk_threshold() -> None:
    # Given
    request = RiskPortfolioInput(
        run_id="run-1",
        execution_at=NOW,
        evidence=source_evidence(),
        signal_id=4022,
        account_id=1,
        ticker="NVDA",
        cycle_ts=NOW,
        critic_approved=True,
        current_price=100,
        equity=10_000,
        risk_score=0.8,
    )

    # When
    result = build_order_plan(
        request, stop_loss_ratio=0.1, take_profit_ratio=0.3, maximum_risk_score=0.7
    )

    # Then
    assert result.decision == "skipped"
    assert result.skipped_reason == "risk_limit"
    assert result.stop_loss == 90
    assert result.take_profit == 130


@pytest.mark.parametrize(
    "blocking_field",
    ["critic_approved", "has_position", "has_open_order", "event_within_two_days"],
)
def test_risk_gate_returns_zero_quantity_for_blocked_plan(blocking_field: str) -> None:
    values = {
        "run_id": "run-1",
        "execution_at": NOW,
        "evidence": source_evidence(),
        "signal_id": 1,
        "account_id": 1,
        "ticker": "NVDA",
        "cycle_ts": NOW,
        "critic_approved": True,
        "current_price": 100,
        "equity": 10_000,
    }
    values[blocking_field] = blocking_field != "critic_approved"

    result = build_order_plan(RiskPortfolioInput.model_validate(values))

    assert result.quantity == 0
    assert result.decision == "skipped"
    assert result.skipped_reason is not None


def test_order_execution_input_rejects_inverted_bracket() -> None:
    with pytest.raises(ValidationError, match="stop < entry < take-profit"):
        _ = OrderExecutionInput(
            run_id="run-1",
            execution_at=NOW,
            evidence=source_evidence(),
            signal_id=1,
            account_id=1,
            ticker="NVDA",
            cycle_ts=NOW,
            quantity=1,
            entry_price=100,
            stop_loss=101,
            take_profit=120,
        )


def test_order_execution_input_has_stable_client_order_id() -> None:
    values = {
        "run_id": "run-1",
        "execution_at": NOW,
        "evidence": source_evidence(),
        "signal_id": 4022,
        "account_id": 7,
        "ticker": "NVDA",
        "cycle_ts": NOW,
        "quantity": 4,
        "entry_price": 100,
        "stop_loss": 85,
        "take_profit": 120,
    }

    first = OrderExecutionInput.model_validate(values)
    second = OrderExecutionInput.model_validate(values)

    assert first.client_order_id == second.client_order_id
    assert first.client_order_id == "q-a7-s4022"


def test_risk_input_rejects_one_cent_price_that_cannot_form_cent_tick_bracket() -> None:
    with pytest.raises(ValidationError, match=r"greater than or equal to 0\.04"):
        _ = RiskPortfolioInput(
            run_id="run-1",
            execution_at=NOW,
            evidence=source_evidence(),
            signal_id=1,
            account_id=1,
            ticker="NVDA",
            cycle_ts=NOW,
            critic_approved=True,
            current_price=0.01,
            equity=100,
        )


def test_risk_output_rejects_non_strict_bracket() -> None:
    with pytest.raises(ValidationError, match="stop < entry < take-profit"):
        _ = RiskPortfolioOutput(
            run_id="run-1",
            signal_id=1,
            account_id=1,
            ticker="NVDA",
            decision="planned",
            quantity=1,
            entry_price=0.04,
            stop_loss=0.04,
            take_profit=0.05,
            skipped_reason=None,
            evidence_ids=("run-1:08",),
        )


def test_risk_gate_skips_when_daily_new_order_cap_is_reached() -> None:
    request = RiskPortfolioInput(
        run_id="run-1",
        execution_at=NOW,
        evidence=source_evidence(),
        signal_id=1,
        account_id=1,
        ticker="NVDA",
        cycle_ts=NOW,
        critic_approved=True,
        current_price=100,
        equity=10_000,
        daily_new_order_count=3,
        daily_new_order_cap=3,
    )

    result = build_order_plan(request)

    assert result.decision == "skipped"
    assert result.skipped_reason == "daily_order_cap"


def test_risk_input_rejects_negative_daily_new_order_count() -> None:
    with pytest.raises(ValidationError):
        _ = RiskPortfolioInput(
            run_id="run-1",
            execution_at=NOW,
            evidence=source_evidence(),
            signal_id=1,
            account_id=1,
            ticker="NVDA",
            cycle_ts=NOW,
            critic_approved=True,
            current_price=100,
            equity=10_000,
            daily_new_order_count=-1,
        )
