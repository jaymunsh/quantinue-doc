from datetime import UTC, datetime
from decimal import Decimal

import pytest

from quantinue.db.domain_records import InsufficientSimulatedCashError
from quantinue.db.simulated_portfolio import (
    MarkSource,
    PortfolioMark,
    RealizedPnlStatus,
    SimulatedFill,
    SimulatedOrder,
    SimulatedOrderStatus,
    project_buy_only_portfolio,
)


def _at(hour: int) -> datetime:
    return datetime(2026, 7, 14, hour, tzinfo=UTC)


def test_two_unique_buys_produce_weighted_cost_cash_and_marked_equity() -> None:
    # Given
    orders = (
        SimulatedOrder(
            "order-1",
            "NVDA",
            2,
            Decimal("100.00"),
            SimulatedOrderStatus.FILLED,
            _at(1),
        ),
        SimulatedOrder(
            "order-2",
            "NVDA",
            1,
            Decimal("130.00"),
            SimulatedOrderStatus.FILLED,
            _at(2),
        ),
    )
    fills = (
        SimulatedFill("fill-1", "order-1", "NVDA", 2, Decimal("100.00"), _at(1)),
        SimulatedFill("fill-2", "order-2", "NVDA", 1, Decimal("130.00"), _at(2)),
    )
    marks = (PortfolioMark("NVDA", Decimal("120.00"), MarkSource.COMPLETED_RUN, _at(3)),)

    # When
    result = project_buy_only_portfolio(Decimal("1000000.00"), orders, fills, marks)

    # Then
    assert result.account.current_cash == Decimal("999670.00")
    assert result.account.equity == Decimal("1000030.00")
    assert result.positions[0].average_cost == Decimal("110.00")
    assert result.positions[0].market_value == Decimal("360.00")
    assert result.positions[0].unrealized_pnl == Decimal("30.00")
    assert result.positions[0].allocation == Decimal("0.0004")


def test_duplicate_fill_identity_cannot_double_debit() -> None:
    # Given
    order = SimulatedOrder(
        "order-1",
        "NVDA",
        2,
        Decimal("100.00"),
        SimulatedOrderStatus.FILLED,
        _at(1),
    )
    fill = SimulatedFill("fill-1", "order-1", "NVDA", 2, Decimal("100.00"), _at(1))

    # When
    result = project_buy_only_portfolio(Decimal("1000000.00"), (order,), (fill, fill), ())

    # Then
    assert result.account.current_cash == Decimal("999800.00")
    assert result.positions[0].quantity == 2
    assert result.positions[0].mark.source is MarkSource.LATEST_FILL


def test_latest_completed_run_mark_wins_over_newer_fill_fallback() -> None:
    # Given
    fills = (SimulatedFill("fill-1", "order-1", "NVDA", 1, Decimal("100.00"), _at(4)),)
    marks = (PortfolioMark("NVDA", Decimal("125.00"), MarkSource.COMPLETED_RUN, _at(3)),)

    # When
    result = project_buy_only_portfolio(Decimal("1000000.00"), (), fills, marks)

    # Then
    assert result.positions[0].mark.price == Decimal("125.00")
    assert result.positions[0].mark.as_of == _at(3)


def test_stale_completed_run_mark_is_replaced_by_latest_completed_run_mark() -> None:
    # Given
    fill = SimulatedFill("fill-1", "order-1", "NVDA", 1, Decimal("100.00"), _at(1))
    marks = (
        PortfolioMark("NVDA", Decimal("105.00"), MarkSource.COMPLETED_RUN, _at(2)),
        PortfolioMark("NVDA", Decimal("110.00"), MarkSource.COMPLETED_RUN, _at(3)),
    )

    # When
    result = project_buy_only_portfolio(Decimal("1000000.00"), (), (fill,), marks)

    # Then
    assert result.positions[0].mark.price == Decimal("110.00")


def test_empty_account_preserves_opening_cash_and_realized_is_not_applicable() -> None:
    # Given / When
    result = project_buy_only_portfolio(Decimal("1000000.00"), (), (), ())

    # Then
    assert result.account.opening_cash == Decimal("1000000.00")
    assert result.account.current_cash == Decimal("1000000.00")
    assert result.account.equity == Decimal("1000000.00")
    assert result.positions == ()
    assert result.realized_pnl is None
    assert result.realized_pnl_status is RealizedPnlStatus.NOT_APPLICABLE_BUY_ONLY


def test_rejected_order_without_fill_does_not_change_account() -> None:
    # Given
    order = SimulatedOrder(
        "order-1",
        "NVDA",
        1,
        Decimal("100.00"),
        SimulatedOrderStatus.REJECTED,
        _at(1),
    )

    # When
    result = project_buy_only_portfolio(Decimal("1000000.00"), (order,), (), ())

    # Then
    assert result.account.current_cash == Decimal("1000000.00")
    assert result.orders == (order,)
    assert result.fills == ()


def test_money_and_allocation_use_deterministic_decimal_rounding() -> None:
    # Given
    fill = SimulatedFill("fill-1", "order-1", "ABC", 3, Decimal("0.01"), _at(1))
    mark = PortfolioMark("ABC", Decimal("0.02"), MarkSource.COMPLETED_RUN, _at(2))

    # When
    result = project_buy_only_portfolio(Decimal("1.00"), (), (fill,), (mark,))

    # Then
    assert result.account.current_cash == Decimal("0.97")
    assert result.account.equity == Decimal("1.03")
    assert result.positions[0].allocation == Decimal("0.0583")


def test_stale_conflicting_duplicate_fill_cannot_replace_first_canonical_fill() -> None:
    # Given
    canonical = SimulatedFill("fill-1", "order-1", "NVDA", 2, Decimal("100.00"), _at(2))
    stale_replay = SimulatedFill("fill-1", "order-1", "NVDA", 9, Decimal("500.00"), _at(1))

    # When
    result = project_buy_only_portfolio(Decimal("1000000.00"), (), (canonical, stale_replay), ())

    # Then
    assert result.account.current_cash == Decimal("999800.00")
    assert result.fills == (canonical,)


def test_projection_rejects_fill_cost_above_opening_cash() -> None:
    # Given
    fill = SimulatedFill("fill-1", "order-1", "NVDA", 2, Decimal("100.00"), _at(1))

    # When / Then
    with pytest.raises(InsufficientSimulatedCashError):
        _ = project_buy_only_portfolio(Decimal("100.00"), (), (fill,), ())
