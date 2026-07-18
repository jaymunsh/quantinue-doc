from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Final

import anyio
import pytest
from pydantic import ValidationError

from quantinue.broker.mock import MockBroker
from quantinue.core.contracts import PipelineRequest
from quantinue.db.contracts import (
    AppOrderExposureReservationOutcome,
    AppOrderExposureStatus,
    DailyOrderReservation,
    parse_app_order_money,
)
from quantinue.db.memory import InMemoryRunStore
from quantinue.llm.provider import DeterministicAnalyzer
from quantinue.orchestration.factory import build_roles
from quantinue.orchestration.pipeline import PipelineOrchestrator
from quantinue.orchestration.policy import DEFAULT_PIPELINE_POLICY

DEFAULT_EXPOSURE_CAP: Final = Decimal("1000.00")
TRADE_DAY: Final = date(2026, 7, 13)
DEFAULT_ENTRY_PRICE: Final = Decimal("100.00")
DEFAULT_STOP_PRICE: Final = Decimal("85.00")
DEFAULT_TAKE_PROFIT_PRICE: Final = Decimal("120.00")


@dataclass(frozen=True, slots=True)
class _ReservationSpec:
    cap: int = 1
    entry_price: Decimal = DEFAULT_ENTRY_PRICE
    exposure_cap: Decimal = DEFAULT_EXPOSURE_CAP
    trade_date: date = TRADE_DAY
    account_id: int = 7
    ticker: str = "NVDA"
    idempotency_key: str | None = None


DEFAULT_RESERVATION_SPEC: Final = _ReservationSpec()


def _reservation(
    identity: int, spec: _ReservationSpec = DEFAULT_RESERVATION_SPEC
) -> DailyOrderReservation:
    return DailyOrderReservation(
        account_id=spec.account_id,
        trade_date=spec.trade_date,
        signal_id=identity,
        idempotency_key=spec.idempotency_key or f"q-a{spec.account_id}-s{identity}",
        ticker=spec.ticker,
        quantity=1,
        entry_price=spec.entry_price,
        stop_price=DEFAULT_STOP_PRICE,
        take_profit_price=DEFAULT_TAKE_PROFIT_PRICE,
        cap=spec.cap,
        max_app_order_exposure_usd=spec.exposure_cap,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("account_id", 0),
        ("account_id", -1),
        ("signal_id", 0),
        ("signal_id", -1),
        ("quantity", 0),
        ("quantity", -1),
        ("cap", 0),
        ("cap", -1),
    ],
)
async def test_memory_reservation_rejects_non_positive_identity_and_quantity_inputs(
    field_name: str, invalid_value: int
) -> None:
    # Given: an actual in-memory store with no existing order exposure.
    store = InMemoryRunStore()

    # When: a shared reservation is constructed with an invalid core value.
    with pytest.raises(ValueError, match=field_name):
        _ = replace(_reservation(1), **{field_name: invalid_value})

    # Then: the invalid object cannot bypass the in-memory reservation gate.
    summary = await store.app_order_exposure_summary(
        DEFAULT_RESERVATION_SPEC.account_id,
        DEFAULT_RESERVATION_SPEC.exposure_cap,
    )
    assert summary.planned_or_reserved == Decimal("0.00")


@pytest.mark.anyio
async def test_memory_daily_cap_allows_only_one_concurrent_new_identity() -> None:
    store = InMemoryRunStore()
    outcomes: list[bool] = []

    async def reserve(identity: int) -> None:
        result = await store.reserve_daily_new_order(_reservation(identity))
        outcomes.append(result.outcome is AppOrderExposureReservationOutcome.ACQUIRED)

    async with anyio.create_task_group() as group:
        _ = group.start_soon(reserve, 101)
        _ = group.start_soon(reserve, 102)

    assert sorted(outcomes) == [False, True]


@pytest.mark.anyio
async def test_memory_daily_cap_is_idempotent_for_same_order_identity() -> None:
    store = InMemoryRunStore()
    request = _reservation(101)

    first = await store.reserve_daily_new_order(request)
    replay = await store.reserve_daily_new_order(request)

    assert first.outcome is AppOrderExposureReservationOutcome.ACQUIRED
    assert replay.outcome is AppOrderExposureReservationOutcome.REPLAYED


@pytest.mark.anyio
async def test_memory_exposure_cap_allows_only_one_concurrent_600_dollar_order() -> None:
    store = InMemoryRunStore()
    outcomes: list[bool] = []

    async def reserve(identity: int) -> None:
        result = await store.reserve_daily_new_order(
            _reservation(
                identity,
                replace(DEFAULT_RESERVATION_SPEC, cap=2, entry_price=Decimal("600.00")),
            )
        )
        outcomes.append(result.outcome is AppOrderExposureReservationOutcome.ACQUIRED)

    async with anyio.create_task_group() as group:
        _ = group.start_soon(reserve, 201)
        _ = group.start_soon(reserve, 202)

    assert sorted(outcomes) == [False, True]


