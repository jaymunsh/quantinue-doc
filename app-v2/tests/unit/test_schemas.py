"""Shared domain schema contract tests."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from quantinue.core.ontology import Decision, OrderStatus, SubmissionState
from quantinue.core.schemas import (
    AccountId,
    Entity,
    Event,
    Evidence,
    Judgment,
    Order,
    OrderId,
    OrderSubmission,
    Review,
    SignalId,
    SubmissionId,
)

NOW = datetime(2026, 7, 10, 14, tzinfo=UTC)


@pytest.mark.parametrize("ticker", ["nvda", "../NVDA", "NVDA\x00", "<B>", "삼성"])
def test_entity_rejects_noncanonical_ticker(ticker: str) -> None:
    with pytest.raises(ValidationError):
        _ = Entity(entity_id="entity-1", ticker=ticker)


def test_evidence_normalizes_time_and_tracks_lineage() -> None:
    # Given/When: evidence crosses the boundary with a non-UTC aware timestamp
    evidence = Evidence(
        evidence_id="ev-1",
        run_id="run-1",
        source="sec.gov",
        source_ref="filing-1",
        observed_at=datetime(2026, 7, 10, 23, tzinfo=timezone(timedelta(hours=9))),
        captured_at=NOW,
        confidence=0.9,
    )
    # Then: execution and source lineage are retained in UTC
    assert evidence.observed_at == NOW
    assert evidence.run_id == "run-1"


@pytest.mark.parametrize("confidence", [-0.001, 1.001])
def test_evidence_rejects_confidence_outside_unit_interval(confidence: float) -> None:
    # Given/When/Then: malformed confidence cannot enter the domain
    with pytest.raises(ValidationError):
        _ = Evidence(
            evidence_id="ev",
            run_id="run",
            source="x",
            source_ref="y",
            observed_at=NOW,
            captured_at=NOW,
            confidence=confidence,
        )


def test_evidence_rejects_observation_after_capture_boundary() -> None:
    # Given: deterministic capture and future observation timestamps
    captured_at = NOW
    observed_at = NOW + timedelta(microseconds=1)
    # When/Then: unavailable future evidence cannot enter a judgment
    with pytest.raises(ValidationError):
        _ = Evidence(
            evidence_id="ev",
            run_id="run",
            source="x",
            source_ref="y",
            observed_at=observed_at,
            captured_at=captured_at,
            confidence=0.5,
        )


def test_evidence_rejects_string_confidence_in_strict_mode() -> None:
    # Given/When/Then: coercible strings remain malformed boundary input
    with pytest.raises(ValidationError):
        _ = Evidence.model_validate(
            {
                "evidence_id": "ev",
                "run_id": "run",
                "source": "x",
                "source_ref": "y",
                "observed_at": NOW,
                "captured_at": NOW,
                "confidence": "0.5",
            }
        )


def test_event_requires_timezone_aware_occurrence() -> None:
    # Given/When/Then: ambiguous wall-clock time is rejected
    with pytest.raises(ValidationError):
        _ = Event(
            event_id="event",
            run_id="run",
            ticker="NVDA",
            event_type="earnings",
            occurred_at=datetime(2026, 7, 10, 14),  # noqa: DTZ001 - malformed fixture
            evidence_ids=("ev",),
        )


def test_judgment_order_and_review_form_traceable_chain() -> None:
    # Given: one evidence-backed event
    event = Event(
        event_id="event",
        run_id="run",
        ticker="NVDA",
        event_type="earnings",
        occurred_at=NOW,
        evidence_ids=("ev",),
    )
    # When: decision, order, and review are parsed
    judgment = Judgment(
        signal_id=SignalId(7),
        ticker="NVDA",
        cycle_ts=NOW,
        decision=Decision.PASS,
        confidence=0.8,
        evidence_ids=("ev",),
        event_ids=(event.event_id,),
    )
    order = Order(
        order_id=OrderId(11),
        signal_id=judgment.signal_id,
        account_id=AccountId(3),
        ticker="NVDA",
        quantity=1,
        entry_price=Decimal(100),
        stop_price=Decimal(95),
        take_profit_price=Decimal(110),
        status=OrderStatus.PLANNED,
        idempotency_key="paper:judgment",
    )
    review = Review(
        signal_id=judgment.signal_id,
        ret_1d=0.01,
        ret_3d=0.02,
        ret_5d=0.03,
        is_hit=True,
        max_drawdown=-0.01,
        lesson="Keep the evidence chain.",
    )
    # Then: lineage remains connected and ticker remains canonical
    assert (event.ticker, order.signal_id, review.signal_id) == ("NVDA", 7, 7)


def test_order_rejects_inverted_bracket() -> None:
    # Given/When/Then: buy bracket must satisfy stop < entry < take-profit
    with pytest.raises(ValidationError):
        _ = Order(
            order_id=OrderId(1),
            signal_id=SignalId(2),
            account_id=AccountId(3),
            ticker="NVDA",
            quantity=1,
            entry_price=Decimal(100),
            stop_price=Decimal(101),
            take_profit_price=Decimal(110),
            status=OrderStatus.PLANNED,
            idempotency_key="unique",
        )


def test_close_order_needs_no_bracket_legs_but_must_name_what_it_closes() -> None:
    """A close carries no stop/take-profit — filling them with dummies would lie."""
    # Given/When
    order = Order(
        order_id=OrderId(9),
        signal_id=SignalId(8),
        account_id=AccountId(3),
        ticker="NVDA",
        quantity=1,
        entry_price=Decimal(130),
        order_type="close",
        closes_order_id=OrderId(1),
        status=OrderStatus.PLANNED,
        idempotency_key="unique-close",
    )

    # Then
    assert order.stop_price is None
    assert order.take_profit_price is None
    assert order.closes_order_id == 1


def test_close_order_without_a_closed_order_is_rejected() -> None:
    """closes_order_id is the realized-P&L pair — a close without it is orphaned."""
    # Given/When/Then
    with pytest.raises(ValidationError):
        _ = Order(
            order_id=OrderId(9),
            signal_id=SignalId(8),
            account_id=AccountId(3),
            ticker="NVDA",
            quantity=1,
            entry_price=Decimal(130),
            order_type="close",
            status=OrderStatus.PLANNED,
            idempotency_key="unique-orphan-close",
        )


def test_bracket_order_still_requires_its_protective_legs() -> None:
    """Widening the model for closes must not let a buy through unprotected."""
    # Given/When/Then
    with pytest.raises(ValidationError):
        _ = Order(
            order_id=OrderId(1),
            signal_id=SignalId(2),
            account_id=AccountId(3),
            ticker="NVDA",
            quantity=1,
            entry_price=Decimal(100),
            status=OrderStatus.PLANNED,
            idempotency_key="unique-unprotected",
        )


def test_order_and_review_identity_fields_match_domain_ddl() -> None:
    # Given/When: canonical persistence identities are inspected
    order_identity = {"order_id", "signal_id", "account_id"}
    review_identity = {"signal_id"}
    # Then: invented or operational identities do not leak into domain models
    assert order_identity <= set(Order.model_fields)
    assert review_identity <= set(Review.model_fields)
    assert {"run_id", "judgment_id"}.isdisjoint(Order.model_fields)
    assert {"review_id", "run_id", "judgment_id", "reviewed_at"}.isdisjoint(Review.model_fields)


def test_order_submission_supports_pre_submit_claim_without_domain_ids() -> None:
    # Given/When: an owner reserves the broker client id before order creation
    claim = OrderSubmission(
        submission_id=SubmissionId(1),
        client_order_id="quantinue-claim-1",
        state=SubmissionState.CLAIMED,
        owner_token="owner-token",
        claimed_at=NOW,
        stale_after=NOW + timedelta(minutes=5),
        run_id=None,
        order_id=None,
        result_payload=None,
        last_error=None,
        created_at=NOW,
        updated_at=NOW,
    )
    # Then: nullable completion linkage preserves pre-submit ownership
    assert (claim.client_order_id, claim.run_id, claim.order_id) == (
        "quantinue-claim-1",
        None,
        None,
    )
