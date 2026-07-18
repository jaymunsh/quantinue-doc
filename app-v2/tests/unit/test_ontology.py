"""Canonical ontology contract tests."""

from typing import get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from quantinue.core.ontology import (
    EVENT_TYPES,
    Bucket,
    Decision,
    EventType,
    Permission,
    ReviewSource,
    Side,
    StageAttemptState,
)


def test_event_type_contains_exactly_twelve_design_values() -> None:
    # Given: the human contract's fixed event vocabulary
    expected = {
        "earnings",
        "guidance_change",
        "ma",
        "capital_raise",
        "buyback",
        "management_change",
        "insider_trade",
        "product_deal",
        "analyst_rating",
        "regulation_legal",
        "delisting_halt",
        "other",
    }
    # When: the canonical Literal is inspected
    actual = set(get_args(EventType))
    # Then: code and the published vocabulary are identical
    assert actual == expected == set(EVENT_TYPES)


def test_event_type_rejects_unregistered_alias() -> None:
    # Given: an old alias that the design normalizes before this boundary
    adapter: TypeAdapter[EventType] = TypeAdapter(EventType)
    # When/Then: canonical parsing rejects it
    with pytest.raises(ValidationError):
        _ = adapter.validate_python("repurchase")


def test_logical_enums_use_contract_values() -> None:
    # Given/When: fixed vocabularies are loaded
    # Then: persisted values remain lower-case snake-case TEXT
    assert Side.BUY.value == "buy"
    assert Decision.PASS.value == "pass"
    assert Permission.TRADE_ELIGIBLE.value == "trade_eligible"
    assert Bucket.SQUEEZE_BREAKOUT.value == "squeeze_breakout"
    assert {item.value for item in ReviewSource} == {"fixture", "market_data"}
    assert {item.value for item in StageAttemptState} == {
        "pending",
        "running",
        "retrying",
        "completed",
        "failed",
        "timed_out",
    }


def test_strategist_side_rejects_phase_two_sell() -> None:
    # Given: the MVP strategist vocabulary
    allowed_sides = {side.value for side in Side}

    # When / Then: a phase-two sell recommendation reaches the boundary
    assert allowed_sides == {"buy", "hold"}
    with pytest.raises(ValueError, match="'sell' is not a valid Side"):
        _ = Side("sell")