@pytest.mark.anyio
async def test_memory_exposure_cap_allows_exact_600_plus_400_boundary() -> None:
    store = InMemoryRunStore()

    first = await store.reserve_daily_new_order(
        _reservation(301, replace(DEFAULT_RESERVATION_SPEC, cap=3, entry_price=Decimal("600.00")))
    )
    second = await store.reserve_daily_new_order(
        _reservation(302, replace(DEFAULT_RESERVATION_SPEC, cap=3, entry_price=Decimal("400.00")))
    )

    assert first.outcome is AppOrderExposureReservationOutcome.ACQUIRED
    assert second.outcome is AppOrderExposureReservationOutcome.ACQUIRED
    assert second.summary.planned_or_reserved == Decimal("1000.00")
    assert second.summary.remaining == Decimal("0.00")


@pytest.mark.anyio
async def test_memory_exposure_cap_uses_exact_decimal_cent_boundaries() -> None:
    store = InMemoryRunStore()

    first = await store.reserve_daily_new_order(
        _reservation(401, replace(DEFAULT_RESERVATION_SPEC, cap=4, entry_price=Decimal("999.99")))
    )
    final_cent = await store.reserve_daily_new_order(
        _reservation(402, replace(DEFAULT_RESERVATION_SPEC, cap=4, entry_price=Decimal("0.01")))
    )
    over_cap = await store.reserve_daily_new_order(
        _reservation(403, replace(DEFAULT_RESERVATION_SPEC, cap=4, entry_price=Decimal("0.01")))
    )

    assert first.outcome is AppOrderExposureReservationOutcome.ACQUIRED
    assert final_cent.outcome is AppOrderExposureReservationOutcome.ACQUIRED
    assert final_cent.summary.planned_or_reserved == Decimal("1000.00")
    assert over_cap.outcome is AppOrderExposureReservationOutcome.REJECTED
    assert over_cap.summary.remaining == Decimal("0.00")


@pytest.mark.anyio
async def test_memory_exposure_replay_does_not_double_count_reference_notional() -> None:
    store = InMemoryRunStore()
    request = _reservation(
        501,
        replace(DEFAULT_RESERVATION_SPEC, cap=3, entry_price=Decimal("600.00")),
    )

    first = await store.reserve_daily_new_order(request)
    replay = await store.reserve_daily_new_order(request)
    second = await store.reserve_daily_new_order(
        _reservation(502, replace(DEFAULT_RESERVATION_SPEC, cap=3, entry_price=Decimal("400.00")))
    )

    assert first.outcome is AppOrderExposureReservationOutcome.ACQUIRED
    assert replay.outcome is AppOrderExposureReservationOutcome.REPLAYED
    assert replay.summary.planned_or_reserved == Decimal("600.00")
    assert second.outcome is AppOrderExposureReservationOutcome.ACQUIRED
    assert second.summary.planned_or_reserved == Decimal("1000.00")


@pytest.mark.anyio
async def test_memory_exposure_cap_is_account_wide_across_trade_dates() -> None:
    store = InMemoryRunStore()

    first = await store.reserve_daily_new_order(
        _reservation(551, replace(DEFAULT_RESERVATION_SPEC, entry_price=Decimal("600.00")))
    )
    next_day = await store.reserve_daily_new_order(
        _reservation(
            552,
            replace(
                DEFAULT_RESERVATION_SPEC,
                entry_price=Decimal("500.00"),
                trade_date=date(2026, 7, 14),
            ),
        )
    )

    assert first.outcome is AppOrderExposureReservationOutcome.ACQUIRED
    assert next_day.outcome is AppOrderExposureReservationOutcome.REJECTED
    assert next_day.summary.planned_or_reserved == Decimal("600.00")


@pytest.mark.anyio
async def test_memory_exposure_summary_counts_only_eligible_lifecycle_states() -> None:
    store = InMemoryRunStore()
    requests = (
        _reservation(
            601, replace(DEFAULT_RESERVATION_SPEC, cap=5, exposure_cap=Decimal("2000.00"))
        ),
        _reservation(
            602,
            replace(
                DEFAULT_RESERVATION_SPEC,
                cap=5,
                entry_price=Decimal("200.00"),
                exposure_cap=Decimal("2000.00"),
            ),
        ),
        _reservation(
            603,
            replace(
                DEFAULT_RESERVATION_SPEC,
                cap=5,
                entry_price=Decimal("300.00"),
                exposure_cap=Decimal("2000.00"),
            ),
        ),
        _reservation(
            604,
            replace(
                DEFAULT_RESERVATION_SPEC,
                cap=5,
                entry_price=Decimal("400.00"),
                exposure_cap=Decimal("2000.00"),
            ),
        ),
        _reservation(
            605,
            replace(
                DEFAULT_RESERVATION_SPEC,
                cap=5,
                entry_price=Decimal("500.00"),
                exposure_cap=Decimal("2000.00"),
            ),
        ),
    )
    for request in requests:
        accepted = await store.reserve_daily_new_order(request)
        assert accepted.outcome is AppOrderExposureReservationOutcome.ACQUIRED

    _ = await store.reconcile_app_order_exposure(
        requests[1].idempotency_key,
        AppOrderExposureStatus.SUBMITTED,
    )
    _ = await store.reconcile_app_order_exposure(
        requests[2].idempotency_key,
        AppOrderExposureStatus.FILLED,
    )
    failed_summary = await store.reconcile_app_order_exposure(
        requests[3].idempotency_key,
        AppOrderExposureStatus.FAILED,
    )
    replayed_failed_summary = await store.reconcile_app_order_exposure(
        requests[3].idempotency_key,
        AppOrderExposureStatus.FAILED,
    )
    _ = await store.reconcile_app_order_exposure(
        requests[4].idempotency_key,
        AppOrderExposureStatus.CANCELED,
    )

    summary = await store.app_order_exposure_summary(account_id=7, cap=Decimal("1000.00"))

    assert summary.planned_or_reserved == Decimal("600.00")
    assert summary.remaining == Decimal("400.00")
    assert failed_summary == replayed_failed_summary


