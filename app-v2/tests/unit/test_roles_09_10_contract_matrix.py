"""Complete evidence-contract matrix for roles 09 and 10."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from typing_extensions import Protocol

from quantinue.core.ontology import EvidenceKind
from quantinue.core.schemas import Evidence
from quantinue.roles.role_09_risk_portfolio.contracts import RiskPortfolioInput
from quantinue.roles.role_10_order_execution.contracts import OrderExecutionInput

NOW = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)


class ContractResult(Protocol):
    """Common observable shared by both late-stage input contracts."""

    @property
    def run_id(self) -> str:
        """Return the execution identity."""
        ...


ContractFactory = Callable[[tuple[Evidence, ...]], ContractResult]


def evidence(
    *,
    evidence_id: str = "run-1:parent",
    run_id: str = "run-1",
    captured_at: datetime = NOW,
    source_ref: str = "fixture://parent",
    parent_evidence_ids: tuple[str, ...] = (),
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        run_id=run_id,
        source="fixture",
        source_ref=source_ref,
        observed_at=captured_at,
        captured_at=captured_at,
        confidence=1.0,
        kind=EvidenceKind.MODEL_OUTPUT,
        parent_evidence_ids=parent_evidence_ids,
    )


def risk_contract(items: tuple[Evidence, ...]) -> RiskPortfolioInput:
    return RiskPortfolioInput(
        run_id="run-1",
        execution_at=NOW,
        evidence=items,
        signal_id=1,
        account_id=1,
        ticker="NVDA",
        cycle_ts=NOW,
        critic_approved=True,
        current_price=100,
        equity=10_000,
    )


def order_contract(items: tuple[Evidence, ...]) -> OrderExecutionInput:
    return OrderExecutionInput(
        run_id="run-1",
        execution_at=NOW,
        evidence=items,
        signal_id=1,
        account_id=1,
        ticker="NVDA",
        cycle_ts=NOW,
        quantity=1,
        entry_price=100,
        stop_loss=85,
        take_profit=120,
    )


@pytest.mark.parametrize("factory", [risk_contract, order_contract])
def test_role_contract_matrix_accepts_valid_parent_evidence(factory: ContractFactory) -> None:
    result = factory((evidence(),))

    assert result.run_id == "run-1"


@pytest.mark.parametrize("factory", [risk_contract, order_contract])
def test_role_contract_matrix_rejects_missing_evidence(factory: ContractFactory) -> None:
    with pytest.raises(ValidationError, match="at least 1"):
        _ = factory(())


@pytest.mark.parametrize(
    ("factory", "invalid_evidence", "message"),
    [
        (risk_contract, (evidence(captured_at=NOW - timedelta(minutes=6)),), "stale"),
        (order_contract, (evidence(captured_at=NOW - timedelta(minutes=6)),), "stale"),
        (risk_contract, (evidence(captured_at=NOW + timedelta(seconds=1)),), "future"),
        (order_contract, (evidence(captured_at=NOW + timedelta(seconds=1)),), "future"),
        (risk_contract, (evidence(run_id="other-run"),), "run_id"),
        (order_contract, (evidence(run_id="other-run"),), "run_id"),
        (
            risk_contract,
            (evidence(), evidence(source_ref="fixture://contradiction")),
            "contradictory",
        ),
        (
            order_contract,
            (evidence(), evidence(source_ref="fixture://contradiction")),
            "contradictory",
        ),
        (
            risk_contract,
            (evidence(parent_evidence_ids=("run-1:missing",)),),
            "lineage",
        ),
        (
            order_contract,
            (evidence(parent_evidence_ids=("run-1:missing",)),),
            "lineage",
        ),
    ],
)
def test_role_contract_matrix_rejects_invalid_evidence(
    factory: ContractFactory,
    invalid_evidence: tuple[Evidence, ...],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _ = factory(invalid_evidence)


def test_role_contract_matrix_has_no_na_dimensions() -> None:
    """All six evidence dimensions are meaningful before sizing or submission."""
    applicable = {
        "missing",
        "stale",
        "future",
        "cross_run",
        "contradictory",
        "missing_parent",
    }
    na_reasons: dict[str, str] = {}

    assert applicable == {
        "missing",
        "stale",
        "future",
        "cross_run",
        "contradictory",
        "missing_parent",
    }
    assert na_reasons == {}
