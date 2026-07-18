"""Role 01 keeps the wide universe and orders it by market capitalisation."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from quantinue.core.errors import ValidationFailureError
from quantinue.market_data.models import Provenance, SecuritySnapshot
from quantinue.orchestration.policy import ScreeningConfig
from quantinue.roles.role_01_universe_screener.service import select_public_universe


def _snapshot(ticker: str, market_cap: int, price: str = "100") -> SecuritySnapshot:
    return SecuritySnapshot(
        ticker=ticker,
        name=f"{ticker} Inc",
        market_cap=Decimal(market_cap),
        last_price=Decimal(price),
        volume=0,  # NASDAQ screener omits volume; the filter uses candles instead.
        provenance=Provenance(
            source="nasdaq-screener",
            source_ref="https://example.test/screener",
            observed_at=datetime(2026, 7, 20, 13, 0, tzinfo=UTC),
            captured_at=datetime(2026, 7, 20, 13, 0, tzinfo=UTC),
            confidence=1.0,
            execution_id="run",
        ),
    )


def test_universe_is_ordered_by_market_cap_not_response_order() -> None:
    # Given: the source returns small caps first (the 1st-gen bug took this order)
    snapshots = (_snapshot("TINY", 1), _snapshot("HUGE", 900), _snapshot("MID", 50))
    config = ScreeningConfig(universe_size=3)

    selected = select_public_universe(snapshots, "HUGE", config)

    assert [item.ticker for item in selected] == ["HUGE", "MID", "TINY"]


def test_universe_keeps_configured_width() -> None:
    snapshots = tuple(_snapshot(f"T{index:04d}", 5000 - index) for index in range(2000))
    config = ScreeningConfig(universe_size=2000)

    selected = select_public_universe(snapshots, "T0000", config)

    assert len(selected) == 2000


def test_universe_truncates_to_configured_size_by_market_cap() -> None:
    snapshots = tuple(_snapshot(f"T{index:04d}", 1000 - index) for index in range(100))
    config = ScreeningConfig(universe_size=10)

    selected = select_public_universe(snapshots, "T0000", config)

    assert len(selected) == 10
    assert [item.ticker for item in selected] == [f"T{index:04d}" for index in range(10)]


def test_requested_ticker_is_retained_even_when_outside_the_cut() -> None:
    snapshots = tuple(_snapshot(f"T{index:04d}", 1000 - index) for index in range(100))
    config = ScreeningConfig(universe_size=5)

    selected = select_public_universe(snapshots, "T0099", config)

    assert len(selected) == 5
    assert "T0099" in {item.ticker for item in selected}


def test_zero_market_cap_rows_are_dropped() -> None:
    snapshots = (_snapshot("GOOD", 100), _snapshot("EMPTY", 0))
    config = ScreeningConfig(universe_size=10)

    selected = select_public_universe(snapshots, "GOOD", config)

    assert [item.ticker for item in selected] == ["GOOD"]


def test_unavailable_requested_ticker_is_rejected() -> None:
    with pytest.raises(ValidationFailureError):
        _ = select_public_universe((_snapshot("GOOD", 100),), "MISSING", ScreeningConfig())

