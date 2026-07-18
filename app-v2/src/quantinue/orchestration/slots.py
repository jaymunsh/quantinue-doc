"""Deterministic slot quantization for idempotent pipeline cycles."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def slot_of(now: datetime, period_minutes: int) -> datetime:
    """Floor a tz-aware moment to its UTC period boundary.

    Every moment inside one period maps to the same slot, so cycle keys
    derived from the slot collapse duplicate manual/automatic triggers.
    """
    if now.tzinfo is None:
        msg = "now must include a timezone"
        raise ValueError(msg)
    if period_minutes <= 0:
        msg = "period_minutes must be a positive period"
        raise ValueError(msg)
    normalized = now.astimezone(UTC)
    midnight = normalized.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = int((normalized - midnight).total_seconds() // 60)
    return midnight + timedelta(minutes=elapsed - elapsed % period_minutes)
