from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

import pytest
from pydantic import SecretStr

import quantinue.orchestration.factory as factory_module
from quantinue.broker.contracts import OrderPlan
from quantinue.broker.reservations import OrderReservations
from quantinue.core.config import BrokerMode, DatabaseMode, Settings
from quantinue.core.contracts import OrderResult
from quantinue.db.postgres import PostgresRunStore


@dataclass(frozen=True, slots=True)
class _RiskControls:
    daily_new_order_cap: int
    max_app_order_exposure_usd: Decimal


class _CapturingRiskPortfolio:
    controls: ClassVar[list[_RiskControls]] = []

    def __init__(self, **controls: Decimal | float) -> None:
        daily_new_order_cap = controls["daily_new_order_cap"]
        max_app_order_exposure_usd = controls["max_app_order_exposure_usd"]
        assert isinstance(daily_new_order_cap, int)
        assert isinstance(max_app_order_exposure_usd, Decimal)
        self.controls.append(
            _RiskControls(
                daily_new_order_cap=daily_new_order_cap,
                max_app_order_exposure_usd=max_app_order_exposure_usd,
            )
        )


class _CapturingBroker:
    reservations: ClassVar[list[OrderReservations]] = []

    def __init__(self, settings: Settings, *, reservations: OrderReservations) -> None:
        del settings
        self.reservations.append(reservations)

    async def submit(self, plan: OrderPlan) -> OrderResult:
        raise AssertionError(plan)


def test_postgres_alpaca_factory_injects_owned_durable_reservations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _CapturingBroker.reservations.clear()
    monkeypatch.setattr(factory_module, "AlpacaBroker", _CapturingBroker)
    settings = Settings(
        database_mode=DatabaseMode.POSTGRES,
        broker_mode=BrokerMode.ALPACA,
        alpaca_api_key=SecretStr("test-key"),
        alpaca_secret_key=SecretStr("test-secret"),
    )

    _, store = factory_module.build_configured_orchestrator(settings)

    assert isinstance(store, PostgresRunStore)
    assert _CapturingBroker.reservations == [store.order_reservations]


def test_configured_factory_injects_first_cycle_order_controls_into_role_09(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    _CapturingRiskPortfolio.controls.clear()
    monkeypatch.setattr(factory_module, "RiskPortfolio", _CapturingRiskPortfolio)
    settings = Settings(
        daily_new_order_cap=1,
        max_app_order_exposure_usd=Decimal("875.55"),
    )

    # When
    _ = factory_module.build_configured_orchestrator(settings)

    # Then
    assert _CapturingRiskPortfolio.controls == [_RiskControls(1, Decimal("875.55"))]
