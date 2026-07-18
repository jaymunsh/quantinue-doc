"""Manual API triggers must share the automatic slot identity."""

from datetime import UTC, datetime

import pytest

import quantinue.main as main_module
from quantinue.main import _pipeline_request


def test_pipeline_request_quantizes_cycle_to_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FrozenDatetime:
        @staticmethod
        def now(tz: object) -> datetime:
            del tz
            return datetime(2026, 7, 20, 13, 47, 23, tzinfo=UTC)

    monkeypatch.setattr(main_module, "datetime", _FrozenDatetime)

    request = _pipeline_request("NVDA", slot_minutes=30)

    assert request.cycle_ts == datetime(2026, 7, 20, 13, 30, tzinfo=UTC)


def test_two_calls_inside_one_slot_share_cycle_ts(monkeypatch: pytest.MonkeyPatch) -> None:
    moments = iter(
        [
            datetime(2026, 7, 20, 13, 31, tzinfo=UTC),
            datetime(2026, 7, 20, 13, 58, tzinfo=UTC),
        ]
    )

    class _SteppingDatetime:
        @staticmethod
        def now(tz: object) -> datetime:
            del tz
            return next(moments)

    monkeypatch.setattr(main_module, "datetime", _SteppingDatetime)

    first = _pipeline_request("NVDA", slot_minutes=30)
    second = _pipeline_request("NVDA", slot_minutes=30)

    assert first.cycle_ts == second.cycle_ts  # → deterministic_run_key 동일 → claim dedup