@pytest.mark.anyio
async def test_memory_failed_reservation_keeps_daily_attempt_but_releases_exposure() -> None:
    store = InMemoryRunStore()
    first_request = _reservation(701)
    first = await store.reserve_daily_new_order(first_request)
    _ = await store.reconcile_app_order_exposure(
        first_request.idempotency_key,
        AppOrderExposureStatus.FAILED,
    )
    retry_as_new_identity = await store.reserve_daily_new_order(_reservation(702))

    assert first.outcome is AppOrderExposureReservationOutcome.ACQUIRED
    assert retry_as_new_identity.outcome is AppOrderExposureReservationOutcome.REJECTED
    assert retry_as_new_identity.summary.planned_or_reserved == Decimal("0.00")


@pytest.mark.anyio
async def test_memory_terminal_exposure_status_cannot_be_released_by_stale_failure() -> None:
    store = InMemoryRunStore()
    request = _reservation(801, replace(DEFAULT_RESERVATION_SPEC, entry_price=Decimal("500.00")))
    _ = await store.reserve_daily_new_order(request)

    filled = await store.reconcile_app_order_exposure(
        request.idempotency_key,
        AppOrderExposureStatus.FILLED,
    )
    stale_failure = await store.reconcile_app_order_exposure(
        request.idempotency_key,
        AppOrderExposureStatus.FAILED,
    )

    assert filled is not None
    assert stale_failure is not None
    assert stale_failure == filled
    assert stale_failure.planned_or_reserved == Decimal("500.00")


@pytest.mark.parametrize("price", [Decimal("100.001"), Decimal("NaN"), Decimal("Infinity")])
def test_memory_reservation_rejects_non_cent_or_nonfinite_money(price: Decimal) -> None:
    with pytest.raises(ValueError, match="must be"):
        _ = _reservation(901, replace(DEFAULT_RESERVATION_SPEC, entry_price=price))


@pytest.mark.parametrize("price", [100.001, float("nan"), float("inf")])
def test_role09_money_parser_rejects_non_cent_or_nonfinite_float(price: float) -> None:
    with pytest.raises(ValidationError):
        _ = parse_app_order_money(price)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("account_id", "ticker", "entry_price"),
    [
        (8, "NVDA", Decimal("100.00")),
        (7, "MSFT", Decimal("100.00")),
        (7, "NVDA", Decimal("101.00")),
    ],
)
async def test_memory_rejects_idempotency_key_replay_with_different_immutable_request(
    account_id: int,
    ticker: str,
    entry_price: Decimal,
) -> None:
    store = InMemoryRunStore()
    key = "q-immutable-replay"
    first = await store.reserve_daily_new_order(
        _reservation(1001, replace(DEFAULT_RESERVATION_SPEC, idempotency_key=key))
    )
    collision = await store.reserve_daily_new_order(
        _reservation(
            1001,
            replace(
                DEFAULT_RESERVATION_SPEC,
                account_id=account_id,
                ticker=ticker,
                idempotency_key=key,
                entry_price=entry_price,
            ),
        )
    )

    assert first.outcome is AppOrderExposureReservationOutcome.ACQUIRED
    assert collision.outcome is AppOrderExposureReservationOutcome.REJECTED
    assert collision.summary.account_id == account_id
    expected_exposure = Decimal("100.00") if account_id == 7 else Decimal("0.00")
    assert collision.summary.planned_or_reserved == expected_exposure


@pytest.mark.anyio
async def test_role09_uses_injected_daily_cap_before_role10_submission() -> None:
    store = InMemoryRunStore()
    roles = build_roles(
        DeterministicAnalyzer(),
        MockBroker(),
        store=store,
        policy=DEFAULT_PIPELINE_POLICY.model_copy(
            update={
                "daily_new_order_cap": 1,
                "max_app_order_exposure_usd": Decimal("10000.00"),
            }
        ),
    )
    orchestrator = PipelineOrchestrator(roles[:10], store)
    cycle = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)

    first = await orchestrator.run(PipelineRequest(ticker="NVDA", cycle_ts=cycle))
    capped = await orchestrator.run(
        PipelineRequest(ticker="NVDA", cycle_ts=cycle + timedelta(minutes=1))
    )

    assert first.order is not None
    assert capped.order is None
    assert capped.stages[8].summary.startswith("수량 0")
