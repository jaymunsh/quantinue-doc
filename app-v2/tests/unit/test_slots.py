"""Slot quantization: any moment inside a period maps to one deterministic slot."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from quantinue.orchestration.slots import slot_of


def test_moments_inside_same_period_share_one_slot() -> None:
    base = datetime(2026, 7, 20, 13, 30, tzinfo=UTC)
    assert slot_of(base + timedelta(minutes=0), 30) == base
    assert slot_of(base + timedelta(minutes=17, seconds=42), 30) == base
    assert slot_of(base + timedelta(minutes=29, seconds=59), 30) == base


def test_boundary_maps_to_itself() -> None:
    boundary = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)
    assert slot_of(boundary, 30) == boundary


def test_slot_is_floored_from_utc_midnight() -> None:
    # 50-minute period: floor(13h30m = 810m, 50) = 800m → 13:20 UTC.
    now = datetime(2026, 7, 20, 13, 30, tzinfo=UTC)
    assert slot_of(now, 50) == datetime(2026, 7, 20, 13, 20, tzinfo=UTC)


def test_non_utc_input_is_normalized_to_utc() -> None:
    kst = timezone(timedelta(hours=9))
    now_kst = datetime(2026, 7, 20, 22, 47, tzinfo=kst)  # = 13:47 UTC
    assert slot_of(now_kst, 30) == datetime(2026, 7, 20, 13, 30, tzinfo=UTC)


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        slot_of(datetime(2026, 7, 20, 13, 30), 30)  # noqa: DTZ001


def test_nonpositive_period_is_rejected() -> None:
    with pytest.raises(ValueError, match="period"):
        slot_of(datetime(2026, 7, 20, tzinfo=UTC), 0)
