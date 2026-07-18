from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TypedDict

import pytest
from pydantic import ValidationError

from quantinue.roles.role_11_reviewer.contracts import ReviewInput


class SignalPayload(TypedDict, total=False):
    run_id: str
    signal_id: int
    side: str
    trade_date: date
    decided_at: datetime
    evidence_ids: tuple[str, ...]
    not_applicable: tuple[dict[str, str], ...]
    decision_close: Decimal
    filled_price: Decimal


class SnapshotPayload(TypedDict, total=False):
    run_id: str
    evidence_id: str
    parent_evidence_ids: tuple[str, ...]
    day_offset: int
    price_date: date
    close: Decimal
    observed_at: datetime
    captured_at: datetime


class Payload(TypedDict):
    signal: SignalPayload
    snapshots: tuple[SnapshotPayload, ...]


Mutation = Callable[[Payload], None]


def valid_payload() -> Payload:
    decided_at = datetime(2026, 7, 13, 19, 0, tzinfo=UTC)
    sessions = (
        (1, date(2026, 7, 14)),
        (2, date(2026, 7, 15)),
        (3, date(2026, 7, 16)),
        (4, date(2026, 7, 17)),
        (5, date(2026, 7, 20)),
    )
    return {
        "signal": {
            "run_id": "run-matrix",
            "signal_id": 41,
            "side": "hold",
            "trade_date": date(2026, 7, 13),
            "decided_at": decided_at,
            "evidence_ids": ("signal-parent",),
            "not_applicable": (
                {"dimension": "filled_price", "reason": "hold creates no broker order"},
            ),
            "decision_close": Decimal(100),
        },
        "snapshots": tuple(
            {
                "run_id": "run-matrix",
                "evidence_id": f"close-{offset}",
                "parent_evidence_ids": ("signal-parent",),
                "day_offset": offset,
                "price_date": session,
                "close": Decimal(100 + offset),
                "observed_at": datetime.combine(session, datetime.min.time(), tzinfo=UTC).replace(
                    hour=20
                ),
                "captured_at": datetime.combine(session, datetime.min.time(), tzinfo=UTC).replace(
                    hour=20, minute=5
                ),
            }
            for offset, session in sessions
        ),
    }


def signal(payload: Payload) -> SignalPayload:
    return payload["signal"]


def snapshots(payload: Payload) -> tuple[SnapshotPayload, ...]:
    return payload["snapshots"]


def missing(payload: Payload) -> None:
    del signal(payload)["run_id"]


def stale(payload: Payload) -> None:
    snapshots(payload)[0]["observed_at"] = datetime(2026, 7, 13, 19, 0, tzinfo=UTC)


def future(payload: Payload) -> None:
    snapshots(payload)[0]["captured_at"] = datetime(2026, 7, 14, 19, 59, tzinfo=UTC)


def cross_run(payload: Payload) -> None:
    snapshots(payload)[0]["run_id"] = "another-run"


def contradiction(payload: Payload) -> None:
    signal(payload)["filled_price"] = Decimal(101)


def missing_parent(payload: Payload) -> None:
    snapshots(payload)[0]["parent_evidence_ids"] = ("unknown-parent",)


PARAMETER_MATRIX: tuple[tuple[str, Mutation, str], ...] = (
    ("missing", missing, "run_id"),
    ("stale", stale, "stale_snapshot"),
    ("future", future, "future_evidence"),
    ("cross-run", cross_run, "cross_run_evidence"),
    ("contradiction", contradiction, "contradictory_hold_fill"),
    ("missing-parent", missing_parent, "missing_parent"),
)


def test_parameter_matrix_accepts_valid_and_preserves_na_reason() -> None:
    # Given
    payload = valid_payload()
    # When
    request = ReviewInput.model_validate(payload, strict=True)
    # Then
    not_applicable = request.signal.not_applicable
    assert len(not_applicable) == 1
    assert not_applicable[0].dimension == "filled_price"
    assert not_applicable[0].reason == "hold creates no broker order"


@pytest.mark.parametrize(("case", "mutate", "error"), PARAMETER_MATRIX, ids=str)
def test_parameter_matrix_rejects_invalid_states(case: str, mutate: Mutation, error: str) -> None:
    # Given
    _ = case
    payload = valid_payload()
    mutate(payload)
    # When / Then
    with pytest.raises(ValidationError, match=error):
        _ = ReviewInput.model_validate(payload, strict=True)
